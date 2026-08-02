from src.analizadores.base import analyzer_loop
from src.procfs import process_summary


def run(task_queue, result_queue, interval_value, stop_event):
    previous_cpu = {}

    def analyze(pids):
        rows = []
        for pid in pids:
            item = process_summary(pid, previous_cpu)
            if item:
                rows.append(item)
        return rows

    analyzer_loop("resumen", task_queue, result_queue, interval_value, stop_event, analyze)
