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
    stdscr.nodelay(True)
    stdscr.keypad(True)
    state = DisplayState()
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
        pass


def handle_signals(stdscr, snapshot, intervals, stop_event, controller, verbose_value, counters, state):
    for signum in controller.read_pending():
        if signum in (signal.SIGINT, signal.SIGTERM):
            state.message = "Saliendo por senal..."
            stop_event.set()
        elif signum == signal.SIGHUP:
            apply_config(intervals)
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
            except curses.error:
                pass
            stdscr.clear()
            state.message = "Pantalla redimensionada"


def apply_config(intervals):
    config = load_config()
    for name, value in config.get("intervalos", {}).items():
        if name in intervals:
            intervals[name].value = float(value)


def handle_key(stdscr, state, intervals, stop_event):
    key = stdscr.getch()
    if key == -1:
        return
    if key in (ord("q"), ord("Q")):
        stop_event.set()
    elif key in (ord("h"), ord("?")):
        state.show_help = not state.show_help
    elif key == curses.KEY_UP:
        state.selected = max(0, state.selected - 1)
    elif key == curses.KEY_DOWN:
        state.selected += 1
    elif key in (10, 13):
        state.pinned_pid = current_selected_pid(stdscr, state)
    elif key == ord("/"):
        state.filter_cmd = prompt(stdscr, "Filtrar comando: ")
        state.selected = 0
    elif key in (ord("u"), ord("U")):
        state.filter_user = prompt(stdscr, "Filtrar usuario: ")
        state.selected = 0
    elif key in (ord("c"), ord("C")):
        state.sort_index = (state.sort_index + 1) % len(SORTS)
    elif key == ord("+"):
        change_interval(state.view_name, intervals, -0.5)
    elif key == ord("-"):
        change_interval(state.view_name, intervals, 0.5)
    else:
        char = chr(key).lower() if 0 <= key < 256 else ""
        if char in VIEW_BY_KEY:
            state.view_index = VIEW_BY_KEY[char]


def prompt(stdscr, label):
    curses.echo()
    h, w = stdscr.getmaxyx()
    try:
        stdscr.addstr(h - 1, 0, " " * max(1, w - 1))
        stdscr.addstr(h - 1, 0, label[: max(1, w - 1)])
    except curses.error:
        pass
    stdscr.nodelay(False)
    value = stdscr.getstr(h - 1, min(len(label), max(0, w - 1)), 40).decode("utf-8", errors="replace")
    stdscr.nodelay(True)
    curses.noecho()
    return value.strip()


def change_interval(view_name, intervals, delta):
    min_interval = next(v[3] for v in VIEWS if v[0] == view_name)
    intervals[view_name].value = max(min_interval, intervals[view_name].value + delta)


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
    add_line(stdscr, top, 0, "PID    PPID   USER       ST  CPU%    RSS KB  TH  COMANDO", width, curses.A_BOLD)
    for screen_row, proc in enumerate(rows[: max(0, bottom - top - 1)], start=top + 1):
        marker = ">" if screen_row - top - 1 == state.selected else " "
        line = (
            f"{marker}{proc['pid']:<6} {proc.get('ppid', 0):<6} {proc.get('usuario', '')[:10]:<10} "
            f"{proc.get('estado', '?'):<2} {proc.get('cpu', 0):>5.1f} "
            f"{proc.get('rss_kb', 0):>9} {proc.get('threads', 0):>3} "
            f"{proc.get('cmd', '')}"
        )
        attr = curses.A_REVERSE if proc.get("pid") == state.pinned_pid else 0
        add_line(stdscr, screen_row, 0, line, width, attr)


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
    add_line(stdscr, top + 1, 0, f"Detalle {title} | PID {pid or '-'}", width, curses.A_BOLD)
    if pid is None:
        add_line(stdscr, top + 2, 0, "Sin procesos para mostrar.", width)
        return
    data = snapshot.get(name, {}).get("data", {})
    lines = detail_lines(name, data, pid, snapshot, verbose)
    for index, line in enumerate(lines[: max(0, height - top - 3)], start=top + 2):
        add_line(stdscr, index, 0, line, width)


def detail_lines(name, data, pid, snapshot, verbose):
    if name == "resumen":
        proc = next((p for p in data if p.get("pid") == pid), {})
        return [f"{key}: {value}" for key, value in proc.items()]
    if name == "memoria":
        item = data.get(pid, {})
        segmentos = item.get("segmentos", {})
        return [
            f"VmSize={item.get('vmsize_kb', 0)} KB VmRSS={item.get('vmrss_kb', 0)} KB VmHWM={item.get('vmhwm_kb', 0)} KB VmSwap={item.get('vmswap_kb', 0)} KB",
            f"VmData={item.get('vmdata_kb', 0)} KB VmStk={item.get('vmstk_kb', 0)} KB VmExe={item.get('vmexe_kb', 0)} KB VmLib={item.get('vmlib_kb', 0)} KB",
            f"Minor faults={item.get('minor_faults', 0)} Major faults={item.get('major_faults', 0)}",
            "Segmentos: " + ", ".join(f"{k}={v} KB" for k, v in segmentos.items()),
        ]
    if name == "fds":
        item = data.get(pid, {})
        limit = 30 if verbose else 10
        lines = [f"FDs abiertos: {item.get('cantidad', 0)}"]
        for fd in item.get("fds", [])[:limit]:
            lines.append(f"{fd['fd']:<4} {fd['tipo']:<10} {fd['destino']}")
        return lines
    if name == "threads":
        item = data.get(pid, {})
        lines = [f"Threads: {item.get('cantidad', 0)}", "TID      ST  CPU%  VOL_CTX  INVOL_CTX  NOMBRE"]
        for th in item.get("threads", [])[:30]:
            lines.append(
                f"{th['tid']:<8} {th['estado']:<2} {th['cpu']:>5.1f} "
                f"{th['voluntary_ctxt_switches']:>8} {th['nonvoluntary_ctxt_switches']:>10}  {th['nombre']}"
            )
        return lines
    if name == "senales":
        item = data.get(pid, {})
        return [f"{key}: {', '.join(value) if value else '-'}" for key, value in item.items() if key != "pid"]
    if name == "scheduling":
        item = data.get(pid, {})
        return [f"{key}: {value}" for key, value in item.items()]
    if name == "sistema":
        info = data
        mem = info.get("memoria", {})
        lines = [
            "CPU: " + ", ".join(f"{k}={v}%" for k, v in info.get("cpu", {}).items()),
            f"Load average: {info.get('loadavg', '')}",
            "Memoria: " + ", ".join(f"{k}={v} KB" for k, v in mem.items()),
            f"Procesos={info.get('procesos_totales', 0)} Threads={info.get('threads_totales', 0)} Zombies={info.get('zombies', 0)}",
            f"Estados: {info.get('procesos_por_estado', {})}",
            f"Uptime={info.get('uptime', 0):.0f}s Boot time={info.get('boot_time', 0)}",
            "Top CPU:",
        ]
        lines.extend(format_top(info.get("top_cpu", []), "cpu"))
        lines.append("Top memoria:")
        lines.extend(format_top(info.get("top_mem", []), "rss_kb"))
        return lines
    return ["Sin datos todavia."]


def format_top(items, key):
    return [f"  PID {p.get('pid')} {key}={p.get(key, 0)} {p.get('cmd', '')[:60]}" for p in items]


def add_line(stdscr, y, x, text, width, attr=0):
    if y < 0:
        return
    try:
        stdscr.addstr(y, x, str(text)[: max(1, width - x - 1)], attr)
    except curses.error:
        pass
