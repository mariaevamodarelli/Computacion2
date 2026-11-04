# TP2 — Plataforma de tareas distribuidas

Tercer avance de Computación II: API HTTP con una cola en memoria, workers
asíncronos, cancelación, timeouts, volcado JSON y tres experimentos reproducibles.
El informe técnico incluye resultados de una ejecución local controlada.

## Ejecutar

```sh
docker compose up --build
```

API en http://localhost:8000 y documentación interactiva en
http://localhost:8000/docs. Se usa un único proceso de Uvicorn porque el estado y
la cola viven en memoria: varias instancias tendrían estados independientes.

Para desarrollo local con Python 3.11 o superior:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Probar

Crear una espera de cinco segundos. El POST devuelve `202` con el identificador
sin esperar esos cinco segundos:

```sh
curl -X POST http://localhost:8000/tareas \
  -H 'Content-Type: application/json' \
  -d '{"tipo":"esperar","segundos":5}'

curl -X POST http://localhost:8000/tareas \
  -H 'Content-Type: application/json' \
  -d '{"tipo":"hashear","texto":"hola","iteraciones":100000}'

curl -X POST http://localhost:8000/tareas \
  -H 'Content-Type: application/json' \
  -d '{"tipo":"descargar","urls":["https://example.com","https://www.python.org"]}'

curl http://localhost:8000/tareas
curl 'http://localhost:8000/tareas?estado=completada'
curl http://localhost:8000/tareas/REEMPLAZAR_POR_ID
curl http://localhost:8000/estadisticas
```

Estados implementados: `pendiente`, `ejecutando`, `completada`, `fallida` y `cancelada`.
Un identificador desconocido devuelve `404`; los parámetros inválidos, `422`;
una cola llena, `503`. Cada descarga devuelve su propio error de red si lo hay;
la tarea contenedora puede quedar completada con errores individuales. Los
códigos HTTP como `404` o `500` se reportan como respuestas recibidas.

## Arquitectura

```mermaid
flowchart TD
    C[Cliente HTTP] --> A[FastAPI]
    A --> Q[asyncio.Queue acotada]
    Q --> W[Pool de N corrutinas]
    W --> IO[httpx.AsyncClient / asyncio.sleep]
    W --> CPU[ProcessPoolExecutor: hash iterado]
    W --> E[Estados y resultados en memoria]
    A --> E
```

`WORKERS` configura cuántas tareas se ejecutan a la vez; `PROCESOS_CPU`, cuántos
procesos calculan hashes; `CAPACIDAD_COLA`, cuántas tareas esperan. Los valores
por defecto son 4, 2 y 100. Cada tarea de descarga acepta hasta 20 URLs, y el
cliente HTTP compartido limita las conexiones a 40.

## Decisiones de diseño

- `asyncio.Queue` permite esperar trabajo con `await` sin bloquear el event
  loop. `queue.Queue.get()` es bloqueante y está pensado para threads.
- El POST valida y encola con `put_nowait`: si no hay espacio, informa el error
  en vez de quedarse esperando. Registrar la tarea no contiene ningún `await`;
  el worker no puede consumirla antes de que se registre.
- Los workers son corrutinas, no procesos. Las descargas usan `asyncio.TaskGroup`
  para iniciar las URLs concurrentemente y esperar su limpieza si alguna falla. Un `for` con un `await` por URL las
  descargaría secuencialmente.
- `httpx.AsyncClient` se comparte para reutilizar conexiones. Los cuerpos se
  leen por fragmentos para contar bytes sin guardar archivos completos.
- El hash se envía con `run_in_executor` a procesos separados: el cálculo Python
  no ocupa el hilo del event loop y puede usar varios núcleos. Se usa `spawn`
  explícitamente y la función de hash está al nivel del módulo, para poder
  importarla en los procesos hijos.
- Una excepción ordinaria de una tarea se registra y la marca como fallida; el
  worker continúa atendiendo la cola. `task_done()` se ejecuta en `finally`.
- El promedio de estadísticas contempla tareas terminadas, tanto completadas
  como fallidas, y mide ejecución, sin contar espera en la cola.

