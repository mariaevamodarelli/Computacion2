import os
import signal 
import time
from collections import Counter

try:
    import pwd
except ImportError:
    pwd = None


if hasattr(os, "sysconf"):
    CLK_TCK = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")
else:
    CLK_TCK = 100
    PAGE_SIZE = 4096


POLICIES = {
    0: "OTHER",
    1: "FIFO",
    2: "RR",
    3: "BATCH",
    5: "IDLE",
    6: "DEADLINE",
}


def read_text(path, default=""):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return default


def list_pids(proc_root="/proc"):
    try:
        return sorted(int(name) for name in os.listdir(proc_root) if name.isdigit())
    except OSError:
        return []


def parse_stat_line(line):
    """Parsea /proc/<pid>/stat respetando comandos con espacios."""
    if not line:
        return {}
    left = line.find("(")
    right = line.rfind(")")
    if left == -1 or right == -1:
        return {}

    pid = int(line[:left].strip())
    comm = line[left + 1:right]
    rest = line[right + 2:].split()
    fields = [str(pid), comm] + rest
    return {
        "pid": pid,
        "comm": comm,
        "state": fields[2] if len(fields) > 2 else "?",
        "ppid": int_or_none(fields, 3),
        "pgrp": int_or_none(fields, 4),
        "session": int_or_none(fields, 5),
        "minflt": int_or_zero(fields, 9),
        "cminflt": int_or_zero(fields, 10),
        "majflt": int_or_zero(fields, 11),
        "cmajflt": int_or_zero(fields, 12),
        "utime": int_or_zero(fields, 13),
        "stime": int_or_zero(fields, 14),
        "priority": int_or_zero(fields, 17),
        "nice": int_or_zero(fields, 18),
        "num_threads": int_or_zero(fields, 19),
        "rt_priority": int_or_zero(fields, 39),
        "policy": int_or_zero(fields, 40),
    }


def int_or_none(items, index):
    try:
        return int(items[index])
    except (IndexError, TypeError, ValueError):
        return None


def int_or_zero(items, index):
    value = int_or_none(items, index)
    return 0 if value is None else value


def read_stat(pid, proc_root="/proc"):
    return parse_stat_line(read_text(f"{proc_root}/{pid}/stat"))


def read_status(pid, proc_root="/proc"):
    status = {}
    for line in read_text(f"{proc_root}/{pid}/status").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        status[key.strip()] = value.strip()
    return status


def status_kb(status, key):
    value = status.get(key, "0 kB").split()[0]
    try:
        return int(value)
    except ValueError:
        return 0


def parse_uid_gid(status):
    uid = first_int(status.get("Uid", "0"))
    gid = first_int(status.get("Gid", "0"))
    try:
        if pwd is None:
            raise KeyError(uid)
        username = pwd.getpwuid(uid).pw_name
    except KeyError:
        username = str(uid)
    return uid, gid, username


def first_int(value):
    try:
        return int(value.split()[0])
    except (IndexError, ValueError, AttributeError):
        return 0


def read_cmdline(pid, proc_root="/proc"):
    raw = read_text(f"{proc_root}/{pid}/cmdline")
    cmd = raw.replace("\x00", " ").strip()
    if cmd:
        return cmd
    stat = read_stat(pid, proc_root)
    return f"[{stat.get('comm', pid)}]"


def cpu_percent(total_ticks, previous, key, now=None):
    now = time.monotonic() if now is None else now
    old_ticks, old_time = previous.get(key, (total_ticks, now))
    previous[key] = (total_ticks, now)
    elapsed = now - old_time
    if elapsed <= 0:
        return 0.0
    return max(0.0, 100.0 * (total_ticks - old_ticks) / CLK_TCK / elapsed)


def process_summary(pid, previous_cpu, proc_root="/proc"):
    stat = read_stat(pid, proc_root)
    if not stat:
        return None
    status = read_status(pid, proc_root)
    uid, gid, user = parse_uid_gid(status)
    total = stat.get("utime", 0) + stat.get("stime", 0)
    return {
        "pid": pid,
        "ppid": first_int(status.get("PPid", str(stat.get("ppid", 0)))),
        "uid": uid,
        "gid": gid,
        "usuario": user,
        "estado": stat.get("state", "?"),
        "cmd": read_cmdline(pid, proc_root),
        "cpu": round(cpu_percent(total, previous_cpu, pid), 1),
        "threads": first_int(status.get("Threads", str(stat.get("num_threads", 0)))),
        "rss_kb": status_kb(status, "VmRSS"),
    }


