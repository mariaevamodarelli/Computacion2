from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl


class Estado(StrEnum):
    PENDIENTE = 'pendiente'
    EJECUTANDO = 'ejecutando'
    COMPLETADA = 'completada'
    FALLIDA = 'fallida'


class Descargar(BaseModel):
    tipo: Literal['descargar']
    urls: list[HttpUrl] = Field(min_length=1, max_length=20)


class Hashear(BaseModel):
    tipo: Literal['hashear']
    texto: str = Field(max_length=100_000)
    iteraciones: int = Field(ge=1, le=10_000_000)


class Esperar(BaseModel):
    tipo: Literal['esperar']
    segundos: float = Field(ge=0, le=300, allow_inf_nan=False)


Solicitud = Annotated[Descargar | Hashear | Esperar, Field(discriminator='tipo')]


def ahora() -> datetime:
    return datetime.now(timezone.utc)


class Tarea(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    tipo: str
    estado: Estado = Estado.PENDIENTE
    creada: datetime = Field(default_factory=ahora)
    iniciada: datetime | None = None
    terminada: datetime | None = None
    duracion_segundos: float | None = None
    resultado: Any = None
    error: str | None = None
