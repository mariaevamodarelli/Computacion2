import curses
import signal
import time

from src.senales import dump_snapshot, load_config


VIEWS = [
    ("resumen", "Resumen", "r", 0.5),
    ("memoria", "Memoria", "m", 1.0),
    ("fds", "FDs", "f", 2.0),
    ("threads", "Threads", "t", 0.5),
    ("senales", "Senales", "s", 5.0),
    ("scheduling", "Scheduling", "p", 5.0),
    ("sistema", "Sistema", "g", 1.0),
]

MIN_INTERVALS = {name: min_interval for name, _title, _letter, min_interval in VIEWS}

VIEW_BY_KEY = {}
for index, (name, _title, letter, _min_interval) in enumerate(VIEWS):
    VIEW_BY_KEY[str(index + 1)] = index
    VIEW_BY_KEY[letter] = index


SORTS = ["cpu", "rss", "pid"]


class DisplayState:
    def __init__(self):
        self.view_index = 0
        self.selected = 0
        self.pinned_pid = None
        self.filter_cmd = ""
        self.filter_user = ""
        self.sort_index = 0
        self.show_help = False
        self.message = ""
        self.last_heartbeat = {}
        self.last_rows = []

    @property
    def view_name(self):
        return VIEWS[self.view_index][0]

    @property
    def sort_name(self):
        return SORTS[self.sort_index]


def run(stdscr, snapshot, lock, intervals, stop_event, signal_controller, verbose_value, counters, heartbeat_conn):
    curses.curs_set(0)
    stdscr.timeout(100)
    stdscr.keypad(True)
    state = DisplayState()
    apply_config(intervals, state)
    while not stop_event.is_set():
        read_heartbeat(heartbeat_conn, state)
        handle_signals(stdscr, snapshot, intervals, stop_event, signal_controller, verbose_value, counters, state)
        handle_key(stdscr, state, intervals, stop_event)
        draw(stdscr, snapshot, lock, intervals, verbose_value, counters, state)
        time.sleep(0.1)


def read_heartbeat(conn, state):
    if conn is None:
        return
    try:
        while conn.poll():
            state.last_heartbeat = conn.recv()
    except (EOFError, OSError):
        return


def handle_signals(stdscr, snapshot, intervals, stop_event, controller, verbose_value, counters, state):
    for signum in controller.read_pending():
        if signum in (signal.SIGINT, signal.SIGTERM):
            state.message = "Saliendo por senal..."
            stop_event.set()
        elif signum == signal.SIGHUP:
            apply_config(intervals, state)
            counters[1] += 1
            state.message = "Config recargada desde config.json"
        elif signum == signal.SIGUSR1:
            path = dump_snapshot(snapshot)
            state.message = f"Snapshot guardado en {path}"
        elif signum == signal.SIGUSR2:
            verbose_value.value = 0 if verbose_value.value else 1
            counters[2] += 1
            state.message = "Verbose ON" if verbose_value.value else "Verbose OFF"
        elif hasattr(signal, "SIGWINCH") and signum == signal.SIGWINCH:
            try:
                curses.resize_term(0, 0)
                stdscr.clear()
            except curses.error:
                state.message = "Resize detectado, no se pudo ajustar la pantalla"
            else:
                state.message = "Pantalla redimensionada"


def apply_config(intervals, state):
    config = load_config()
    for name, value in config.get("intervalos", {}).items():
        if name in intervals:
            intervals[name].value = clamp_interval(name, value)
    state.filter_cmd = str(config.get("filtro_comando", state.filter_cmd)).strip()
    state.filter_user = str(config.get("filtro_usuario", state.filter_user)).strip()
    orden = str(config.get("orden", state.sort_name)).lower()
    if orden in SORTS:
        state.sort_index = SORTS.index(orden)
    state.selected = 0