def memory_info(pid, proc_root="/proc"):
    stat = read_stat(pid, proc_root)
    status = read_status(pid, proc_root)
    if not stat or not status:
        return None
    return {
        "pid": pid,
        "vmsize_kb": status_kb(status, "VmSize"),
        "vmrss_kb": status_kb(status, "VmRSS"),
        "vmdata_kb": status_kb(status, "VmData"),
        "vmstk_kb": status_kb(status, "VmStk"),
        "vmexe_kb": status_kb(status, "VmExe"),
        "vmlib_kb": status_kb(status, "VmLib"),
        "vmhwm_kb": status_kb(status, "VmHWM"),
        "vmswap_kb": status_kb(status, "VmSwap"),
        "minor_faults": stat.get("minflt", 0) + stat.get("cminflt", 0),
        "major_faults": stat.get("majflt", 0) + stat.get("cmajflt", 0),
        "segmentos": map_segments(pid, proc_root),
    }


def map_segments(pid, proc_root="/proc"):
    groups = Counter()
    for line in read_text(f"{proc_root}/{pid}/maps").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        start_end = parts[0].split("-")
        if len(start_end) != 2:
            continue
        try:
            size_kb = (int(start_end[1], 16) - int(start_end[0], 16)) // 1024
        except ValueError:
            continue
        perms = parts[1]
        name = parts[-1] if len(parts) >= 6 else ""
        if "[heap]" in name:
            group = "heap"
        elif "[stack]" in name:
            group = "stack"
        elif "x" in perms:
            group = "text"
        elif "w" in perms:
            group = "data"
        else:
            group = "shared"
        groups[group] += size_kb
    return dict(groups)

def fd_info(pid, proc_root="/proc"):
    fd_dir = f"{proc_root}/{pid}/fd"
    try:
        names = sorted(os.listdir(fd_dir), key=lambda n: int(n))
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    items = []
    for name in names:
        path = f"{fd_dir}/{name}"
        try:
            target = os.readlink(path)
        except OSError:
            target = "(sin permiso o cerrado)"
        items.append({"fd": name, "tipo": infer_fd_type(target), "destino": target})
    return {"pid": pid, "fds": items, "cantidad": len(items)}


def infer_fd_type(target):
    lower = target.lower()
    if lower.startswith("socket:"):
        return "socket"
    if lower.startswith("pipe:"):
        return "pipe"
    if lower.startswith("/dev/pts") or lower.startswith("/dev/tty"):
        return "tty"
    if lower.startswith("anon_inode"):
        return "anon_inode"
    if target.startswith("/"):
        return "file"
    return "otro"

def thread_info(pid, previous_cpu, proc_root="/proc"):
    task_dir = f"{proc_root}/{pid}/task"
    try:
        tids = sorted(int(name) for name in os.listdir(task_dir) if name.isdigit())
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    threads = []
    for tid in tids:
        stat = parse_stat_line(read_text(f"{task_dir}/{tid}/stat"))
        status = {}
        for line in read_text(f"{task_dir}/{tid}/status").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                status[key.strip()] = value.strip()
        total = stat.get("utime", 0) + stat.get("stime", 0)
        threads.append({
            "tid": tid,
            "nombre": read_text(f"{task_dir}/{tid}/comm", str(tid)).strip(),
            "estado": stat.get("state", "?"),
            "cpu": round(cpu_percent(total, previous_cpu, (pid, tid)), 1),
            "voluntary_ctxt_switches": first_int(status.get("voluntary_ctxt_switches", "0")),
            "nonvoluntary_ctxt_switches": first_int(status.get("nonvoluntary_ctxt_switches", "0")),
        })
    return {"pid": pid, "threads": threads, "cantidad": len(threads)}


