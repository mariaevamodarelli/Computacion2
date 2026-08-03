# Trabajo Practico Nro 1 - Monitor de Procesos y Threads

Computacion II - Universidad de Mendoza - 2026
Nombre: Maria Eva Modarelli

## 1. Descripcion general

Este proyecto implementa un monitor de procesos para Linux leyendo directamente el filesystem `/proc`, sin usar `psutil` ni comandos externos como `ps`, `top` o `htop`.

El programa muestra una TUI en consola hecha con `curses`. Arriba se ve siempre una lista resumida de procesos y abajo aparece el detalle de la vista activa. Las vistas disponibles son:

1. Resumen
2. Memoria
3. File descriptors
4. Threads
5. Senales
6. Scheduling
7. Sistema global

Los atajos principales son:

| Tecla | Accion |
|---|---|
| `1` a `7` o `r/m/f/t/s/p/g` | Cambiar de vista |
| Flechas | Navegar procesos |
| `Enter` | Fijar el proceso seleccionado |
| `/` | Filtrar por comando |
| `u` | Filtrar por usuario |
| `c` | Cambiar orden CPU/RSS/PID |
| `+` / `-` | Cambiar intervalo de la vista activa |
| `h` / `?` | Ayuda |
| `q` | Salir limpiamente |

Al iniciar se lee `config.json` para cargar intervalos, filtro de comando, filtro de usuario y orden inicial.

## 2. Diagrama de arquitectura

```text
                         +-------------------------+
                         |  Snapshot global        |
                         |  Manager.dict           |
                         +-----------+-------------+
                                     ^
                                     |
                                  escribe
                                     |
         +-------------+     +-------+-------+
         | Recolector  | --> |  Agregador    |
         | lista /proc |     | actualiza     |
         +------+------+     +-------+-------+
                |                    ^
                | Queue por vista    |
                v                    | Queue de resultados
   +------------+---------------------------------------------+
   |            |            |            |                   |
+--+--+      +--+--+      +--+--+      +--+--+            +--+--+
| Res |      | Mem |      | FDs |      | Thr |    ...     | Sist|
+-----+      +-----+      +-----+      +-----+            +-----+

                         +-------------------------+
                         | Display curses          |
                         | lee snapshot            |
                         | cambia intervalos Value |
                         +-------------------------+
```

Procesos principales:

| Componente | Archivo | Responsabilidad |
|---|---|---|
| Recolector | `src/recolector.py` | Lista PIDs en `/proc` y los manda a los analizadores |
| Analizadores | `src/analizadores/` | Cada proceso calcula una dimension distinta |
| Agregador | `src/agregador.py` | Recibe resultados y actualiza el snapshot |
| Display | `src/display.py` | Muestra la TUI y procesa teclado |
| Senales | `src/senales.py` | Configura self-pipe y acciones por senal |

## 3. Decisiones de diseno

### Por que use `Queue`

Use `multiprocessing.Queue` en dos lugares. El recolector manda listas de PIDs a cada analizador con una Queue propia por vista. Despues, todos los analizadores mandan sus resultados al agregador usando una Queue compartida de resultados.

Elegir Queue me parecio natural porque el trabajo se puede pensar como mensajes: "estos son los PIDs actuales" y "este es el resultado nuevo de la vista memoria". Ademas evita compartir estructuras grandes entre procesos todo el tiempo.

### Por que use `Manager.dict`

Use `Manager.dict` para el snapshot global porque varios procesos necesitan compartir un estado comun. Un `dict` normal no serviria porque cada proceso tendria su propia copia de memoria.

El snapshot tiene claves como `resumen`, `memoria`, `fds`, `threads`, `senales`, `scheduling` y `sistema`. Cada clave guarda los datos y un timestamp.

### Por que use `Value`

Use `multiprocessing.Value` para los intervalos de refresco porque son numeros chicos compartidos entre procesos. El display puede modificar el intervalo de una vista con `+` o `-`, y el analizador correspondiente lee ese valor en su propio loop.

Esto cumple la parte de la consigna que pide que el cambio de intervalo display -> analizador se haga con memoria compartida.

### Por que use `Array`

Use `multiprocessing.Array` para contadores simples compartidos:

- cantidad de snapshots actualizados
- cantidad de recargas por SIGHUP
- cantidad de toggles de verbose por SIGUSR2

No es una estructura compleja, por eso un Array alcanza.

### Por que use `Pipe`

Use `multiprocessing.Pipe` entre el recolector y el display para mandar un heartbeat sencillo con la cantidad de PIDs vistos. No es el canal principal de datos, pero muestra una comunicacion directa punto a punto entre procesos.

### Race conditions

La race condition principal seria que el agregador este escribiendo el snapshot mientras el display lo lee. Para evitar eso uso un `RLock` del `Manager`: el agregador entra al lock antes de escribir, y el display entra al mismo lock antes de copiar datos para dibujar.

Tambien evito que las Queues crezcan sin limite: las Queues de tareas tienen `maxsize=3` y el recolector no agrega mas trabajo si ya hay backlog.

### Intervalos por defecto

Use los intervalos pedidos por la consigna:

| Vista | Intervalo |
|---|---:|
| Resumen | 2s |
| Memoria | 3s |
| FDs | 5s |
| Threads | 2s |
| Senales | 10s |
| Scheduling | 10s |
| Sistema | 2s |

