"""Ejecutar desde la raíz: python -m benchmarks.medir

Arranca dos servidores temporales en puertos libres. No usa la API de :8000,
ni Internet, ni Git. Guarda JSON con muestras, versiones y configuración.
"""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter

import fastapi
import httpx
import pydantic

from app.models import Descargar
from app.tasks import descargar_url, ejecutar


@contextmanager
def servidor(directa=False):
    with tempfile.TemporaryDirectory() as temporal:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            puerto = sock.getsockname()[1]
        marcador = Path(temporal) / 'iniciado'
        entorno = dict(os.environ, CPU_DIRECTA=str(int(directa)),
                       MARCADOR=str(marcador), DIRECTORIO_ESTADO=temporal,
                       WORKERS='4', CAPACIDAD_COLA='100', PROCESOS_CPU='2',
                       PYTHONDONTWRITEBYTECODE='1')
        with (Path(temporal) / 'servidor.log').open('w+') as log:
            proceso = subprocess.Popen([
                sys.executable, '-B', '-m', 'uvicorn', 'benchmarks.servidor:app',
                '--host', '127.0.0.1', '--port', str(puerto), '--log-level', 'warning',
            ], env=entorno, stdout=log, stderr=log)
            try:
                yield f'http://127.0.0.1:{puerto}', marcador
            finally:
                proceso.terminate()
                try:
                    proceso.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proceso.kill()
                    proceso.wait()
                    raise RuntimeError('El servidor no cerró dentro de 15 segundos')
                log.seek(0)
                texto = log.read()
                if 'Application startup failed' in texto:
                    raise RuntimeError(texto)


async def disponible(client):
    for _ in range(100):
        try:
            respuesta = await client.get('/estadisticas')
            respuesta.raise_for_status()
            return
        except httpx.TransportError:
            await asyncio.sleep(0.05)
    raise RuntimeError('No arrancó el servidor de mediciones')


async def terminar(client, ids):
    limite = perf_counter() + 90
    while perf_counter() < limite:
        r = await client.get('/tareas')
        r.raise_for_status()
        tareas = [t for t in r.json() if t['id'] in ids]
        if len(tareas) == len(ids) and all(
            t['estado'] in ('completada', 'fallida', 'cancelada') for t in tareas
        ):
            return tareas
        await asyncio.sleep(0.01)
    raise TimeoutError('Las tareas no terminaron')


def verificar_descargas(resultados):
    assert len(resultados) == 20
    assert all(r['codigo_estado'] == 200 and r['tamano_bytes'] == 1024
               and r['error'] is None for r in resultados), resultados


async def medir_io(client, base):
    urls = [base + f'/fixture?n={n}' for n in range(20)]
    muestras = []
    for _ in range(3):
        inicio = perf_counter()
        resultados = [await descargar_url(client, url) for url in urls]
        secuencial = perf_counter() - inicio
        verificar_descargas(resultados)
        inicio = perf_counter()
        resultados = await ejecutar(Descargar(tipo='descargar', urls=urls), client, None)
        concurrente = perf_counter() - inicio
        verificar_descargas(resultados)
        muestras.append({'secuencial_s': secuencial, 'concurrente_s': concurrente,
                         'speedup': secuencial / concurrente})
    return muestras


async def medir_cpu(client, marcador):
    muestras = []
    # Calentar el pool antes de medir: arranque spawn separado del cálculo.
    r = await client.post('/tareas', json={
        'tipo': 'hashear', 'texto': 'calentamiento', 'iteraciones': 1,
    })
    r.raise_for_status()
    await terminar(client, {r.json()['id']})
    for _ in range(3):
        marcador.unlink(missing_ok=True)
        r = await client.post('/tareas', json={
            'tipo': 'hashear', 'texto': 'medicion', 'iteraciones': 3_000_000,
            'timeout': 60,
        })
        r.raise_for_status()
        identificador = r.json()['id']
        limite = perf_counter() + 10
        while not marcador.exists():
            if perf_counter() > limite:
                raise TimeoutError('No comenzó el hash')
            await asyncio.sleep(.001)
        inicio = perf_counter()
        consulta = await client.get('/tareas')
        latencia = perf_counter() - inicio
        consulta.raise_for_status()
        tarea = (await terminar(client, {identificador}))[0]
        assert tarea['estado'] == 'completada', tarea
        muestras.append({'latencia_get_s': latencia,
                         'duracion_hash_s': tarea['duracion_segundos']})
    return muestras


def percentil(valores, q):
    ordenados = sorted(valores)
    return ordenados[max(0, __import__('math').ceil(q * len(ordenados)) - 1)]


async def medir_pool(client):
    filas = []
    for cantidad in (1, 4, 8, 16, 32, 64, 128):
        inicio = perf_counter()
        respuestas = await asyncio.gather(*[
            client.post('/tareas', json={'tipo': 'esperar', 'segundos': .15})
            for _ in range(cantidad)
        ])
        assert all(r.status_code in (202, 503) for r in respuestas)
        ids = {r.json()['id'] for r in respuestas if r.status_code == 202}
        tareas = await terminar(client, ids)
        total = perf_counter() - inicio
        assert all(t['estado'] == 'completada' for t in tareas)
        latencias = [(datetime.fromisoformat(t['terminada']) -
                      datetime.fromisoformat(t['creada'])).total_seconds()
                     for t in tareas]
        filas.append({'enviadas': cantidad, 'aceptadas': len(ids),
                      'rechazadas': cantidad - len(ids), 'lote_s': total,
                      'latencia_mediana_s': statistics.median(latencias),
                      'latencia_p95_s': percentil(latencias, .95),
                      'tareas_por_s': len(ids) / total})
    return filas


async def principal():
    datos = {'fecha_utc': datetime.now(timezone.utc).isoformat(),
             'entorno': {'python': sys.version, 'plataforma': platform.platform(),
                         'cpus_visibles': os.cpu_count(), 'fastapi': fastapi.__version__,
                         'httpx': httpx.__version__, 'pydantic': pydantic.__version__},
             'configuracion': {'workers': 4, 'procesos_cpu': 2, 'capacidad_cola': 100,
                               'descargas': 20, 'fixture_demora_s': .1,
                               'fixture_bytes': 1024, 'iteraciones_hash': 3_000_000,
                               'espera_pool_s': .15, 'hash_bloque_iteraciones': 50_000}}
    with servidor() as (base, marcador):
        async with httpx.AsyncClient(base_url=base, timeout=60,
                                     limits=httpx.Limits(max_connections=150),
                                     trust_env=False) as client:
            await disponible(client)
            print('Midiendo 20 descargas secuenciales y concurrentes...', flush=True)
            datos['io'] = await medir_io(client, base)
            print('Midiendo latencia con executor...', flush=True)
            datos['cpu_executor'] = await medir_cpu(client, marcador)
            print('Midiendo saturación del pool...', flush=True)
            datos['pool'] = await medir_pool(client)
    with servidor(directa=True) as (base, marcador):
        async with httpx.AsyncClient(base_url=base, timeout=60, trust_env=False) as client:
            await disponible(client)
            print('Midiendo latencia con CPU directa (solo experimento)...', flush=True)
            datos['cpu_directa'] = await medir_cpu(client, marcador)
    destino = Path('mediciones/resultados.json')
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    from benchmarks.informe import generar
    generar()
    print(f'Resultados e informe actualizados: {destino}', flush=True)


if __name__ == '__main__':
    asyncio.run(principal())
