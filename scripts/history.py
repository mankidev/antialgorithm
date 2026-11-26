import json
import os
import shutil
import sys
import time


def state_dir():
    xdg = os.environ.get("XDG_STATE_HOME", "")
    if os.path.isabs(xdg):
        return xdg
    return os.path.join(os.path.expanduser("~"), ".local", "state")


def history_path():
    custom = os.environ.get("ANTIALGORITHM_HISTORY")
    if not custom:
        return os.path.join(state_dir(), "antialgorithm", "history.jsonl")
    custom = os.path.abspath(os.path.expanduser(custom))
    if os.path.isdir(custom):
        return os.path.join(custom, "history.jsonl")
    return custom


PATH = history_path()
OLD_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "history.jsonl")
LIMIT = 300
LOCK_WAIT = 15
LOCK_STALE = 10


def acquire(lock):
    deadline = time.monotonic() + LOCK_WAIT
    while time.monotonic() < deadline:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return
        except FileExistsError:
            pass
        except PermissionError:
            if os.name != "nt":
                raise
        try:
            if time.time() - os.path.getmtime(lock) > LOCK_STALE:
                os.remove(lock)
                continue
        except (FileNotFoundError, PermissionError):
            pass
        time.sleep(0.02)
    raise TimeoutError(f"could not create the lock file {lock} within {LOCK_WAIT} s")


def release(lock):
    try:
        os.remove(lock)
    except FileNotFoundError:
        pass


def replace(tmp):
    for attempt in range(50):
        try:
            os.replace(tmp, PATH)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


def migrate():
    if os.path.exists(PATH) or not os.path.exists(OLD_PATH):
        return
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    lock = PATH + ".lock"
    acquire(lock)
    try:
        if not os.path.exists(PATH):
            shutil.copyfile(OLD_PATH, PATH)
    finally:
        release(lock)


def read():
    try:
        with open(PATH, encoding="utf-8-sig", errors="replace", newline="") as f:
            lines = f.read().split("\n")
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def load(kind=None):
    try:
        migrate()
        rows = read()
    except OSError as e:
        print(f"could not read the history: {e}", file=sys.stderr)
        return []
    return [row for row in rows if kind is None or row.get("type") == kind]


def add(entry):
    entry = {"date": time.strftime("%Y-%m-%d"), **entry}
    migrate()
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    lock = PATH + ".lock"
    acquire(lock)
    try:
        rows = read()[-(LIMIT - 1):] + [entry]
        tmp = f"{PATH}.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            replace(tmp)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    finally:
        release(lock)