Las vistas mas dinamicas refrescan mas rapido. FDs, senales y scheduling cambian menos seguido y ademas pueden ser mas costosas.

## 4. Conceptos del curso aplicados

- Procesos y `/proc`: todo el monitor se basa en leer archivos como `/proc/<pid>/stat`, `/proc/<pid>/status`, `/proc/<pid>/fd`, `/proc/<pid>/task` y `/proc/stat`.
- Fork y procesos zombie: en la vista Sistema cuento procesos en estado `Z`, que representa procesos terminados cuyo padre todavia no hizo `wait()`.
- File descriptors: en la vista FDs leo `/proc/<pid>/fd` y uso `os.readlink` para ver si apuntan a archivos, pipes, sockets o terminales.
- Senales: decodifico `SigBlk`, `SigIgn`, `SigCgt`, `SigPnd` y `ShdPnd` desde mascaras hexadecimales a nombres como `SIGINT` o `SIGTERM`.
- Multiprocessing: cada analizador corre como `multiprocessing.Process`, no como thread.
- IPC: uso `Queue`, `Pipe`, `Manager.dict`, `Value` y `Array`.
- Memoria compartida: los intervalos se comparten con `Value` y el snapshot con `Manager.dict`.
- Threads como LWPs: la vista Threads recorre `/proc/<pid>/task`, donde cada TID tiene su propio `stat`, `status` y `comm`.
- Scheduling: la vista Scheduling muestra nice, priority, policy, RT priority, affinity y context switches.
- Self-pipe: para las senales uso `signal.set_wakeup_fd`, asi el handler no hace trabajo pesado y el loop principal procesa la accion despues.

## 5. Senales soportadas

| Senal | Accion |
|---|---|
| SIGINT | Shutdown limpio |
| SIGTERM | Shutdown limpio |
| SIGHUP | Recarga intervalos, filtros default y orden desde `config.json` |
| SIGUSR1 | Guarda `dump_<timestamp>.json` |
| SIGUSR2 | Activa/desactiva modo verbose |
| SIGWINCH | Repinta por resize de terminal |

## 6. Limitaciones conocidas

- Algunos procesos pueden desaparecer mientras se leen sus archivos en `/proc`. El codigo lo maneja devolviendo datos vacios o saltando ese PID.
- Algunos archivos pueden dar `PermissionError`, sobre todo FDs o mapas de memoria de procesos de otros usuarios.
- El CPU por proceso y por thread se calcula por delta entre lecturas, por eso la primera medicion puede aparecer en cero.
- Si hay muchisimos procesos, la TUI sigue funcionando, pero los analizadores mas costosos como FDs o memoria pueden tardar mas.
- Si se mata manualmente un analizador con `kill`, el monitor no lo reinicia. El resto de vistas sigue funcionando.
- La TUI esta pensada para terminales con tamano razonable. En terminales muy chicas puede recortar lineas.
- El analizador de sistema lee el resumen desde `Manager.dict` con `snapshot.get()`. Esa lectura individual es atomica en el proxy del Manager; el lock fuerte queda en el display y el agregador, que son los que dibujan y reemplazan vistas completas.

## 7. Como correr

Desde la carpeta del proyecto:

```bash
docker compose up --build
```

El contenedor se ejecuta con `tty: true` y `stdin_open: true` para que `curses` pueda recibir teclado.
Tambien se configura `pid: host` para que el monitor vea los procesos del Linux host y no solamente los procesos internos del contenedor.

Si se usa la version vieja del comando, tambien funciona:

```bash
docker-compose up --build
```

En algunas instalaciones de Docker Compose, `up` no reenvia bien las teclas a programas con TUI. Si la pantalla aparece pero no responde a `1`, `2`, `q`, etc., se puede abrir otra terminal y adjuntarse al contenedor:

```bash
docker attach monitor-procesos
```

No es un paso de instalacion: el monitor ya esta levantado con `docker compose up --build`, pero `attach` conecta el teclado directamente al TTY del contenedor.

### Como probar senales

Como el contenedor usa `pid: host`, el PID 1 dentro del contenedor no es el proceso del monitor. Por eso no hay que probar con `kill -HUP 1`.

Primero busco el PID real del monitor:

```bash
docker exec monitor-procesos pgrep -f "src.main"
```

Despues mando la senal a ese PID:

```bash
docker exec monitor-procesos kill -HUP <pid>
docker exec monitor-procesos kill -USR1 <pid>
docker exec monitor-procesos kill -USR2 <pid>
docker exec monitor-procesos kill -TERM <pid>
```

`SIGHUP` vuelve a leer `config.json`: intervalos, `filtro_comando`, `filtro_usuario` y `orden`. Si un intervalo del archivo queda por debajo del minimo de la consigna, el programa usa el minimo permitido para esa vista.

Para salir:

```text
q
```

o `Ctrl+C`, que dispara SIGINT y apaga los procesos hijos.

## 8. Como testear

Los tests son unitarios y verifican partes delicadas del parseo de `/proc` y la logica de keybindings de la TUI:

```bash
python -m unittest discover -s tests
```

En esta entrega no uso `pytest` para no agregar dependencias.