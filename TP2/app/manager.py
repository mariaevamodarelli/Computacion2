import asyncio
import logging
import json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from time import perf_counter

import httpx

from app.models import Estado, Solicitud, Tarea, ahora, Hashear
from app.tasks import ejecutar

logger = logging.getLogger(__name__)


class ColaLlena(Exception):
    pass


class Gestor:
    def __init__(self, workers: int, capacidad: int, procesos: int,
                 directorio_estado="datos", espera_apagado=10):
        self.cola: asyncio.Queue = asyncio.Queue(maxsize=capacidad)
        self.tareas: dict[str, Tarea] = {}
        self.cantidad_workers = workers
        self.procesos = procesos
        self.workers: list[asyncio.Task] = []
        self.aceptando = False
        self.activas: dict[str, asyncio.Task] = {}
        self.directorio_estado = Path(directorio_estado)
        self.espera_apagado = espera_apagado

    async def iniciar(self):
        self.directorio_estado.mkdir(parents=True, exist_ok=True)
        previos = sorted(self.directorio_estado.glob('estado_*.json'))
        if previos:
            logger.warning('Hay un volcado previo: %s (no se restaura)', previos[-1])
        self.compartido = get_context('spawn').Manager()
        self.client = httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=40),
        )
        # spawn evita depender del método de arranque por defecto del sistema.
        self.executor = ProcessPoolExecutor(
            max_workers=self.procesos, mp_context=get_context('spawn')
        )
        self.workers = [
            asyncio.create_task(self.worker(), name=f'worker-{n}')
            for n in range(self.cantidad_workers)
        ]
        self.aceptando = True

    def encolar(self, solicitud: Solicitud) -> Tarea:
        tarea = Tarea(tipo=solicitud.tipo, timeout=solicitud.timeout)
        try:
            self.cola.put_nowait((tarea.id, solicitud))
        except asyncio.QueueFull as exc:
            raise ColaLlena from exc
        # No hay await entre insertar en la cola y registrar el estado.
        self.tareas[tarea.id] = tarea
        return tarea

    async def cancelar(self, identificador: str) -> Tarea:
        tarea = self.tareas[identificador]
        if tarea.estado == Estado.PENDIENTE:
            tarea.estado = Estado.CANCELADA
            tarea.terminada = ahora()
        elif tarea.estado == Estado.EJECUTANDO:
            activa = self.activas.get(identificador)
            if activa is not None:
                if not activa.cancelling():
                    activa.cancel()
                await asyncio.gather(activa, return_exceptions=True)
                tarea.estado = Estado.CANCELADA
                tarea.terminada = ahora()
        return tarea

    async def worker(self):
        while True:
            identificador, solicitud = await self.cola.get()
            tarea = self.tareas[identificador]
            if tarea.estado == Estado.CANCELADA:
                self.cola.task_done()
                continue
            inicio = perf_counter()
            tarea.estado = Estado.EJECUTANDO
            tarea.iniciada = ahora()
            try:
                detener = (self.compartido.Event()
                           if isinstance(solicitud, Hashear) else None)
                activa = asyncio.create_task(ejecutar(
                    solicitud, self.client, self.executor, detener
                ))
                self.activas[identificador] = activa
                try:
                    tarea.resultado = await asyncio.wait_for(
                        activa, timeout=solicitud.timeout
                    )
                    tarea.estado = Estado.COMPLETADA
                except TimeoutError:
                    tarea.estado = Estado.FALLIDA
                    tarea.error = f'Timeout de {solicitud.timeout} segundos'
                except asyncio.CancelledError:
                    tarea.estado = Estado.CANCELADA
            except Exception as exc:
                logger.exception('Falló la tarea %s', identificador)
                tarea.estado = Estado.FALLIDA
                tarea.error = str(exc) or type(exc).__name__
            finally:
                self.activas.pop(identificador, None)
                tarea.terminada = ahora()
                tarea.duracion_segundos = perf_counter() - inicio
                self.cola.task_done()

    async def cerrar(self):
        self.aceptando = False
        # Solo se concede tiempo de gracia a las tareas que ya comenzaron.
        for tarea in self.tareas.values():
            if tarea.estado == Estado.PENDIENTE:
                tarea.estado = Estado.CANCELADA
                tarea.terminada = ahora()
        try:
            await asyncio.wait_for(self.cola.join(), self.espera_apagado)
        except TimeoutError:
            for activa in list(self.activas.values()):
                if not activa.cancelling():
                    activa.cancel()
            await self.cola.join()
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        await self.client.aclose()
        await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=True)
        await asyncio.to_thread(self.compartido.shutdown)
        await asyncio.to_thread(self.volcar)

    def volcar(self):
        nombre = f"estado_{ahora().strftime('%Y%m%dT%H%M%S_%fZ')}.json"
        destino = self.directorio_estado / nombre
        temporal = destino.with_suffix('.tmp')
        temporal.write_text(json.dumps(
            [tarea.model_dump(mode='json') for tarea in self.tareas.values()],
            indent=2, ensure_ascii=False,
        ), encoding='utf-8')
        temporal.replace(destino)
        logger.warning('Estado guardado en %s', destino)

    def estadisticas(self) -> dict:
        totales = {estado.value: 0 for estado in Estado}
        duraciones = []
        for tarea in self.tareas.values():
            totales[tarea.estado.value] += 1
            if tarea.duracion_segundos is not None:
                duraciones.append(tarea.duracion_segundos)
        return {
            'total': len(self.tareas),
            'por_estado': totales,
            'workers_totales': self.cantidad_workers,
            'workers_ocupados': totales[Estado.EJECUTANDO.value],
            'en_cola': self.cola.qsize(),
            'promedio_segundos': (
                sum(duraciones) / len(duraciones) if duraciones else None
            ),
        }