def handle_key(stdscr, state, intervals, stop_event):
    key = stdscr.getch()
    if key == -1:
        return
    if key in (ord("q"), ord("Q")):
        stop_event.set()
    elif key in (ord("h"), ord("?")):
        state.show_help = not state.show_help
        state.message = "Ayuda visible" if state.show_help else "Ayuda oculta"
    elif key == curses.KEY_UP:
        state.selected = max(0, state.selected - 1)
        state.message = "Seleccion anterior"
    elif key == curses.KEY_DOWN:
        state.selected += 1
        state.message = "Seleccion siguiente"
    elif key in (10, 13):
        state.pinned_pid = current_selected_pid(stdscr, state)
        state.message = f"PID fijado: {state.pinned_pid}" if state.pinned_pid else "No hay PID para fijar"
    elif key == ord("/"):
        state.filter_cmd = prompt(stdscr, "Filtrar comando: ")
        state.selected = 0
        state.message = f"Filtro comando: {state.filter_cmd or '(sin filtro)'}"
    elif key in (ord("u"), ord("U")):
        state.filter_user = prompt(stdscr, "Filtrar usuario: ")
        state.selected = 0
        state.message = f"Filtro usuario: {state.filter_user or '(sin filtro)'}"
    elif key in (ord("c"), ord("C")):
        state.sort_index = (state.sort_index + 1) % len(SORTS)
        state.message = f"Orden: {state.sort_name}"
    elif key == ord("+"):
        change_interval(state.view_name, intervals, -0.5)
        state.message = f"Intervalo {state.view_name}: {intervals[state.view_name].value:.1f}s"
    elif key == ord("-"):
        change_interval(state.view_name, intervals, 0.5)
        state.message = f"Intervalo {state.view_name}: {intervals[state.view_name].value:.1f}s"
    else:
        char = chr(key).lower() if 0 <= key < 256 else ""
        if char in VIEW_BY_KEY:
            state.view_index = VIEW_BY_KEY[char]
            _name, title, _letter, _min_interval = VIEWS[state.view_index]
            state.message = f"Vista activa: {title}"


def prompt(stdscr, label):
    curses.echo()
    h, width = stdscr.getmaxyx()
    add_line(stdscr, h - 1, 0, " " * max(1, width - 1), width)
    add_line(stdscr, h - 1, 0, label, width)
    stdscr.timeout(-1)
    max_len = max(1, width - len(label) - 1)
    value = stdscr.getstr(h - 1, min(len(label), width - 1), max_len).decode("utf-8", errors="replace")
    stdscr.timeout(100)
    curses.noecho()
    return value.strip()


def change_interval(view_name, intervals, delta):
    intervals[view_name].value = clamp_interval(view_name, intervals[view_name].value + delta)


def clamp_interval(view_name, value):
    min_interval = MIN_INTERVALS[view_name]
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return min_interval
    return max(min_interval, numeric_value)


def current_selected_pid(_stdscr, state):
    rows = state.last_rows
    if not rows:
        return None
    index = min(state.selected, len(rows) - 1)
    return rows[index]["pid"]


