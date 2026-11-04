"""Servidor exclusivo para mediciones locales. No usar como API de producción."""
import asyncio
import os
from pathlib import Path

from fastapi.responses import Response

import app.manager as manager
from app.main import app
from app.models import Hashear
from app.tasks import ejecutar, hash_iterado


async def ejecutar_medido(solicitud, client, executor):
    if isinstance(solicitud, Hashear):
        # El cliente está en otro proceso: detecta este marcador aun cuando
        # el event loop del servidor se encuentra bloqueado por el hash.
        Path(os.environ['MARCADOR']).touch()
        if os.getenv('CPU_DIRECTA') == '1':
            return {'hash': hash_iterado(solicitud.texto, solicitud.iteraciones),
                    'iteraciones': solicitud.iteraciones}
    return await ejecutar(solicitud, client, executor)


manager.ejecutar = ejecutar_medido


@app.get('/fixture')
async def fixture():
    # Red HTTP real por loopback, con demora controlada y cuerpo conocido.
    await asyncio.sleep(0.1)
    return Response(content=b'x' * 1024, media_type='application/octet-stream')