def signal_info(pid, proc_root="/proc"):
    status = read_status(pid, proc_root)
    if not status:
        return None
    keys = ["SigBlk", "SigIgn", "SigCgt", "SigPnd", "ShdPnd"]
    return {"pid": pid, **{key: decode_signal_mask(status.get(key, "0")) for key in keys}}


def decode_signal_mask(mask_hex):
    try:
        mask = int(mask_hex, 16)
    except (TypeError, ValueError):
        return []
    names = []
    for number in range(1, 65):
        if not (mask & (1 << (number - 1))):
            continue
        try:
            names.append(signal.Signals(number).name)
        except ValueError:
            names.append(f"SIG{number}")
    return names


def scheduling_info(pid, proc_root="/proc"):
    stat = read_stat(pid, proc_root)
    status = read_status(pid, proc_root)
    if not stat or not status:
        return None
    return {
        "pid": pid,
        "nice": stat.get("nice", 0),
        "priority": stat.get("priority", 0),
        "policy": POLICIES.get(stat.get("policy", 0), str(stat.get("policy", 0))),
        "rt_priority": stat.get("rt_priority", 0),
        "cpu_affinity": status.get("Cpus_allowed_list", "?"),
        "voluntary_ctxt_switches": first_int(status.get("voluntary_ctxt_switches", "0")),
        "nonvoluntary_ctxt_switches": first_int(status.get("nonvoluntary_ctxt_switches", "0")),
        "utime": stat.get("utime", 0),
        "stime": stat.get("stime", 0),
        "sid": stat.get("session"),
        "pgid": stat.get("pgrp"),
    }


def read_cpu_line(proc_root="/proc"):
    for line in read_text(f"{proc_root}/stat").splitlines():
        if line.startswith("cpu "):
            values = [int(part) for part in line.split()[1:]]
            return values
    return []


def cpu_global_percent(previous_cpu, proc_root="/proc"):
    values = read_cpu_line(proc_root)
    if not values:
        return {}
    old = previous_cpu.get("global", values)
    previous_cpu["global"] = values
    delta = [max(0, new - old_value) for new, old_value in zip(values, old)]
    total = sum(delta) or 1
    names = ["user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"]
    return {name: round(100.0 * delta[i] / total, 1) for i, name in enumerate(names) if i < len(delta)}


def system_info(previous_cpu, summaries=None, proc_root="/proc"):
    summaries = summaries or []
    states = Counter()
    total_threads = 0
    for pid in list_pids(proc_root):
        stat = read_stat(pid, proc_root)
        if not stat:
            continue
        states[stat.get("state", "?")] += 1
        total_threads += stat.get("num_threads", 0)

    meminfo = {}
    for line in read_text(f"{proc_root}/meminfo").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meminfo[key] = first_int(value)

    btime = 0
    for line in read_text(f"{proc_root}/stat").splitlines():
        if line.startswith("btime "):
            btime = first_int(line.replace("btime", "", 1))
            break

    uptime_parts = read_text(f"{proc_root}/uptime", "0 0").split()
    uptime = float(uptime_parts[0]) if uptime_parts else 0.0
    top_cpu = sorted(summaries, key=lambda p: p.get("cpu", 0), reverse=True)[:3]
    top_mem = sorted(summaries, key=lambda p: p.get("rss_kb", 0), reverse=True)[:3]
    return {
        "cpu": cpu_global_percent(previous_cpu, proc_root),
        "loadavg": read_text(f"{proc_root}/loadavg").strip(),
        "memoria": {
            "MemTotal": meminfo.get("MemTotal", 0),
            "MemFree": meminfo.get("MemFree", 0),
            "Buffers": meminfo.get("Buffers", 0),
            "Cached": meminfo.get("Cached", 0),
            "SwapTotal": meminfo.get("SwapTotal", 0),
            "SwapFree": meminfo.get("SwapFree", 0),
        },
        "procesos_totales": sum(states.values()),
        "procesos_por_estado": dict(states),
        "threads_totales": total_threads,
        "zombies": states.get("Z", 0),
        "boot_time": btime,
        "uptime": uptime,
        "top_cpu": top_cpu,
        "top_mem": top_mem,
    }