def draw(stdscr, snapshot, lock, intervals, verbose_value, counters, state):
    with lock:
        local = {key: dict(value) for key, value in snapshot.items()}
    rows = process_rows(local, state)
    state.last_rows = rows
    state.selected = min(state.selected, max(0, len(rows) - 1))
    active_pid = state.pinned_pid or (rows[state.selected]["pid"] if rows else None)

    stdscr.erase()
    height, width = stdscr.getmaxyx()
    draw_header(stdscr, width, intervals, counters, state)
    draw_process_table(stdscr, rows, state, height, width)
    detail_top = max(8, min(height - 10, height // 2))
    draw_detail(stdscr, local, active_pid, detail_top, height, width, state, bool(verbose_value.value))
    stdscr.refresh()


def draw_header(stdscr, width, intervals, counters, state):
    name, title, _letter, _min_interval = VIEWS[state.view_index]
    heartbeat = state.last_heartbeat.get("pids", "?")
    text = (
        f"Monitor /proc | Vista {state.view_index + 1}: {title} | "
        f"intervalo {intervals[name].value:.1f}s | orden {state.sort_name} | "
        f"pids {heartbeat} | snapshots {counters[0]}"
    )
    add_line(stdscr, 0, 0, text, width, curses.A_REVERSE)
    filters = f"Filtro cmd='{state.filter_cmd}' usuario='{state.filter_user}' pin={state.pinned_pid or '-'}"
    add_line(stdscr, 1, 0, filters, width)
    if state.message:
        add_line(stdscr, 2, 0, state.message, width)
    if state.show_help:
        help_text = "1-7/r/m/f/t/s/p/g vistas | flechas navegar | Enter pin | / cmd | u usuario | c orden | +/- intervalo | q salir"
        add_line(stdscr, 3, 0, help_text, width)


def draw_process_table(stdscr, rows, state, height, width):
    top = 4
    bottom = max(top + 1, min(height // 2, height - 8))
    visible = max(1, bottom - top - 1)
    offset = max(0, state.selected - visible + 1)
    page = rows[offset: offset + visible]
    columns = [
        ("PID", 7),
        ("PPID", 7),
        ("UID", 6),
        ("GID", 6),
        ("USUARIO", 12),
        ("ST", 4),
        ("CPU%", 7),
        ("RSS KB", 10),
        ("TH", 5),
        ("COMANDO", 80),
    ]
    draw_table_header(stdscr, top, columns, width)
    for visible_index, proc in enumerate(page):
        screen_row = top + 1 + visible_index
        real_index = offset + visible_index
        pid_text = f">{proc['pid']}" if real_index == state.selected else str(proc["pid"])
        values = [
            pid_text,
            proc.get("ppid", 0),
            proc.get("uid", 0),
            proc.get("gid", 0),
            proc.get("usuario", ""),
            proc.get("estado", "?"),
            f"{proc.get('cpu', 0):.1f}",
            proc.get("rss_kb", 0),
            proc.get("threads", 0),
            proc.get("cmd", ""),
        ]
        attr = curses.A_REVERSE if proc.get("pid") == state.pinned_pid else 0
        draw_table_row(stdscr, screen_row, columns, values, width, attr)


def process_rows(snapshot, state):
    rows = list(snapshot.get("resumen", {}).get("data", []))
    if state.filter_cmd:
        rows = [p for p in rows if state.filter_cmd.lower() in p.get("cmd", "").lower()]
    if state.filter_user:
        rows = [p for p in rows if state.filter_user.lower() in p.get("usuario", "").lower()]
    if state.sort_name == "cpu":
        rows.sort(key=lambda p: p.get("cpu", 0), reverse=True)
    elif state.sort_name == "rss":
        rows.sort(key=lambda p: p.get("rss_kb", 0), reverse=True)
    else:
        rows.sort(key=lambda p: p.get("pid", 0))
    return rows


def draw_detail(stdscr, snapshot, pid, top, height, width, state, verbose):
    name, title, _letter, _min_interval = VIEWS[state.view_index]
    add_line(stdscr, top, 0, "-" * max(1, width - 1), width)
    add_line(stdscr, top + 1, 0, f"Tabla {title} | PID {pid or '-'}", width, curses.A_BOLD)
    if pid is None:
        add_line(stdscr, top + 2, 0, "Sin procesos para mostrar.", width)
        return
    data = snapshot.get(name, {}).get("data", {})
    tables = detail_tables(name, data, pid, verbose)
    row = top + 2
    max_row = height - 1
    for columns, rows in tables:
        if row >= max_row:
            break
        draw_table_header(stdscr, row, columns, width)
        row += 1
        for values in rows:
            if row >= max_row:
                break
            draw_table_row(stdscr, row, columns, values, width)
            row += 1
        row += 1


def draw_table_header(stdscr, y, columns, width):
    labels = [label for label, _size in columns]
    draw_table_row(stdscr, y, columns, labels, width, curses.A_BOLD)


def draw_table_row(stdscr, y, columns, values, width, attr=0):
    parts = []
    for index, (_label, size) in enumerate(columns):
        value = str(values[index]) if index < len(values) else ""
        parts.append(value[:size].ljust(size))
    add_line(stdscr, y, 0, " | ".join(parts), width, attr)


def detail_tables(name, data, pid, verbose):
    if name == "resumen":
        proc = next((p for p in data if p.get("pid") == pid), {})
        return [(
            [("PID", 7), ("PPID", 7), ("UID", 6), ("GID", 6), ("USUARIO", 12), ("EST", 4), ("CPU%", 7), ("THREADS", 8), ("COMANDO", 80)],
            [[
                proc.get("pid", ""),
                proc.get("ppid", ""),
                proc.get("uid", ""),
                proc.get("gid", ""),
                proc.get("usuario", ""),
                proc.get("estado", ""),
                proc.get("cpu", ""),
                proc.get("threads", ""),
                proc.get("cmd", ""),
            ]],
        )]
    if name == "memoria":
        item = data.get(pid, {})
        segmentos = item.get("segmentos", {})
        return [
            (
                [("VmSize", 9), ("VmRSS", 9), ("VmData", 9), ("VmStk", 9), ("VmExe", 9), ("VmLib", 9), ("VmHWM", 9), ("VmSwap", 9)],
                [[
                    item.get("vmsize_kb", 0),
                    item.get("vmrss_kb", 0),
                    item.get("vmdata_kb", 0),
                    item.get("vmstk_kb", 0),
                    item.get("vmexe_kb", 0),
                    item.get("vmlib_kb", 0),
                    item.get("vmhwm_kb", 0),
                    item.get("vmswap_kb", 0),
                ]],
            ),
            (
                [("minflt", 10), ("cminflt", 10), ("majflt", 10), ("cmajflt", 10), ("minor total", 12), ("major total", 12)],
                [[
                    item.get("minflt", 0),
                    item.get("cminflt", 0),
                    item.get("majflt", 0),
                    item.get("cmajflt", 0),
                    item.get("minor_faults", 0),
                    item.get("major_faults", 0),
                ]],
            ),
            (
                [("SEGMENTO", 14), ("KB", 12)],
                [[key, value] for key, value in sorted(segmentos.items())] or [["sin datos", 0]],
            ),
        ]
    if name == "fds":
        item = data.get(pid, {})
        limit = 30 if verbose else 12
        return [(
            [("FD", 6), ("TIPO", 12), ("DESTINO", 100)],
            [[fd.get("fd", ""), fd.get("tipo", ""), fd.get("destino", "")] for fd in item.get("fds", [])[:limit]] or [["-", "-", "sin FDs visibles"]],
        )]
    if name == "threads":
        item = data.get(pid, {})
        return [(
            [("TID", 8), ("EST", 4), ("CPU%", 7), ("VOL_CTX", 10), ("INVOL_CTX", 11), ("NOMBRE", 40)],
            [[
                th.get("tid", ""),
                th.get("estado", ""),
                th.get("cpu", ""),
                th.get("voluntary_ctxt_switches", ""),
                th.get("nonvoluntary_ctxt_switches", ""),
                th.get("nombre", ""),
            ] for th in item.get("threads", [])[:30]] or [["-", "-", "-", "-", "-", "sin threads visibles"]],
        )]
    if name == "senales":
        item = data.get(pid, {})
        rows = []
        labels = {
            "SigBlk": "bloqueadas",
            "SigIgn": "ignoradas",
            "SigCgt": "con handler",
            "SigPnd": "pendientes proc",
            "ShdPnd": "pendientes grupo",
        }
        for key in ["SigBlk", "SigIgn", "SigCgt", "SigPnd", "ShdPnd"]:
            value = ", ".join(item.get(key, [])) or "-"
            rows.append([key, labels[key], value])
        return [(
            [("CAMPO", 8), ("SIGNIFICADO", 18), ("SENALES DECODIFICADAS", 100)],
            rows,
        )]
    if name == "scheduling":
        item = data.get(pid, {})
        return [(
            [("NICE", 7), ("PRIORITY", 10), ("POLICY", 10), ("RT_PRIO", 9), ("AFFINITY", 12), ("VOL_CTX", 10), ("INVOL_CTX", 11), ("UTIME", 9), ("STIME", 9), ("SID", 8), ("PGID", 8)],
            [[
                item.get("nice", ""),
                item.get("priority", ""),
                item.get("policy", ""),
                item.get("rt_priority", ""),
                item.get("cpu_affinity", ""),
                item.get("voluntary_ctxt_switches", ""),
                item.get("nonvoluntary_ctxt_switches", ""),
                item.get("utime", ""),
                item.get("stime", ""),
                item.get("sid", ""),
                item.get("pgid", ""),
            ]],
        )]
    if name == "sistema":
        info = data
        cpu = info.get("cpu", {})
        mem = info.get("memoria", {})
        return [
            (
                [("USER", 8), ("SYSTEM", 8), ("IDLE", 8), ("IOWAIT", 8), ("LOADAVG", 24)],
                [[cpu.get("user", 0), cpu.get("system", 0), cpu.get("idle", 0), cpu.get("iowait", 0), info.get("loadavg", "")]],
            ),
            (
                [("MemTotal", 10), ("MemFree", 10), ("Buffers", 10), ("Cached", 10), ("SwapTotal", 10), ("SwapFree", 10)],
                [[mem.get("MemTotal", 0), mem.get("MemFree", 0), mem.get("Buffers", 0), mem.get("Cached", 0), mem.get("SwapTotal", 0), mem.get("SwapFree", 0)]],
            ),
            (
                [("PROC", 8), ("THREADS", 9), ("ZOMBIES", 9), ("ESTADOS", 35), ("UPTIME", 12), ("BOOT", 12)],
                [[info.get("procesos_totales", 0), info.get("threads_totales", 0), info.get("zombies", 0), info.get("procesos_por_estado", {}), f"{info.get('uptime', 0):.0f}s", info.get("boot_time", 0)]],
            ),
            (
                [("TOP CPU PID", 12), ("CPU%", 8), ("COMANDO", 80)],
                [[p.get("pid", ""), p.get("cpu", ""), p.get("cmd", "")] for p in info.get("top_cpu", [])] or [["-", "-", "-"]],
            ),
            (
                [("TOP MEM PID", 12), ("RSS KB", 10), ("COMANDO", 80)],
                [[p.get("pid", ""), p.get("rss_kb", ""), p.get("cmd", "")] for p in info.get("top_mem", [])] or [["-", "-", "-"]],
            ),
        ]
    return [([("INFO", 80)], [["Sin datos todavia."]])]


def add_line(stdscr, y, x, text, width, attr=0):
    if y < 0:
        return
    try:
        stdscr.addstr(y, x, str(text)[: max(1, width - x - 1)], attr)
    except curses.error:
        return