## Cancelación, timeouts y apagado

Cada POST acepta `timeout` en segundos (por defecto 30). El límite cuenta desde
que comienza la ejecución, sin incluir la espera en la cola. Si vence, la tarea
pasa a `fallida`, con el motivo en `error`.

```sh
curl -X POST http://localhost:8000/tareas \
  -H 'Content-Type: application/json' \
  -d '{"tipo":"esperar","segundos":20,"timeout":2}'

curl -X DELETE http://localhost:8000/tareas/REEMPLAZAR_POR_ID
```

La cancelación de una tarea pendiente la marca y el worker la saltea cuando
sale de la cola. Esa entrada ocupa espacio hasta ser consumida. La cancelación
de una tarea en ejecución cancela su corrutina hija, conservando el worker.
Cancelar una tarea terminada devuelve su estado sin modificarla.

Las descargas usan `TaskGroup`: ante cancelación o error inesperado se esperan
todas las corrutinas hijas. El contexto del streaming cierra la respuesta.
Para CPU, cancelar el await no detiene el proceso: se usa un evento compartido
mediante `multiprocessing.Manager`. El hash lo consulta cada 4096 iteraciones.
Al cancelar o vencer el timeout, se activa el evento y se espera la salida del
cálculo. Por eso el timeout puede excederse ligeramente durante la limpieza.
El hash sigue usando `run_in_executor` y procesos separados.

Uvicorn recibe SIGINT/SIGTERM y ejecuta el cierre del lifespan. Se dejan de
aceptar tareas nuevas, se cancelan las pendientes y se concede hasta
`ESPERA_APAGADO` segundos (10 por defecto) a las activas. Después se cancelan
las restantes, se espera su limpieza y se cierran conexiones y procesos.
Se escribe `datos/estado_<timestamp>.json` mediante archivo temporal y rename.
Compose monta esa carpeta en el host para conservar los volcados. Al arrancar
se informa por log si hay un volcado previo; no se restaura ni reejecuta.

## Mediciones reproducibles

Desde la raíz del proyecto, con las dependencias instaladas:

```sh
python -m benchmarks.medir
```

También se puede ejecutar dentro de Docker, sin levantar la API normal:

```sh
mkdir -p mediciones
docker compose build
docker compose run --rm --no-deps -v "$PWD/mediciones:/app/mediciones" api python -m benchmarks.medir
```

El comando inicia y cierra servidores temporales en puertos locales libres;
no usa el servicio de puerto 8000 ni sitios de Internet. Guarda muestras y
metadatos en `mediciones/resultados.json`. La variante que bloquea el event loop
existe solo en `benchmarks/servidor.py`; el arranque normal con `app.main:app`
siempre utiliza el executor. Los resultados detallados y la interpretación
están en [mediciones/INFORME.md](mediciones/INFORME.md).

Los datos incluidos se obtuvieron ejecutando Python en este entorno de trabajo,
no dentro de Docker ni en la terminal del estudiante. Son mediciones reales de
HTTP local con una demora controlada; no describen la velocidad de Internet.
Si se repite el experimento en otra máquina, hay que actualizar resultados e
interpretación juntos: el JSON se sobrescribe, el texto del informe no.

### Resultados incluidos

Configuración: 4 workers, 2 procesos CPU y cola de 100. HTTP local real;
respuestas de 1024 bytes con 100 ms de demora controlada. Medianas de tres
repeticiones para I/O y CPU; una muestra por tamaño de lote para el pool.

| Experimento | Resultado |
|---|---|
| 20 descargas secuenciales | 2.0672 s |
| 20 descargas concurrentes | 0.1233 s |
| Speedup (cociente de medianas) | 16.76× |
| GET durante hash sin executor | 710.601 ms |
| GET durante hash con executor | 0.996 ms |

El hash usa 3.000.000 iteraciones de SHA-256. El executor mantiene disponible
el event loop; el beneficio medido es la respuesta de la API, no acelerar el
hash aislado. En I/O, las esperas de red se superponen.

