# Mediciones — TP2

## Entorno y alcance

Ejecución iniciada el 2026-11-03T23:15:05.027653+00:00. Python 3.12.3,
Linux-7.0.0-31-generic-x86_64-with-glibc2.39. CPU lógicas visibles: 16
(no implica núcleos dedicados ni cuotas garantizadas).
FastAPI 0.162.0, HTTPX 0.28.1,
Pydantic 2.14.2.

Datos obtenidos por ejecución real desde el entorno de desarrollo, fuera de
Docker. La red es TCP de loopback. Se usan 4 workers, 2 procesos de CPU y una
cola de capacidad 100. Las muestras originales están en `resultados.json`.

El cliente de medición corre en un proceso distinto al servidor, por lo que
puede medir una respuesta lenta incluso si el event loop del servidor está
bloqueado. Se usan puertos libres y carpetas temporales para no interferir con
la API normal. Se repitió la corrida final sin ejecutar las pruebas en paralelo.
No se mide un entorno de producción ni se presupone que otros equipos tengan
los mismos resultados.

## 1. Veinte descargas secuenciales frente a concurrentes

Se descargan 20 respuestas HTTP de 1024 bytes. Cada respuesta demora 100 ms
mediante `asyncio.sleep` en el servidor de prueba. El tiempo incluye la red
local, la espera y la lectura completa del cuerpo. Se comprueba HTTP 200,
tamaño y ausencia de errores. El cliente HTTP se reutiliza.

La versión secuencial usa un `for` con `await descargar_url`; la concurrente
invoca `ejecutar(Descargar(...))`, que usa `TaskGroup`. Se llaman las mismas
funciones de descarga de la aplicación, sin medir la espera de su cola.
Se ejecutan tres pares, siempre secuencial y luego concurrente.

| Repetición | Secuencial (s) | Concurrente (s) | Speedup |
|---|---:|---:|---:|
| 1 | 2.0651 | 0.1204 | 17.16× |
| 2 | 2.0672 | 0.1233 | 16.76× |
| 3 | 2.0700 | 0.1271 | 16.29× |

Mediana secuencial: **2.0672 s**. Mediana concurrente: **0.1233 s**.
Cociente de medianas: **16.76×**. El speedup de cada fila se calcula
como tiempo secuencial dividido por tiempo concurrente.

Las esperas se superponen: el tiempo concurrente se acerca al de una respuesta,
con el costo adicional de abrir conexiones y programar corrutinas. La mejora
no representa una aceleración del cálculo ni una medición del ancho de banda
de Internet. La demora controlada domina este caso de I/O.

## 2. Event loop bloqueado por CPU

Cada hash aplica SHA-256 3.000.000 de veces. Un servidor experimental usa la
misma API y el mismo gestor, pero reemplaza la ejecución de `hashear` por una
llamada síncrona directa. La otra variante usa el executor de la aplicación.
El pool se calienta con una tarea mínima antes de medir.

El servidor crea un marcador justo antes del cálculo; el cliente externo lo
detecta y envía `GET /tareas`. Así el GET se envía durante el trabajo, sin
necesitar que el servidor responda primero a una consulta de estado. El modo
que bloquea está confinado al módulo de benchmarks y no se activa al ejecutar
`uvicorn app.main:app`. Se verificó que todos los hashes terminaran completados.

| Modo | Repetición | Ejecución del hash (s) | Latencia GET (ms) |
|---|---:|---:|---:|
| Sin executor | 1 | 0.7096 | 708.941 |
| Sin executor | 2 | 0.7120 | 710.601 |
| Sin executor | 3 | 0.7142 | 713.482 |
| Con executor | 1 | 0.7662 | 1.328 |
| Con executor | 2 | 0.7447 | 0.945 |
| Con executor | 3 | 0.7546 | 0.996 |

La mediana del GET pasó de **710.601 ms** sin executor a
**0.996 ms** con executor (cociente: **713.6×**).

Sin executor el hilo no vuelve al event loop hasta terminar el bucle, por eso
el GET espera casi todo ese tiempo. Con executor el cálculo ocurre en otro
proceso y el servidor sigue atendiendo conexiones. Esto mide capacidad de
respuesta: no demuestra que calcular un hash aislado sea más rápido con pool.
La creación de eventos, la comunicación entre procesos y la planificación
agregan costos.

## 3. Techo del pool y espera en cola

Se envían lotes de tareas `esperar` de 150 ms mediante POST concurrentes. Se
usan los endpoints reales de la API. Cada lote termina antes de comenzar el
siguiente. El tiempo de lote incluye envío y consultas hasta detectar el final.
La latencia por tarea se calcula desde `creada` hasta `terminada`: incluye cola
y ejecución; el campo `duracion_segundos` de la API solo mide ejecución.

El p95 usa el método de rango más cercano sobre las tareas aceptadas. Se hace
una muestra por tamaño de lote: sirve para observar el límite, no para estimar
un percentil estable de producción.

| Enviadas | Aceptadas | Rechazadas (503) | Lote (s) | Mediana (s) | p95 (s) | Tareas/s |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 0 | 0.161 | 0.151 | 0.151 | 6.20 |
| 4 | 4 | 0 | 0.164 | 0.152 | 0.152 | 24.33 |
| 8 | 8 | 0 | 0.315 | 0.224 | 0.299 | 25.38 |
| 16 | 16 | 0 | 0.613 | 0.372 | 0.594 | 26.09 |
| 32 | 32 | 0 | 1.234 | 0.664 | 1.180 | 25.93 |
| 64 | 64 | 0 | 2.444 | 1.251 | 2.352 | 26.18 |
| 128 | 104 | 24 | 3.955 | 1.998 | 3.697 | 26.30 |

Con cuatro tareas, cada una puede comenzar sin esperar un worker. Al superar
cuatro, aparece espera en cola: ya se ve en el lote de ocho. El rendimiento se
aproxima al techo teórico de este caso, `4 / 0,15 ≈ 26,67 tareas/s`, mientras
la latencia crece por tandas. Agregar solicitudes no agrega workers.

En el lote de 128 se aceptaron 104 y se rechazaron
24. El techo instantáneo es cuatro tareas ejecutando
más cien pendientes. No es un máximo universal de solicitudes por lote: si
algunas terminan mientras llegan otras, puede aceptarse un total mayor.

Este resultado caracteriza un pool ocupado por esperas. En hashes, además
limita el pool de dos procesos; en descargas también influyen las conexiones
HTTP, la red y el servidor remoto. No se midió aquí la saturación de todos los
recursos posibles.

## Reproducción y límites de interpretación

Ejecutar `python -m benchmarks.medir` desde la raíz con requirements instalados.
El comando tarda del orden de decenas de segundos y guarda JSON nuevo. El
informe debe actualizarse si se reemplazan esas muestras.

Tres repeticiones en I/O y CPU son una muestra pequeña. El orden es fijo, hay
variación de planificación y no se controla la carga ajena del equipo. Para
conclusiones más generales se necesitan más repeticiones y cargas distintas.
No se usaron respuestas HTTP simuladas en estas mediciones: las simulaciones
con `MockTransport` están únicamente en las pruebas unitarias.
