from src.analizadores.base import analyzer_loop
from src.procfs import thread_info


def run(task_queue, result_queue, interval_value, stop_event):
    previous_cpu = {}

    def analyze(pids):
        data = {}
        for pid in pids:
            item = thread_info(pid, previous_cpu)
            if item:
                data[pid] = item
        return data

    analyzer_loop("threads", task_queue, result_queue, interval_value, stop_event, analyze)
