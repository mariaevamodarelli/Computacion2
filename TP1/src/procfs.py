import os
import time


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

