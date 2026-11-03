import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from time import perf_counter

import httpx

from app.models import Estado, Solicitud, Tarea, ahora
from app.tasks import ejecutar

logger = logging.getLogger(__name__)


class ColaLlena(Exception):
    pass


class Gestor:
    def __init__(self, workers: int, capacidad: int, procesos: int):
        self.cola: asyncio.Queue = asyncio.Queue(maxsize=capacidad)
        self.tareas: dict[str, Tarea] = {}
        self.cantidad_workers = workers
        self.procesos = procesos
        self.workers: list[asyncio.Task] = []
        self.aceptando = False

    async def iniciar(self):
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
        tarea = Tarea(tipo=solicitud.tipo)
        try:
            self.cola.put_nowait((tarea.id, solicitud))
        except asyncio.QueueFull as exc:
            raise ColaLlena from exc
        # No hay await entre insertar en la cola y registrar el estado.
        self.tareas[tarea.id] = tarea
        return tarea

    async def worker(self):
        while True:
            identificador, solicitud = await self.cola.get()
            tarea = self.tareas[identificador]
            inicio = perf_counter()
            tarea.estado = Estado.EJECUTANDO
            tarea.iniciada = ahora()
            try:
                tarea.resultado = await ejecutar(
                    solicitud, self.client, self.executor
                )
                tarea.estado = Estado.COMPLETADA
            except Exception as exc:
                logger.exception('Falló la tarea %s', identificador)
                tarea.estado = Estado.FALLIDA
                tarea.error = str(exc) or type(exc).__name__
            finally:
                tarea.terminada = ahora()
                tarea.duracion_segundos = perf_counter() - inicio
                self.cola.task_done()

    async def cerrar(self):
        self.aceptando = False
        # Primer avance: drenar toda la cola. El límite de apagado y el volcado
        # a JSON se incorporarán junto con cancelación y timeouts.
        await self.cola.join()
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        await self.client.aclose()
        await asyncio.to_thread(self.executor.shutdown, wait=True)

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
