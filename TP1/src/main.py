import curses
import multiprocessing as mp
import sys
import time

from src import agregador, display, recolector
from src.analizadores import fds, memoria, resumen, scheduling, senales, sistema, threads
from src.senales import SignalController, load_config


ANALYZERS = {
    "resumen": resumen.run,
    "memoria": memoria.run,
    "fds": fds.run,
    "threads": threads.run,
    "senales": senales.run,
    "scheduling": scheduling.run,
    "sistema": sistema.run,
}

DEFAULT_INTERVALS = {
    "resumen": 2.0,
    "memoria": 3.0,
    "fds": 5.0,
    "threads": 2.0,
    "senales": 10.0,
    "scheduling": 10.0,
    "sistema": 2.0,
}


def build_intervals(config):
    values = {}
    configured = config.get("intervalos", {})
    for name, default in DEFAULT_INTERVALS.items():
        values[name] = mp.Value("d", float(configured.get(name, default)))
    return values


def start_processes(snapshot, lock, counters, intervals, stop_event):
    result_queue = mp.Queue()
    task_queues = {name: mp.Queue(maxsize=3) for name in ANALYZERS}
    heartbeat_parent, heartbeat_child = mp.Pipe(duplex=False)
    processes = []

    collector = mp.Process(
        target=recolector.run,
        name="recolector",
        args=(task_queues, stop_event, heartbeat_child),
    )
    processes.append(collector)

    aggregator = mp.Process(
        target=agregador.run,
        name="agregador",
        args=(result_queue, snapshot, lock, counters, stop_event),
    )
    processes.append(aggregator)

    for name, target in ANALYZERS.items():
        if name == "sistema":
            args = (task_queues[name], result_queue, intervals[name], stop_event, snapshot)
        else:
            args = (task_queues[name], result_queue, intervals[name], stop_event)
        processes.append(mp.Process(target=target, name=f"analizador-{name}", args=args))

    for process in processes:
        process.start()

    return processes, heartbeat_parent


def stop_processes(processes, stop_event):
    stop_event.set()
    for process in processes:
        process.join(timeout=2)
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)


def main():
    mp.set_start_method("fork")
    config = load_config()
    with mp.Manager() as manager:
        snapshot = manager.dict({
            "resumen": {"data": [], "ts": time.time()},
            "memoria": {"data": {}, "ts": time.time()},
            "fds": {"data": {}, "ts": time.time()},
            "threads": {"data": {}, "ts": time.time()},
            "senales": {"data": {}, "ts": time.time()},
            "scheduling": {"data": {}, "ts": time.time()},
            "sistema": {"data": {}, "ts": time.time()},
        })
        lock = manager.RLock()
        stop_event = mp.Event()
        intervals = build_intervals(config)
        verbose_value = mp.Value("i", 0)
        counters = mp.Array("i", [0, 0, 0])
        processes, heartbeat_conn = start_processes(snapshot, lock, counters, intervals, stop_event)
        signal_controller = SignalController()
        try:
            curses.wrapper(
                display.run,
                snapshot,
                lock,
                intervals,
                stop_event,
                signal_controller,
                verbose_value,
                counters,
                heartbeat_conn,
            )
        finally:
            stop_processes(processes, stop_event)
            signal_controller.close()


if __name__ == "__main__":
    if sys.platform != "linux":
        print("Este TP esta pensado para correr en Linux o dentro del contenedor Docker.")
        sys.exit(1)
    main()
