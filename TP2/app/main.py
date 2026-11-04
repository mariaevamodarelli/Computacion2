import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, status

from app.manager import ColaLlena, Gestor
from app.models import Estado, Solicitud, Tarea


def entero_positivo(nombre: str, defecto: int) -> int:
    valor = int(os.getenv(nombre, str(defecto)))
    if valor < 1:
        raise ValueError(f'{nombre} debe ser mayor que cero')
    return valor


@asynccontextmanager
async def lifespan(app: FastAPI):
    gestor = Gestor(
        workers=entero_positivo('WORKERS', 4),
        capacidad=entero_positivo('CAPACIDAD_COLA', 100),
        procesos=entero_positivo('PROCESOS_CPU', 2),
        directorio_estado=os.getenv('DIRECTORIO_ESTADO', 'datos'),
        espera_apagado=entero_positivo('ESPERA_APAGADO', 10),
    )
    await gestor.iniciar()
    app.state.gestor = gestor
    try:
        yield
    finally:
        await gestor.cerrar()


app = FastAPI(title='Plataforma de tareas distribuidas', lifespan=lifespan)


@app.post('/tareas', response_model=Tarea, status_code=status.HTTP_202_ACCEPTED)
async def crear_tarea(solicitud: Solicitud, request: Request):
    gestor = request.app.state.gestor
    if not gestor.aceptando:
        raise HTTPException(503, 'El servicio se está apagando')
    try:
        return gestor.encolar(solicitud)
    except ColaLlena:
        raise HTTPException(503, 'Cola llena; intentá nuevamente más tarde')


@app.get('/tareas', response_model=list[Tarea])
async def listar_tareas(request: Request, estado: Estado | None = Query(None)):
    tareas = request.app.state.gestor.tareas.values()
    return [tarea for tarea in tareas if estado is None or tarea.estado == estado]


@app.get('/tareas/{identificador}', response_model=Tarea)
async def obtener_tarea(identificador: str, request: Request):
    tarea = request.app.state.gestor.tareas.get(identificador)
    if tarea is None:
        raise HTTPException(404, 'Tarea inexistente')
    return tarea


@app.get('/estadisticas')
async def estadisticas(request: Request):
    return request.app.state.gestor.estadisticas()


@app.delete('/tareas/{identificador}', response_model=Tarea)
async def cancelar_tarea(identificador: str, request: Request):
    gestor = request.app.state.gestor
    if identificador not in gestor.tareas:
        raise HTTPException(404, 'Tarea inexistente')
    return await gestor.cancelar(identificador)
