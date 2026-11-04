import asyncio
import hashlib
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

import httpx

from app.main import app
from app.manager import ColaLlena, Gestor
from app.models import Descargar, Esperar, Estado, Hashear
from app.tasks import ejecutar


class SistemaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.gestor = Gestor(workers=2, capacidad=2, procesos=1,
                             directorio_estado=self.temporal.name, espera_apagado=1)
        await self.gestor.iniciar()

    async def asyncTearDown(self):
        await self.gestor.cerrar()
        self.temporal.cleanup()

    async def drenar(self):
        await asyncio.wait_for(self.gestor.cola.join(), timeout=15)

    async def test_post_inmediato_y_workers_concurrentes(self):
        app.state.gestor = self.gestor
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test'
        ) as client:
            respuesta = await client.post(
                '/tareas', json={'tipo': 'esperar', 'segundos': 0.1}
            )
            self.assertEqual(respuesta.status_code, 202)
            self.assertEqual(respuesta.json()['estado'], 'pendiente')
            segunda = self.gestor.encolar(Esperar(tipo='esperar', segundos=0.1))
            # Ceder el control permite a ambos workers empezar antes de terminar.
            await asyncio.sleep(0)
            primera = self.gestor.tareas[respuesta.json()['id']]
            self.assertEqual(primera.estado, Estado.EJECUTANDO)
            self.assertEqual(segunda.estado, Estado.EJECUTANDO)
            await self.drenar()
            consulta = await client.get(f'/tareas/{primera.id}')
            self.assertEqual(consulta.json()['estado'], 'completada')
            self.assertEqual((await client.get('/tareas/no-existe')).status_code, 404)
            self.assertEqual((await client.post(
                '/tareas', json={'tipo': 'esperar', 'segundos': -1}
            )).status_code, 422)
            estadisticas = (await client.get('/estadisticas')).json()
            self.assertEqual(estadisticas['por_estado']['completada'], 2)
            self.assertEqual(estadisticas['workers_ocupados'], 0)

    async def test_cola_llena_no_registra_tarea_rechazada(self):
        for _ in range(2):
            self.gestor.encolar(Esperar(tipo='esperar', segundos=0))
        with self.assertRaises(ColaLlena):
            self.gestor.encolar(Esperar(tipo='esperar', segundos=0))
        self.assertEqual(len(self.gestor.tareas), 2)
        await self.drenar()

    async def test_error_no_mata_worker(self):
        async def falla(*args):
            raise RuntimeError('fallo de prueba')

        with patch('app.manager.ejecutar', side_effect=falla):
            tarea = self.gestor.encolar(Esperar(tipo='esperar', segundos=0))
            with self.assertLogs('app.manager', level='ERROR'):
                await self.drenar()
        self.assertEqual(tarea.estado, Estado.FALLIDA)
        self.assertEqual(tarea.error, 'fallo de prueba')
        siguiente = self.gestor.encolar(Esperar(tipo='esperar', segundos=0))
        await self.drenar()
        self.assertEqual(siguiente.estado, Estado.COMPLETADA)
        self.assertTrue(all(not worker.done() for worker in self.gestor.workers))

    async def test_hash_en_proceso(self):
        tarea = self.gestor.encolar(Hashear(
            tipo='hashear', texto='hola', iteraciones=2
        ))
        await self.drenar()
        esperado = hashlib.sha256(hashlib.sha256(b'hola').digest()).hexdigest()
        self.assertEqual(tarea.estado, Estado.COMPLETADA)
        self.assertEqual(tarea.resultado['hash'], esperado)

    async def test_descargas_concurrentes_y_error_individual(self):
        activas = 0
        max_activas = 0

        async def responder(request):
            nonlocal activas, max_activas
            activas += 1
            max_activas = max(max_activas, activas)
            await asyncio.sleep(0.01)
            activas -= 1
            if request.url.path == '/error':
                raise httpx.ConnectError('error simulado', request=request)
            return httpx.Response(200, content=b'abc')

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(responder)
        ) as client:
            resultado = await ejecutar(Descargar(
                tipo='descargar',
                urls=['https://test.local/a', 'https://test.local/error'],
            ), client, self.gestor.executor)
        self.assertEqual(max_activas, 2)
        self.assertEqual(resultado[0]['tamano_bytes'], 3)
        self.assertEqual(resultado[0]['codigo_estado'], 200)
        self.assertIn('error simulado', resultado[1]['error'])

    async def test_cancelar_pendiente_y_ejecutando_por_api(self):
        app.state.gestor = self.gestor
        pendiente = self.gestor.encolar(Esperar(tipo='esperar', segundos=10))
        await self.gestor.cancelar(pendiente.id)
        self.assertEqual(pendiente.estado, Estado.CANCELADA)
        self.assertIsNone(pendiente.iniciada)
        activa = self.gestor.encolar(Esperar(tipo='esperar', segundos=10))
        await asyncio.sleep(0.01)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test'
        ) as client:
            respuesta = await client.delete('/tareas/' + activa.id)
            self.assertEqual(respuesta.json()['estado'], 'cancelada')
            self.assertEqual((await client.delete('/tareas/no-existe')).status_code, 404)
        await self.drenar()
        self.assertFalse(self.gestor.activas)
        self.assertTrue(all(not worker.done() for worker in self.gestor.workers))

    async def test_timeout_y_worker_reutilizable(self):
        tarea = self.gestor.encolar(Esperar(
            tipo='esperar', segundos=10, timeout=0.03
        ))
        await self.drenar()
        self.assertEqual(tarea.estado, Estado.FALLIDA)
        self.assertIn('Timeout', tarea.error)
        siguiente = self.gestor.encolar(Esperar(tipo='esperar', segundos=0))
        await self.drenar()
        self.assertEqual(siguiente.estado, Estado.COMPLETADA)

    async def test_cancelar_hash_libera_proceso(self):
        tarea = self.gestor.encolar(Hashear(
            tipo='hashear', texto='hola', iteraciones=10_000_000
        ))
        await asyncio.sleep(0.15)
        await asyncio.wait_for(self.gestor.cancelar(tarea.id), 5)
        await self.drenar()
        self.assertEqual(tarea.estado, Estado.CANCELADA)
        siguiente = self.gestor.encolar(Hashear(
            tipo='hashear', texto='hola', iteraciones=2
        ))
        await self.drenar()
        self.assertEqual(siguiente.estado, Estado.COMPLETADA)

    async def test_apagado_cancela_y_vuelca_json(self):
        self.gestor.espera_apagado = 0.03
        tarea = self.gestor.encolar(Esperar(tipo='esperar', segundos=10))
        await asyncio.sleep(0.01)
        await asyncio.wait_for(self.gestor.cerrar(), 5)
        archivos = list(Path(self.temporal.name).glob('estado_*.json'))
        self.assertEqual(len(archivos), 1)
        datos = json.loads(archivos[0].read_text())
        self.assertEqual(datos[0]['estado'], 'cancelada')
        self.assertEqual(datos[0]['id'], tarea.id)
        self.assertTrue(all(worker.done() for worker in self.gestor.workers))
        # Reiniciar comprueba la detección del volcado y permite el tearDown.
        with self.assertLogs('app.manager', level='WARNING') as logs:
            await self.gestor.iniciar()
        self.assertIn('volcado previo', ' '.join(logs.output))

    async def test_timeout_hash_libera_proceso(self):
        tarea = self.gestor.encolar(Hashear(
            tipo='hashear', texto='hola', iteraciones=10_000_000, timeout=0.05
        ))
        await self.drenar()
        self.assertEqual(tarea.estado, Estado.FALLIDA)
        self.assertIn('Timeout', tarea.error)
        self.assertFalse(self.gestor.activas)

    async def test_cancelar_descarga_cierra_stream(self):
        iniciada = asyncio.Event()
        cerrada = asyncio.Event()

        class Flujo(httpx.AsyncByteStream):
            async def __aiter__(self):
                iniciada.set()
                await asyncio.sleep(10)
                yield b'datos'

            async def aclose(self):
                cerrada.set()

        async def responder(request):
            return httpx.Response(200, stream=Flujo())

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(responder)
        ) as client:
            tarea = asyncio.create_task(ejecutar(Descargar(
                tipo='descargar', urls=['https://test.local/archivo']
            ), client, self.gestor.executor))
            await asyncio.wait_for(iniciada.wait(), 2)
            tarea.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await tarea
            self.assertTrue(cerrada.is_set())

    async def test_worker_cancelado_se_reemplaza(self):
        anterior = self.gestor.workers[0]
        with self.assertLogs('app.manager', level='ERROR'):
            anterior.cancel()
            await asyncio.sleep(0.02)
        self.assertIsNot(self.gestor.workers[0], anterior)
        self.assertFalse(self.gestor.workers[0].done())
        tarea = self.gestor.encolar(Esperar(tipo='esperar', segundos=0))
        await self.drenar()
        self.assertEqual(tarea.estado, Estado.COMPLETADA)

    async def test_hash_en_bloques_conserva_resultado(self):
        tarea = self.gestor.encolar(Hashear(
            tipo='hashear', texto='cadena', iteraciones=50_001
        ))
        await self.drenar()
        valor = b'cadena'
        for _ in range(50_001):
            valor = hashlib.sha256(valor).digest()
        self.assertEqual(tarea.resultado['hash'], valor.hex())


if __name__ == '__main__':
    unittest.main()
