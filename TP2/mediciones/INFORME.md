# Mediciones — TP2

## Entorno

Inicio UTC: 2026-11-04T02:41:58.743087+00:00. Python 3.12.3 (main, Aug 31 2026, 10:18:26) [GCC 13.3.0].
Plataforma: Linux-7.0.0-31-generic-x86_64-with-glibc2.39. CPU visibles: 16
(no implica CPU dedicadas). FastAPI 0.162.0, HTTPX 0.28.1,
Pydantic 2.14.2. Cuatro workers, dos procesos CPU y cola de 100.
Se midió fuera de Docker, con TCP de loopback, sin sitios externos.

Muestras de la implementación final con bloques de hash.

## 1. Veinte descargas secuenciales y concurrentes

Se descargan respuestas de 1024 bytes con demora de 100 ms. Se comprueba código
200, tamaño y ausencia de errores. Se reutiliza el cliente. La versión secuencial
usa un for con await descargar_url; la concurrente usa ejecutar(Descargar(...)).
Son las funciones reales de la aplicación, sin espera en la cola. Cada par se
mide primero secuencial y luego concurrente.

| Repetición | Secuencial (s) | Concurrente (s) | Speedup |
|---|---:|---:|---:|
| 1 | 2.0601 | 0.1183 | 17.42× |
| 2 | 2.0614 | 0.1266 | 16.28× |
| 3 | 2.0667 | 0.1230 | 16.80× |

El cociente de medianas es 16.76×. Las esperas se superponen y el tiempo
se acerca al de una respuesta más apertura de conexiones y planificación.
Esto no acelera CPU ni describe el ancho de banda de Internet.

## 2. Hash y respuesta del event loop

Se encola un hash de 3.000.000 iteraciones. Un marcador escrito justo antes del
cálculo permite al cliente externo enviar GET /tareas durante el trabajo. El
cliente corre en otro proceso y puede medir aunque el servidor esté bloqueado.
El pool se calienta antes de medir. La variante síncrona solo existe en el
servidor de benchmarks; la API normal siempre usa el executor. Se comprueba
que los hashes terminen completados.

| Modo | Repetición | Hash (s) | GET (ms) |
|---|---:|---:|---:|
| Sin executor | 1 | 0.6314 | 632.758 |
| Sin executor | 2 | 0.6466 | 647.735 |
| Sin executor | 3 | 0.6326 | 633.988 |
| Con executor | 1 | 0.8072 | 1.467 |
| Con executor | 2 | 0.7383 | 0.953 |
| Con executor | 3 | 0.7123 | 0.966 |

Sin executor, el bucle ocupa el hilo hasta terminar e impide atender el GET.
Con executor, otro proceso calcula y el event loop puede atender conexiones.
La comparación mide capacidad de respuesta, no aceleración del hash aislado.

## 3. Saturación del pool

Se envían lotes concurrentes de tareas esperar de 150 ms por los endpoints
reales. Cada lote termina antes del siguiente. Su tiempo incluye envío y
consultas hasta detectar el final. La latencia por tarea va de creada a
terminada e incluye cola; duracion_segundos solo mide ejecución. El p95 usa
rango más cercano. Hay una muestra por tamaño de lote.

### Resultados incluidos

Muestras de la implementación final con bloques de hash.

| Experimento | Resultado |
|---|---:|
| 20 descargas secuenciales (mediana) | 2.0614 s |
| 20 descargas concurrentes (mediana) | 0.1230 s |
| Speedup (cociente de medianas) | 16.76× |
| GET durante hash sin executor (mediana) | 633.988 ms |
| GET durante hash con executor (mediana) | 0.966 ms |

Tres repeticiones de I/O y CPU. HTTP local real con respuestas de 1024 bytes y
100 ms de espera. El hash usa 3.000.000 iteraciones SHA-256. El executor mejora
la respuesta de la API, no necesariamente el tiempo del hash.

| Enviadas | Aceptadas | Rechazadas (503) | Lote (s) | Mediana (s) | p95 (s) | Tareas/s |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 0 | 0.159 | 0.151 | 0.151 | 6.31 |
| 4 | 4 | 0 | 0.158 | 0.151 | 0.151 | 25.28 |
| 8 | 8 | 0 | 0.312 | 0.225 | 0.299 | 25.65 |
| 16 | 16 | 0 | 0.624 | 0.372 | 0.594 | 25.66 |
| 32 | 32 | 0 | 1.233 | 0.663 | 1.178 | 25.95 |
| 64 | 64 | 0 | 2.436 | 1.249 | 2.346 | 26.27 |
| 128 | 104 | 24 | 3.960 | 1.998 | 3.694 | 26.26 |

Con cuatro workers y esperas de 150 ms, el techo esperado es 4 / 0,15 ≈ 26,67
tareas/s. Al superar cuatro tareas aparece espera en cola. La capacidad
instantánea es 4 activas + 100 pendientes; no es un máximo universal por lote,
porque pueden terminar tareas mientras llegan otras.



El rendimiento se estabiliza mientras crece la latencia. Más solicitudes no
agregan workers. CPU, conexiones HTTP y red impondrían límites distintos en
otras cargas; este experimento caracteriza esperas.

## Reproducción y límites

Ejecutar python -m benchmarks.medir desde la raíz con requirements instalados.
Arranca y cierra servidores temporales, guarda JSON y regenera este informe y
las tablas del README. Para regenerar solo textos: python -m benchmarks.informe.

Tres repeticiones son pocas; el orden es fijo y puede variar la carga del equipo.
No se extrapolan percentiles de producción. Las pruebas unitarias usan
MockTransport; estas mediciones usan HTTP real y conservan muestras en JSON.
