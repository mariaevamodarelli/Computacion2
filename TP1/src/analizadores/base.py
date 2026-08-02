import time


def analyzer_loop(name, task_queue, result_queue, interval_value, stop_event, analyze):
    """Loop comun para los analizadores.

    Cada proceso espera una lista de PIDs, analiza su dimension y publica
    el resultado al agregador usando una Queue.
    """
    last_run = 0.0
    latest_pids = []
    while not stop_event.is_set():
        try:
            while True:
                latest_pids = task_queue.get_nowait()
        except Exception:
            pass

        now = time.monotonic()
        interval = max(0.1, interval_value.value)
        if latest_pids and now - last_run >= interval:
            data = analyze(latest_pids)
            result_queue.put({"vista": name, "ts": time.time(), "data": data})
            last_run = now

        time.sleep(0.05)