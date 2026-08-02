import queue
import time


def run(result_queue, snapshot, lock, counters, stop_event):
    """Actualiza el snapshot global dentro de una seccion critica."""
    while not stop_event.is_set():
        try:
            message = result_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        vista = message["vista"]
        with lock:
            snapshot[vista] = {
                "data": message["data"],
                "ts": message.get("ts", time.time()),
            }
            counters[0] += 1