"""Actualiza informe y tablas del README con las muestras guardadas."""
import json
from pathlib import Path
import statistics


def generar():
    d = json.loads(Path('mediciones/resultados.json').read_text())
    med = lambda key, field: statistics.median(x[field] for x in d[key])
    sec, con = med('io', 'secuencial_s'), med('io', 'concurrente_s')
    directa = med('cpu_directa', 'latencia_get_s') * 1000
    executor = med('cpu_executor', 'latencia_get_s') * 1000
    vigente = d['configuracion'].get('hash_bloque_iteraciones') == 50_000
    nota = ('Muestras de la implementación final con bloques de hash.' if vigente else
            '**Muestras anteriores al ajuste final de CPU:** el hash todavía usaba '
            'un evento compartido. Son datos reales, pero no validan el rendimiento '
            'de la versión por bloques. Repetir python -m benchmarks.medir para '
            'actualizar automáticamente resultados e informe.')
    resumen = f'''### Resultados incluidos

{nota}

| Experimento | Resultado |
|---|---:|
| 20 descargas secuenciales (mediana) | {sec:.4f} s |
| 20 descargas concurrentes (mediana) | {con:.4f} s |
| Speedup (cociente de medianas) | {sec/con:.2f}× |
| GET durante hash sin executor (mediana) | {directa:.3f} ms |
| GET durante hash con executor (mediana) | {executor:.3f} ms |

Tres repeticiones de I/O y CPU. HTTP local real con respuestas de 1024 bytes y
100 ms de espera. El hash usa 3.000.000 iteraciones SHA-256. El executor mejora
la respuesta de la API, no necesariamente el tiempo del hash.

| Enviadas | Aceptadas | Rechazadas (503) | Lote (s) | Mediana (s) | p95 (s) | Tareas/s |
|---:|---:|---:|---:|---:|---:|---:|
'''
    for x in d['pool']:
        resumen += (f"| {x['enviadas']} | {x['aceptadas']} | {x['rechazadas']} | "
                    f"{x['lote_s']:.3f} | {x['latencia_mediana_s']:.3f} | "
                    f"{x['latencia_p95_s']:.3f} | {x['tareas_por_s']:.2f} |\n")
    resumen += '''
Con cuatro workers y esperas de 150 ms, el techo esperado es 4 / 0,15 ≈ 26,67
tareas/s. Al superar cuatro tareas aparece espera en cola. La capacidad
instantánea es 4 activas + 100 pendientes; no es un máximo universal por lote,
porque pueden terminar tareas mientras llegan otras.

'''
    io = '\n'.join(f"| {i} | {x['secuencial_s']:.4f} | {x['concurrente_s']:.4f} | {x['speedup']:.2f}× |" for i, x in enumerate(d['io'], 1))
    cpu = '\n'.join(f"| {modo} | {i} | {x['duracion_hash_s']:.4f} | {1000*x['latencia_get_s']:.3f} |" for key, modo in [('cpu_directa','Sin executor'),('cpu_executor','Con executor')] for i, x in enumerate(d[key], 1))
    informe = f'''# Mediciones — TP2

## Entorno

Inicio UTC: {d['fecha_utc']}. Python {d['entorno']['python']}.
Plataforma: {d['entorno']['plataforma']}. CPU visibles: {d['entorno']['cpus_visibles']}
(no implica CPU dedicadas). FastAPI {d['entorno']['fastapi']}, HTTPX {d['entorno']['httpx']},
Pydantic {d['entorno']['pydantic']}. Cuatro workers, dos procesos CPU y cola de 100.
Se midió fuera de Docker, con TCP de loopback, sin sitios externos.

{nota}

## 1. Veinte descargas secuenciales y concurrentes

Se descargan respuestas de 1024 bytes con demora de 100 ms. Se comprueba código
200, tamaño y ausencia de errores. Se reutiliza el cliente. La versión secuencial
usa un for con await descargar_url; la concurrente usa ejecutar(Descargar(...)).
Son las funciones reales de la aplicación, sin espera en la cola. Cada par se
mide primero secuencial y luego concurrente.

| Repetición | Secuencial (s) | Concurrente (s) | Speedup |
|---|---:|---:|---:|
{io}

El cociente de medianas es {sec/con:.2f}×. Las esperas se superponen y el tiempo
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
{cpu}

Sin executor, el bucle ocupa el hilo hasta terminar e impide atender el GET.
Con executor, otro proceso calcula y el event loop puede atender conexiones.
La comparación mide capacidad de respuesta, no aceleración del hash aislado.

## 3. Saturación del pool

Se envían lotes concurrentes de tareas esperar de 150 ms por los endpoints
reales. Cada lote termina antes del siguiente. Su tiempo incluye envío y
consultas hasta detectar el final. La latencia por tarea va de creada a
terminada e incluye cola; duracion_segundos solo mide ejecución. El p95 usa
rango más cercano. Hay una muestra por tamaño de lote.

{resumen}

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
'''
    Path('mediciones/INFORME.md').write_text(informe, encoding='utf-8')
    archivo = Path('README.md')
    texto = archivo.read_text(encoding='utf-8')
    inicio = texto.index('### Resultados incluidos')
    fin = texto.index('## Conceptos de la materia aplicados', inicio)
    archivo.write_text(texto[:inicio] + resumen + texto[fin:], encoding='utf-8')


if __name__ == '__main__':
    generar()
