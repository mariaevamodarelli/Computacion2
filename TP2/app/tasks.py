import asyncio
import hashlib
from time import perf_counter

import httpx

from app.models import Descargar, Esperar, Hashear, Solicitud


def hash_bloque(valor: bytes, iteraciones: int) -> bytes:
    for _ in range(iteraciones):
        valor = hashlib.sha256(valor).digest()
    return valor


def hash_iterado(texto: str, iteraciones: int) -> str:
    """Versión síncrona para el experimento que bloquea el event loop."""
    return hash_bloque(texto.encode('utf-8'), iteraciones).hex()


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


async def ejecutar(solicitud: Solicitud, client: httpx.AsyncClient, executor):
    if isinstance(solicitud, Descargar):
        async with asyncio.TaskGroup() as grupo:
            descargas = [
                grupo.create_task(descargar_url(client, str(url)))
                for url in solicitud.urls
            ]
        return [descarga.result() for descarga in descargas]
    if isinstance(solicitud, Hashear):
        loop = asyncio.get_running_loop()
        valor = solicitud.texto.encode('utf-8')
        restantes = solicitud.iteraciones
        while restantes:
            cantidad = min(restantes, 50_000)
            futuro = loop.run_in_executor(executor, hash_bloque, valor, cantidad)
            try:
                valor = await asyncio.shield(futuro)
            except asyncio.CancelledError:
                # No se envían nuevos bloques; se espera únicamente el actual.
                await asyncio.shield(futuro)
                raise
            restantes -= cantidad
        valor = valor.hex()
        return {'hash': valor, 'iteraciones': solicitud.iteraciones}
    if isinstance(solicitud, Esperar):
        await asyncio.sleep(solicitud.segundos)
        return {'segundos': solicitud.segundos}
    raise ValueError('Tipo de tarea no soportado')