| Tareas enviadas | Tiempo de lote (s) | Latencia p95 (s) | Rechazadas |
|---:|---:|---:|---:|
| 1 | 0.161 | 0.151 | 0 |
| 4 | 0.164 | 0.152 | 0 |
| 8 | 0.315 | 0.299 | 0 |
| 16 | 0.613 | 0.594 | 0 |
| 32 | 1.234 | 1.180 | 0 |
| 64 | 2.444 | 2.352 | 0 |
| 128 | 3.955 | 3.697 | 24 |

Las tareas de este experimento esperan 150 ms. Por encima de cuatro tareas
simultáneas aparece cola; el rendimiento se aproxima a `4 / 0,15 ≈ 26,67`
tareas/s y la latencia crece. El lote de 128 llena la capacidad instantánea
de 4 activas + 100 pendientes. Este techo corresponde a esperas, no mide el
máximo de CPU o de red. El informe vinculado conserva cada muestra y explica
la sincronización del experimento con y sin executor.

## Conceptos de la materia aplicados

- **Clases 13–14, sockets y servidores concurrentes:** Uvicorn atiende conexiones
  TCP y FastAPI expone las operaciones HTTP. En las mediciones, cliente y
  servidor son procesos distintos comunicados por TCP de loopback.
- **Clase 17, multiplexación:** el event loop puede esperar múltiples sockets
  sin asignar un thread a cada descarga. El mecanismo concreto depende del
  sistema operativo; no se invoca `select` manualmente en la aplicación.
- **Clase 18, corrutinas y event loop:** los `await` de HTTPX y `asyncio.sleep`
  ceden el control. Ejecutar el bucle de hashes en ese hilo impide atender
  otras solicitudes, como muestra el experimento de latencia.
- **Clase 19, HTTP y FastAPI:** Pydantic valida las solicitudes y el POST
  devuelve `202 Accepted`. El cliente consulta luego el recurso por su ID.
- **Clases 20–21, asyncio en red y avanzado:** `asyncio.Queue` implementa
  productor–consumidor; `TaskGroup` administra las descargas hijas;
  `wait_for` combina límite temporal con cancelación y limpieza.
- **Clase 22, concurrent.futures:** `ProcessPoolExecutor` aísla el cálculo
  intensivo y `run_in_executor` lo integra con el event loop. El evento
  compartido permite la detención cooperativa.
- **Bloque 0, context managers:** `lifespan`, `async with client.stream(...)`
  y `try/finally` definen cuándo abrir y cerrar recursos.

No hace falta un lock para registrar una tarea porque no hay `await` entre la
inserción en la cola y su registro. Esto no vuelve atómicas todas las funciones
async: cada `await` permite que otra corrutina avance. El estado de tareas se
modifica en el hilo del event loop; los procesos solo calculan y consultan el
evento compartido.

Con 10.000 solicitudes de golpe, una vez ocupados los workers y los 100 lugares
de la cola, el POST responde `503`. Eso evita una cola ilimitada, pero no limita
el historial: las tareas terminadas siguen ocupando memoria. El listado y las
estadísticas recorren ese historial, lo cual también aumenta su costo.

## Limitaciones actuales

El historial crece en memoria y no se restaura al reiniciar. No hay autenticación.
El cierre tiene un tiempo de gracia limitado, seguido por limpieza cooperativa;
no garantiza un límite absoluto si un proceso del sistema deja de responder.
Las URLs son aportadas por el cliente: esta versión se debe usar en un entorno
local controlado, no como servicio público. Las dependencias tienen rangos de
versiones y aún no hay archivo de bloqueo reproducible.

## Verificación

Tras instalar requirements:

```sh
python -m unittest discover -s tests -v
```

Las pruebas cubren encolado inmediato, concurrencia, recuperación tras errores,
cola llena, descargas simuladas, hash en proceso real, cancelación pendiente y
en ejecución, cancelación y timeout de CPU, cierre de streaming, timeout de
espera, apagado con cancelación y volcado JSON, y detección del volcado previo.
Las 11 pruebas pasaron en esta copia. La configuración de Compose se validó;
no se ejecutó la imagen Docker por falta de acceso al daemon en este entorno.
