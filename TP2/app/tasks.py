import asyncio
import hashlib
from time import perf_counter

import httpx

from app.models import Descargar, Esperar, Hashear, Solicitud


def hash_iterado(texto: str, iteraciones: int, detener=None) -> str:
    """Función síncrona de nivel de módulo: se puede enviar a otro proceso."""
    valor = texto.encode('utf-8')
    for numero in range(iteraciones):
        if numero % 4096 == 0 and detener is not None and detener.is_set():
            return None
        valor = hashlib.sha256(valor).digest()
    return valor.hex()


async def descargar_url(client: httpx.AsyncClient, url: str) -> dict:
    inicio = perf_counter()
    try:
        # El streaming cuenta bytes sin guardar todo el archivo en memoria.
        async with client.stream('GET', url) as respuesta:
            tamano = 0
            async for fragmento in respuesta.aiter_bytes():
                tamano += len(fragmento)
        return {
            'url': url,
            'codigo_estado': respuesta.status_code,
            'tamano_bytes': tamano,
            'tiempo_segundos': perf_counter() - inicio,
            'error': None,
        }
    except httpx.HTTPError as exc:
        return {
            'url': url,
            'codigo_estado': None,
            'tamano_bytes': None,
            'tiempo_segundos': perf_counter() - inicio,
            'error': str(exc),
        }


async def ejecutar(solicitud: Solicitud, client: httpx.AsyncClient, executor, detener=None):
    if isinstance(solicitud, Descargar):
        async with asyncio.TaskGroup() as grupo:
            descargas = [
                grupo.create_task(descargar_url(client, str(url)))
                for url in solicitud.urls
            ]
        return [descarga.result() for descarga in descargas]
    if isinstance(solicitud, Hashear):
        loop = asyncio.get_running_loop()
        futuro = loop.run_in_executor(
            executor, hash_iterado, solicitud.texto, solicitud.iteraciones, detener
        )
        try:
            valor = await asyncio.shield(futuro)
        except asyncio.CancelledError:
            if detener is not None:
                detener.set()
            # Cancelar el await no detiene un proceso. Esperamos su salida
            # cooperativa antes de liberar el worker y el evento compartido.
            await asyncio.shield(futuro)
            raise
        return {'hash': valor, 'iteraciones': solicitud.iteraciones}
    if isinstance(solicitud, Esperar):
        await asyncio.sleep(solicitud.segundos)
        return {'segundos': solicitud.segundos}
    raise ValueError('Tipo de tarea no soportado')
