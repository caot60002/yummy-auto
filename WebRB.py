# -*- coding: utf-8 -*-
"""
Reconstructed source for `WebRB.py` (`__main__`) from `WebRB.dll` (Nuitka Commercial Python 3.11).
Version: 0.5.7
"""

import asyncio
import atexit
import base64
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import ctypes
from ctypes import byref, wintypes
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import faulthandler
import gc
import gzip
import hashlib
from io import BytesIO
import json
import logging
import mss
import msvcrt
import os
from pathlib import Path
import platform
import psutil
import pygetwindow as gw
import queue
import random
import re
import secrets
import shutil
import socket
import ssl
import string
import subprocess
import sys
import threading
import time
import traceback
from types import SimpleNamespace
import urllib
import urllib.parse
from urllib.parse import parse_qs, urlparse
import uuid
import warnings
import zipfile

import aiohttp
from colorama import Fore, init
import keyring
from nacl.exceptions import CryptoError
from nacl.public import Box, PrivateKey, PublicKey
from nacl.signing import SigningKey
from nacl.utils import random as nacl_random
from PIL import ImageGrab
import requests
from urllib3.exceptions import InsecureRequestWarning
import win32con
import win32gui
import win32process
import winreg as reg

# Optional Cryptography / PyCryptodome
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
except ImportError:
    try:
        from Cryptodome.Cipher import AES
        from Cryptodome.Util.Padding import pad, unpad
    except ImportError:
        AES = None
        pad = None
        unpad = None

# Custom modules
from roblox_private_server import should_fetch_auto_private_server
from roblox_webview2_launch import launch_with_webview2_env
import solve_captcha as solve_captcha_module
from solve_captcha import solve_captcha
from solve_in_app import solve_captcha as solve_in_app_captcha
from startup_cookie_prefill import (
    apply_private_server_lines,
    build_prefill_status,
    publish_valid_cookie_snapshot,
)
import yummypack

version = "0.5.7"
ROBLOX_API_USER_AGENT = (
    "mozilla/5.0 (macintosh; intel Mac OS X 10_15_4) AppleWebKit/605.1.15 "
    "(KHTML,like Gecko) mobile 9B176 ROBLOX iOS App 2.445.410643 "
    "Hybrid RobloxApp/2.445.410643 (GlobasDist;AppleAppStore)"
)


def _roblox_api_headers(headers=None):
    merged = {
        "User-Agent": ROBLOX_API_USER_AGENT,
        "Accept": "application/json",
    }
    if headers:
        merged.update(headers)
    return merged


def _get_compiled_containing_dir():
    try:
        return os.path.dirname(os.path.abspath(sys.argv[0]))
    except Exception:
        return os.getcwd()


def _get_program_path():
    try:
        if getattr(sys, "frozen", False):
            return sys.executable
        return os.path.abspath(sys.argv[0])
    except Exception:
        return sys.executable or "WebRB.exe"


def _get_runtime_dir():
    containing_dir = _get_compiled_containing_dir()
    program_path = _get_program_path()
    if containing_dir and os.path.isdir(containing_dir):
        return containing_dir
    if program_path:
        return os.path.dirname(os.path.abspath(program_path))
    return os.getcwd()


PROGRAM_PATH = _get_program_path()
RUNTIME_DIR = _get_runtime_dir()
try:
    os.chdir(RUNTIME_DIR)
except Exception:
    pass

_DLL_DIRECTORY_HANDLES = []


def _prime_windows_dll_search_paths():
    seen = set()
    candidates = [
        RUNTIME_DIR,
        os.path.join(RUNTIME_DIR, "lib"),
        os.path.join(RUNTIME_DIR, "bin"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Roblox", "bin"),
    ]
    for raw_path in candidates:
        if not raw_path or not os.path.isdir(raw_path):
            continue
        normalized = os.path.normpath(os.path.abspath(raw_path))
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        try:
            handle = ctypes.windll.kernel32.AddDllDirectory(ctypes.c_wchar_p(normalized))
            if handle:
                _DLL_DIRECTORY_HANDLES.append(handle)
        except Exception:
            pass


_prime_windows_dll_search_paths()

warnings.simplefilter("ignore", InsecureRequestWarning)
os.system("cls")

for _lg_name in (
    "websockets.protocol",
    "websockets.server",
    "websockets.client",
    "websockets.legacy.protocol",
    "asyncio",
):
    lg = logging.getLogger(_lg_name)
    if "legacy" in _lg_name:
        lg.setLevel(logging.CRITICAL)
    elif "asyncio" in _lg_name:
        lg.setLevel(logging.ERROR)
    else:
        lg.setLevel(logging.WARNING)

init(autoreset=True)
os.makedirs("log", exist_ok=True)
logging.basicConfig(
    filename="log/log.txt",
    level=logging.ERROR,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

CONSOLE_LOCK = threading.RLock()
_RAW_PRINT = print
_RAW_TRACEBACK_PRINT_EXC = traceback.print_exc
_CONSOLE_OUTPUT_DISABLED = False


def _is_console_write_broken(exc):
    text = str(exc).lower()
    return any(
        m in text
        for m in (
            "handle is invalid",
            "invalid parameter",
            "broken pipe",
            "i/o operation on closed file",
        )
    )


def _is_runtime_shutdown_exception(exc):
    text = str(exc).lower()
    markers = (
        "shutting down",
        "interpreter shutdown",
        "sys.meta_path is none",
        "cannot schedule new futures",
        "event loop is closed",
    )
    return any(marker in text for marker in markers)


def _disable_console_output(exc=None):
    global _CONSOLE_OUTPUT_DISABLED
    _CONSOLE_OUTPUT_DISABLED = True


def _clear_console():
    if _CONSOLE_OUTPUT_DISABLED:
        return
    try:
        os.system("cls")
    except Exception as exc:
        if _is_console_write_broken(exc):
            _disable_console_output(exc)


def _thread_safe_print(*args, **kwargs):
    if _CONSOLE_OUTPUT_DISABLED:
        return
    with CONSOLE_LOCK:
        try:
            _RAW_PRINT(*args, **kwargs)
        except Exception as exc:
            if _is_console_write_broken(exc):
                _disable_console_output(exc)


def _thread_safe_print_exc(*args, **kwargs):
    if _CONSOLE_OUTPUT_DISABLED:
        return
    with CONSOLE_LOCK:
        try:
            _RAW_TRACEBACK_PRINT_EXC(*args, **kwargs)
        except Exception as exc:
            if _is_console_write_broken(exc):
                _disable_console_output(exc)


solve_captcha_module.set_log_printer(_thread_safe_print)

# Remote Error Logging & Diagnostics
_FAULT_LOG_HANDLE = None
REMOTE_ERROR_LOG_URL = "https://connection.testwebyummy.click/client/error-log"
REMOTE_ERROR_LOG_TIMEOUT = (3, 5)
REMOTE_ERROR_LOG_QUEUE_MAXSIZE = 100
_REMOTE_ERROR_LOG_QUEUE = queue.Queue(maxsize=REMOTE_ERROR_LOG_QUEUE_MAXSIZE)
_REMOTE_ERROR_LOG_WORKER_STARTED = False
_REMOTE_ERROR_LOG_WORKER_LOCK = threading.Lock()


def _json_safe_remote_error_value(value, depth=0):
    if depth > 5:
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe_remote_error_value(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_remote_error_value(v, depth + 1) for v in value]
    return str(value)


def _get_remote_error_auth_payload():
    auth_payload = {}
    try:
        if os.path.exists("auth.json"):
            with open("auth.json", "r", encoding="utf-8-sig") as f:
                auth_payload = json.load(f)
    except Exception:
        pass
    return auth_payload


def _build_remote_error_payload(data):
    payload = _get_remote_error_auth_payload()
    payload.update(data)
    payload["version"] = version
    payload["timestamp"] = int(time.time())
    payload["platform"] = platform.platform()
    return payload


def _start_remote_error_log_worker():
    global _REMOTE_ERROR_LOG_WORKER_STARTED
    with _REMOTE_ERROR_LOG_WORKER_LOCK:
        if _REMOTE_ERROR_LOG_WORKER_STARTED:
            return
        _REMOTE_ERROR_LOG_WORKER_STARTED = True
        t = threading.Thread(
            target=_remote_error_log_worker,
            daemon=True,
            name="RemoteErrorLogWorker",
        )
        t.start()


def _enqueue_remote_error_log(payload):
    try:
        safe_payload = _json_safe_remote_error_value(payload)
        _REMOTE_ERROR_LOG_QUEUE.put_nowait(safe_payload)
        _start_remote_error_log_worker()
    except Exception:
        pass


def _remote_error_log_worker():
    session = requests.Session()
    while True:
        try:
            payload = _REMOTE_ERROR_LOG_QUEUE.get()
            if payload is None:
                break
            try:
                session.post(REMOTE_ERROR_LOG_URL, json=payload, timeout=REMOTE_ERROR_LOG_TIMEOUT)
            except Exception:
                pass
            finally:
                _REMOTE_ERROR_LOG_QUEUE.task_done()
        except Exception:
            time.sleep(1)


class _RemoteErrorLogHandler(logging.Handler):
    def emit(self, record):
        try:
            traceback_text = self.format(record)
            payload = _build_remote_error_payload({
                "level": record.levelname,
                "message": record.getMessage(),
                "traceback": traceback_text,
                "remote_context": getattr(record, "remote_context", "logging"),
                "remote_kind": getattr(record, "remote_kind", "error"),
            })
            _enqueue_remote_error_log(payload)
        except Exception:
            pass


def _install_remote_error_logging():
    root_logger = logging.getLogger()
    remote_handler = _RemoteErrorLogHandler()
    remote_handler.setLevel(logging.ERROR)
    root_logger.addHandler(remote_handler)


def _log_uncaught_exception(context, exc_type, exc_value, exc_tb, message=None):
    traceback_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    traceback_text = "".join(traceback_lines)
    payload = _build_remote_error_payload({
        "context": context,
        "error_type": exc_type.__name__ if exc_type else "Exception",
        "error_message": str(exc_value),
        "traceback": traceback_text,
        "message": message or str(exc_value),
    })
    _enqueue_remote_error_log(payload)


def _install_runtime_diagnostics():
    global _FAULT_LOG_HANDLE
    fault_log_path = os.path.join("log", "fault.log")
    try:
        _FAULT_LOG_HANDLE = open(fault_log_path, "a", encoding="utf-8")
        faulthandler.enable(_FAULT_LOG_HANDLE)
    except Exception:
        pass

    def _sys_excepthook(exc_type, exc_value, exc_tb):
        _log_uncaught_exception("sys.excepthook", exc_type, exc_value, exc_tb)
        _thread_safe_print_exc()

    def _thread_excepthook(args):
        thread_name = getattr(args.thread, "name", "unknown")
        _log_uncaught_exception(
            f"thread:{thread_name}",
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
        )

    def _log_clean_exit():
        try:
            if _FAULT_LOG_HANDLE and not _FAULT_LOG_HANDLE.closed:
                _FAULT_LOG_HANDLE.close()
        except Exception:
            pass

    sys.excepthook = _sys_excepthook
    threading.excepthook = _thread_excepthook
    atexit.register(_log_clean_exit)
    _install_remote_error_logging()


_install_runtime_diagnostics()


def _safe_input(prompt=""):
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""
    except Exception as exc:
        if _is_console_write_broken(exc):
            _disable_console_output(exc)
        return ""


# Global Hotkeys Listener (Ctrl+R -> killproc, Ctrl+Q -> arrange_roblox_windows_hotkey_once)
_HOTKEY_LISTENER_THREAD = None
_HOTKEY_LISTENER_LOCK = threading.Lock()
_HOTKEY_HANDLERS = {}

WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
VK_Q = 0x51
VK_R = 0x52
HOTKEY_ID_KILL_ROBLOX = 1
HOTKEY_ID_ARRANGE_ROBLOX = 2


def _register_global_hotkeys():
    user32 = ctypes.windll.user32
    registrations = [
        (HOTKEY_ID_KILL_ROBLOX, MOD_CONTROL, VK_R, lambda: killproc()),
        (
            HOTKEY_ID_ARRANGE_ROBLOX,
            MOD_CONTROL,
            VK_Q,
            lambda: arrange_roblox_windows_hotkey_once(),
        ),
    ]
    registered_ids = []
    for hotkey_id, modifiers, vk_code, handler in registrations:
        try:
            if user32.RegisterHotKey(None, hotkey_id, modifiers, vk_code):
                _HOTKEY_HANDLERS[hotkey_id] = handler
                registered_ids.append(hotkey_id)
        except Exception:
            pass
    return registered_ids


def _global_hotkey_listener():
    user32 = ctypes.windll.user32
    registered_ids = _register_global_hotkeys()
    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                hotkey_id = msg.wParam
                handler = _HOTKEY_HANDLERS.get(hotkey_id)
                if handler:
                    try:
                        threading.Thread(target=handler, daemon=True).start()
                    except Exception:
                        pass
            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))
    finally:
        for hid in registered_ids:
            try:
                user32.UnregisterHotKey(None, hid)
            except Exception:
                pass


def start_global_hotkey_listener():
    global _HOTKEY_LISTENER_THREAD
    with _HOTKEY_LISTENER_LOCK:
        if _HOTKEY_LISTENER_THREAD is not None and _HOTKEY_LISTENER_THREAD.is_alive():
            return
        _HOTKEY_LISTENER_THREAD = threading.Thread(
            target=_global_hotkey_listener,
            daemon=True,
            name="GlobalHotkeyListener",
        )
        _HOTKEY_LISTENER_THREAD.start()


# Disable Windows Console QuickEdit & Mouse Input
ENABLE_QUICK_EDIT = 0x0040
ENABLE_INSERT_MODE = 0x0020
ENABLE_MOUSE_INPUT = 0x0010
kernel32 = ctypes.windll.kernel32
try:
    hStdin = kernel32.GetStdHandle(-10)
    mode = wintypes.DWORD()
    if kernel32.GetConsoleMode(hStdin, byref(mode)):
        new_mode = mode.value & ~ENABLE_QUICK_EDIT & ~ENABLE_MOUSE_INPUT
        kernel32.SetConsoleMode(hStdin, new_mode)
except Exception:
    pass


def log(message: str, level: str = None) -> str:
    timestamp = datetime.now().strftime("[%d/%m/%Y - %H:%M:%S]")
    colors = {
        "INFO": Fore.CYAN,
        "SUCCESS": Fore.GREEN,
        "WARN": Fore.YELLOW,
        "ERROR": Fore.RED,
        "PURPLE": Fore.MAGENTA,
    }
    color = colors.get(level, Fore.WHITE)
    level_str = f"[{level}]" if level else ""
    formatted = f"{Fore.WHITE}{timestamp} {color}{level_str} {message}{Fore.RESET}"
    _thread_safe_print(formatted)
    return formatted


def info(msg: str):
    return log(msg, "INFO")


def success(msg: str):
    return log(msg, "SUCCESS")


def warn(msg: str):
    return log(msg, "WARN")


def error(msg: str):
    return log(msg, "ERROR")


def purple(msg: str):
    return log(msg, "PURPLE")


USER_ID_PRIVATE_KEY = "webrb-local-private-key-v1"
USER_ID_PRIVATE_KEY_PREFIX = "enc-v1:"


def _encode_user_id_for_tool(value):
    if not value:
        return ""
    plain = str(value).strip()
    key = USER_ID_PRIVATE_KEY.encode("utf-8")
    key_len = len(key)
    plain_bytes = plain.encode("utf-8")
    encrypted = bytes(b ^ key[idx % key_len] for idx, b in enumerate(plain_bytes))
    token = base64.b64encode(encrypted).decode("ascii")
    return f"{USER_ID_PRIVATE_KEY_PREFIX}{token}"


_solve_captcha_lock = threading.Lock()
_solve_captcha_calls = {}
_solve_captcha_failures = {}


def _solve_captcha_account_key(username, cookie_value):
    return f"{username or ''}:{(cookie_value or '')[:32]}"


def _can_run_solve_captcha(cfg, account_key):
    with _solve_captcha_lock:
        used_calls = _solve_captcha_calls.get(account_key, 0)
        max_calls = int(cfg.get("Max Solve Captcha Attempts", 5)) if cfg else 5
        return used_calls < max_calls


def _record_solve_captcha_result(account_key, status):
    with _solve_captcha_lock:
        _solve_captcha_calls[account_key] = _solve_captcha_calls.get(account_key, 0) + 1
        if status in ("failed", "error", "timeout"):
            _solve_captcha_failures[account_key] = (
                _solve_captcha_failures.get(account_key, 0) + 1
            )


def _get_solve_captcha_failures(account_key):
    with _solve_captcha_lock:
        return _solve_captcha_failures.get(account_key, 0)


def _normalize_solve_captcha_status(value):
    if not value:
        return "failed"
    text = str(value).strip().lower()
    aliases = {
        "ok": "success",
        "pass": "success",
        "passed": "success",
        "done": "success",
        "success": "success",
        "fail": "failed",
        "failed": "failed",
        "error": "error",
        "timeout": "timeout",
    }
    return aliases.get(text, text)


def _parse_third_party_solve_captcha_response(response):
    if response is None:
        return "failed"
    try:
        if isinstance(response, str):
            payload = json.loads(response)
        elif hasattr(response, "json"):
            payload = response.json()
        elif isinstance(response, dict):
            payload = response
        else:
            payload = {}
        for key in ("status", "result", "state", "code"):
            if key in payload:
                val = str(payload[key]).strip().lower()
                normalized = _normalize_solve_captcha_status(val)
                return normalized
        if payload.get("success") is True:
            return "success"
    except Exception:
        pass
    return "failed"


THIRD_PARTY_SOLVE_CAPTCHA_TIMEOUT_KEY = "Third Party Solve Captcha Timeout [s]"
THIRD_PARTY_SOLVE_CAPTCHA_DEFAULT_TIMEOUT_SECONDS = 120
SOLVE_CAPTCHA_BROWSER_TIMEOUT_KEY = "Solve Captcha Browser Timeout [s]"
SOLVE_CAPTCHA_BROWSER_DEFAULT_TIMEOUT_SECONDS = 300


def _normalize_third_party_solve_captcha_timeout(value):
    try:
        t = int(value)
        if t > 0:
            return t
    except Exception:
        pass
    return THIRD_PARTY_SOLVE_CAPTCHA_DEFAULT_TIMEOUT_SECONDS


def _normalize_solve_captcha_browser_timeout(value):
    try:
        t = int(value)
        if t > 0:
            return t
    except Exception:
        pass
    return SOLVE_CAPTCHA_BROWSER_DEFAULT_TIMEOUT_SECONDS


def get_third_party_solve_captcha_timeout(cfg=None):
    if cfg and THIRD_PARTY_SOLVE_CAPTCHA_TIMEOUT_KEY in cfg:
        return _normalize_third_party_solve_captcha_timeout(
            cfg[THIRD_PARTY_SOLVE_CAPTCHA_TIMEOUT_KEY]
        )
    return THIRD_PARTY_SOLVE_CAPTCHA_DEFAULT_TIMEOUT_SECONDS


def _run_third_party_solve_captcha(username, cookie_value, cfg=None, stage_callback=None):
    if not cfg:
        return "failed"
    url = cfg.get("Third Party Solve Captcha URL", "").strip()
    if not url:
        return "failed"
    timeout_seconds = get_third_party_solve_captcha_timeout(cfg)
    payload = {
        "username": username,
        "cookie": cookie_value,
        "timeout": timeout_seconds,
    }
    headers = {"Content-Type": "application/json"}
    try:
        if stage_callback:
            stage_callback("third_party_request")
        r = requests.post(url, json=payload, headers=headers, timeout=timeout_seconds)
        status = _parse_third_party_solve_captcha_response(r)
        return status
    except Exception as exc:
        if stage_callback:
            stage_callback(f"third_party_error: {exc}")
        return "error"


def _should_run_solve_cap_from_other_vps(cookie_value, username=None):
    try:
        cfg = read_file("config.json")
        if cfg and cfg.get("Solve Captcha From Other VPS", False):
            return True
    except Exception:
        pass
    return False


def _run_solve_captcha_direct(username, cookie_value, cfg=None, stage_callback=None):
    if not cfg:
        cfg = read_file("config.json") or {}
    third_party_url = cfg.get("Third Party Solve Captcha URL", "").strip()
    if third_party_url:
        third_party_status = _run_third_party_solve_captcha(
            username, cookie_value, cfg, stage_callback
        )
        if third_party_status == "success":
            return "success"
    try:
        sync_captcha_extension_client_keys()
        extension_path = _configured_captcha_extension_path()
        return solve_captcha(
            cookie_value,
            cfg=cfg,
            username=username,
            stage_callback=stage_callback,
            extension_path=extension_path,
        )
    except Exception as exc:
        if stage_callback:
            stage_callback(f"direct_solve_error: {exc}")
        return "error"


def _is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_sock:
        probe_sock.settimeout(0.5)
        return probe_sock.connect_ex(("127.0.0.1", port)) == 0


def _pick_remote_debug_port():
    start_port = 9222
    end_port = 9322
    for _ in range(50):
        candidate = random.randint(start_port, end_port)
        if not _is_port_in_use(candidate):
            return candidate
    return 9222


def _start_solve_in_app_thread(
    port,
    solver_client_key=None,
    solver_provider="yescaptcha",
    session_label=None,
):
    try:
        def solve_worker():
            try:
                solve_in_app_captcha(
                    port=port,
                    solver_client_key=solver_client_key,
                    solver_provider=solver_provider,
                    session_label=session_label,
                )
            except Exception as e:
                logging.error(f"In-app solver thread error: {e}")

        t = threading.Thread(
            target=solve_worker,
            daemon=True,
            name=f"SolveInApp-{port}",
        )
        t.start()
        return t
    except Exception as e:
        logging.error(f"Failed to start solve in app thread: {e}")
        return None


def solve_captcha_with_limit(username, cookie_value, cfg=None, stage_callback=None):
    account_key = _solve_captcha_account_key(username, cookie_value)
    if not _can_run_solve_captcha(cfg, account_key):
        return "limit_exceeded"
    status = _run_solve_captcha_direct(username, cookie_value, cfg, stage_callback)
    _record_solve_captcha_result(account_key, status)
    return status


def _reset_solve_captcha_counters(account_key):
    with _solve_captcha_lock:
        _solve_captcha_calls.pop(account_key, None)
        _solve_captcha_failures.pop(account_key, None)


def kill_other_webrb_exe(current_pid=None):
    if current_pid is None:
        current_pid = os.getpid()
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if p.info["pid"] == current_pid:
                continue
            name = (p.info.get("name") or "").lower()
            exe = (p.info.get("exe") or "").lower()
            if "webrb" in name or "webrb" in exe:
                p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def detect_base_path(current_dir=None):
    if current_dir is None:
        current_dir = os.getcwd()
    return current_dir


def ensure_file_exists(folder, filename):
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, filename)
    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            if filename.endswith(".json"):
                f.write("{}")
            else:
                f.write("")
    return file_path


def _normalize_windows_path(path):
    if not path:
        return ""
    text = str(path).strip().strip('"').strip("'")
    return os.path.normpath(os.path.abspath(text))


def _webplayer_runtime_dir():
    return RUNTIME_DIR


def _get_legacy_solve_captcha_exe_paths():
    seen = set()
    candidate_dirs = [
        RUNTIME_DIR,
        os.path.join(RUNTIME_DIR, "allscript"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "yummyauto"),
        os.path.join(os.environ.get("PROGRAMDATA", ""), "yummyauto"),
    ]
    paths = []
    for raw_path in candidate_dirs:
        if not raw_path or not os.path.isdir(raw_path):
            continue
        normalized_dir = _normalize_windows_path(raw_path)
        lowered = normalized_dir.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        target = os.path.join(normalized_dir, "solve_captcha.exe")
        if os.path.isfile(target):
            paths.append(target)
    return paths


def _terminate_legacy_solve_captcha_processes(target_paths=None):
    current_pid = os.getpid()
    normalized_targets = [
        _normalize_windows_path(p).lower()
        for p in (target_paths or _get_legacy_solve_captcha_exe_paths())
    ]
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if proc.info["pid"] == current_pid:
                continue
            exe_path = proc.info.get("exe") or ""
            exe_norm = _normalize_windows_path(exe_path).lower() if exe_path else ""
            name = (proc.info.get("name") or "").lower()
            if exe_norm in normalized_targets or name == "solve_captcha.exe":
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def _cleanup_legacy_solve_captcha_exe():
    target_paths = _get_legacy_solve_captcha_exe_paths()
    _terminate_legacy_solve_captcha_processes(target_paths)
    for path in target_paths:
        for _attempt in range(3):
            try:
                if os.path.isfile(path):
                    os.remove(path)
                break
            except Exception:
                time.sleep(0.5)


def _query_windows_service_running(service_name):
    try:
        r = subprocess.run(
            ["sc.exe", "query", service_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        text = r.stdout
        exists = "1060" not in text
        running = "STATE" in text and "RUNNING" in text
        return exists, running
    except Exception:
        return False, False


def _same_binary_file(path_a, path_b):
    try:
        if not os.path.isfile(path_a) or not os.path.isfile(path_b):
            return False
        if os.path.getsize(path_a) != os.path.getsize(path_b):
            return False
        with open(path_a, "rb") as file_a, open(path_b, "rb") as file_b:
            while True:
                chunk_a = file_a.read(65536)
                chunk_b = file_b.read(65536)
                if chunk_a != chunk_b:
                    return False
                if not chunk_a:
                    return True
    except Exception:
        return False


def _is_windows_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _run_elevated_install_script(install_script, runtime_dir, timeout=30):
    try:
        escaped_script = install_script.replace('"', '`"')
        powershell_command = (
            f'Start-Process powershell -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass '
            f'-NoProfile -File `"{escaped_script}`"" -WorkingDirectory `"{runtime_dir}`" -Wait'
        )
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", powershell_command],
            timeout=timeout,
        )
        return r.returncode == 0
    except Exception:
        return False


def _extract_yummyauto_service_auth_identity(payload):
    if not isinstance(payload, dict):
        return "", ""
    return str(payload.get("UserID (UUID)", "")).strip(), str(payload.get("Key", "")).strip()


def _programdata_auth_user_changed(payload, installed_auth):
    source_user_id, source_user_key = _extract_yummyauto_service_auth_identity(payload)
    if not os.path.isfile(installed_auth):
        return True
    try:
        with open(installed_auth, "r", encoding="utf-8-sig") as file:
            installed_payload = json.load(file)
            installed_user_id, installed_user_key = _extract_yummyauto_service_auth_identity(
                installed_payload
            )
            return (source_user_id != installed_user_id) or (source_user_key != installed_user_key)
    except Exception:
        return True


def _sync_yummyauto_programdata_auth(runtime_dir):
    programdata_dir = os.path.join(
        os.environ.get("PROGRAMDATA", "C:\\ProgramData"), "yummyauto"
    )
    os.makedirs(programdata_dir, exist_ok=True)
    runtime_auth = os.path.join(runtime_dir, "auth.json")
    installed_auth = os.path.join(programdata_dir, "auth.json")
    if os.path.isfile(runtime_auth):
        try:
            with open(runtime_auth, "r", encoding="utf-8-sig") as file:
                payload = json.load(file)
            if _programdata_auth_user_changed(payload, installed_auth):
                temp_auth = installed_auth + ".tmp"
                with open(temp_auth, "w", encoding="utf-8") as file:
                    json.dump(payload, file, indent=4)
                os.replace(temp_auth, installed_auth)
        except Exception as e:
            logging.error(f"Sync auth to ProgramData failed: {e}")


def ensure_yummyauto_service(
    service_exe_path="yummyauto_service.exe", wait=False, timeout=30, poll=0.5
):
    runtime_dir = RUNTIME_DIR
    service_sc_name = "YummyAutoService"
    programdata_dir = os.path.join(
        os.environ.get("PROGRAMDATA", "C:\\ProgramData"), "yummyauto"
    )
    installed_service = os.path.join(programdata_dir, "yummyauto_service.exe")
    payload_candidates = [
        os.path.join(runtime_dir, service_exe_path),
        os.path.join(runtime_dir, "allscript", service_exe_path),
        os.path.join(runtime_dir, "bin", service_exe_path),
    ]
    payload_service = next((p for p in payload_candidates if os.path.isfile(p)), None)
    install_script_candidates = [
        os.path.join(runtime_dir, "install_yummyauto_service.bat"),
        os.path.join(runtime_dir, "allscript", "install_yummyauto_service.bat"),
    ]
    install_script = next((p for p in install_script_candidates if os.path.isfile(p)), None)

    _sync_yummyauto_programdata_auth(runtime_dir)

    service_exists, service_running = _query_windows_service_running(service_sc_name)
    if not service_exists or (
        payload_service and not _same_binary_file(payload_service, installed_service)
    ):
        if install_script and os.path.isfile(install_script):
            _run_elevated_install_script(install_script, runtime_dir, timeout=timeout)
            service_exists, service_running = _query_windows_service_running(service_sc_name)

    if service_exists and not service_running:
        try:
            subprocess.run(["sc.exe", "start", service_sc_name], capture_output=True, timeout=5)
        except Exception:
            pass
        if wait:
            start_t = time.time()
            while time.time() - start_t < timeout:
                _, service_running = _query_windows_service_running(service_sc_name)
                if service_running:
                    break
                time.sleep(poll)

    return {
        "exists": service_exists,
        "running": service_running,
        "reason": "ok" if service_running else "not_running",
    }


folders_files = [
    ["stored", "savedata.txt"],
    ["switched", "storeddata.txt"],
    ["switched", "deadcookie.txt"],
    ["stored", "privateserver.txt"],
    ["switched", "banned.txt"],
    ["stored", "storeddeadck.txt"],
]

for folder, filename in folders_files:
    ensure_file_exists(folder, filename)

# UTC Synchronization
current_utc_time = None
last_request_time = 0
_lock = threading.Lock()
_anchor_utc = None
_anchor_mono = None
_last_attempt_mono = 0
_last_success_mono = 0
_inflight = False
_utc_source = "uninitialized"
_utc_shutdown = threading.Event()

UTC_API_URLS = [
    "https://cdn.yummywebplayer.click/utc",
    "https://api-am.yummydata.click/utc",
    "https://api-pet99.yummydata.click/utc",
]
UTC_HTTP_DATE_URLS = [
    "https://microsoft.com",
    "https://google.com",
    "https://cloudflare.com",
]
UTC_NTP_SERVERS = [
    "time.cloudflare.com",
    "time.google.com",
    "time.windows.com",
    "pool.ntp.org",
]
UTC_STATE_FILE = os.path.join("stored", "utc_state.json")
UTC_HTTP_TIMEOUT = (4, 4)
UTC_NTP_TIMEOUT = 4
UTC_SYNC_INTERVAL = 60
UTC_LOOP_SLEEP = 0.25

UTC_SESSION = None
UTC_SESSION_LOCK = threading.Lock()


def _utc_now():
    return datetime.now(timezone.utc)


def _set_utc_source(source):
    global _utc_source
    previous = _utc_source
    _utc_source = source
    return previous


def _save_utc_state(server_dt):
    try:
        local_dt = _utc_now()
        payload = {
            "server_utc": server_dt.isoformat(),
            "local_utc": local_dt.isoformat(),
            "offset_seconds": (server_dt - local_dt).total_seconds(),
            "timestamp": time.time(),
        }
        with open(UTC_STATE_FILE, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=4)
    except Exception:
        pass


def _load_cached_utc_time():
    if not os.path.isfile(UTC_STATE_FILE):
        return None
    try:
        with open(UTC_STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            offset_seconds = float(data.get("offset_seconds", 0.0))
            return _utc_now() + timedelta(seconds=offset_seconds)
    except Exception:
        return None


def _get_utc_session():
    global UTC_SESSION
    with UTC_SESSION_LOCK:
        if UTC_SESSION is None:
            UTC_SESSION = requests.Session()
            adapter = requests.adapters.HTTPAdapter(max_retries=1)
            UTC_SESSION.mount("http://", adapter)
            UTC_SESSION.mount("https://", adapter)
        return UTC_SESSION


def _fetch(url):
    s = _get_utc_session()
    r = s.get(url, timeout=UTC_HTTP_TIMEOUT)
    if r.status_code == 200:
        data = r.json()
        if "utc" in data:
            return datetime.fromisoformat(data["utc"])
    return None


def _fetch_http_date(url):
    s = _get_utc_session()
    r = s.head(url, timeout=UTC_HTTP_TIMEOUT)
    date_header = r.headers.get("Date")
    if date_header:
        dt = parsedate_to_datetime(date_header)
        return dt.astimezone(timezone.utc)
    return None


def _fetch_ntp_time(host):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(UTC_NTP_TIMEOUT)
    packet = b"\x1b" + 47 * b"\0"
    started = time.time()
    try:
        sock.sendto(packet, (host, 123))
        data, _ = sock.recvfrom(1024)
        finished = time.time()
        if len(data) >= 48:
            seconds = int.from_bytes(data[40:44], "big")
            fraction = int.from_bytes(data[44:48], "big")
            unix_time = (seconds - 2208988800) + (fraction / (2**32))
            return datetime.fromtimestamp(unix_time, tz=timezone.utc)
    except Exception:
        pass
    finally:
        sock.close()
    return None


def _run_utc_jobs(jobs):
    for fn, arg, label in jobs:
        try:
            dt = fn(arg)
            if dt is not None:
                return dt, label
        except Exception:
            pass
    return None, None


def _resolve_live_utc_time():
    primary_jobs = [(_fetch, u, f"api:{u}") for u in UTC_API_URLS]
    dt, source = _run_utc_jobs(primary_jobs)
    if dt is not None:
        return dt, source
    fallback_jobs = [(_fetch_http_date, u, f"http_date:{u}") for u in UTC_HTTP_DATE_URLS] + [
        (_fetch_ntp_time, h, f"ntp:{h}") for h in UTC_NTP_SERVERS
    ]
    dt, source = _run_utc_jobs(fallback_jobs)
    if dt is not None:
        return dt, source
    return None, None


def get_utc_time():
    global current_utc_time, _anchor_utc, _anchor_mono
    with _lock:
        if _anchor_utc is not None and _anchor_mono is not None:
            elapsed = time.monotonic() - _anchor_mono
            return _anchor_utc + timedelta(seconds=elapsed)
        cached_dt = _load_cached_utc_time()
        if cached_dt is not None:
            return cached_dt
        return _utc_now()


def update_utc_time():
    global _anchor_utc, _anchor_mono, _last_attempt_mono, _last_success_mono, _inflight
    while not _utc_shutdown.is_set():
        now_m = time.monotonic()
        need_sync = (_anchor_utc is None) or (now_m - _last_success_mono > UTC_SYNC_INTERVAL)
        if need_sync:
            _last_attempt_mono = now_m
            try:
                dt, source = _resolve_live_utc_time()
                if dt is not None:
                    with _lock:
                        _anchor_utc = dt
                        _anchor_mono = time.monotonic()
                        _last_success_mono = _anchor_mono
                        _set_utc_source(source)
                    _save_utc_state(dt)
            except Exception:
                pass
        time.sleep(UTC_LOOP_SLEEP)


def _stop_utc_updater():
    _utc_shutdown.set()


atexit.register(_stop_utc_updater)
time_updater = threading.Thread(target=update_utc_time, daemon=True, name="UtcUpdater")
time_updater.start()


def reset_to_defaults(file_path, default_data):
    try:
        with open(file_path, "w", encoding="utf-8-sig") as file:
            json.dump(default_data, file, indent=4)
    except Exception:
        pass


def validate_and_reset(file_path, default_data):
    if not os.path.exists(file_path):
        reset_to_defaults(file_path, default_data)
        return
    try:
        with open(file_path, "r", encoding="utf-8-sig") as file:
            json.load(file)
    except Exception:
        reset_to_defaults(file_path, default_data)


def _coerce(v, t):
    try:
        if t is bool:
            return bool(v)
        if t is int:
            return int(v)
        if t is float:
            return float(v)
        if t is str:
            return str(v)
    except Exception:
        pass
    return v


def _merge_defaults(defaults, data, strict=False, coerce_types=False):
    changed = False
    out = dict(data) if isinstance(data, dict) else {}
    for k, dv in defaults.items():
        if k not in out:
            out[k] = dv
            changed = True
        elif coerce_types and type(out[k]) is not type(dv) and dv is not None:
            nv = _coerce(out[k], type(dv))
            if nv != out[k]:
                out[k] = nv
                changed = True
    if strict:
        for k in list(out.keys()):
            if k not in defaults:
                del out[k]
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
    if not isinstance(cfg, dict):
        return changed
    for k in SPECIAL_ZERO_KEYS:
        if k in cfg and cfg[k] == 0:
            cfg[k] = "0"
            changed = True
    return changed


def validate_fill_defaults(
    file_path, defaults, strict=False, coerce_types=False, encoding="utf-8-sig"
):
    try:
        if not os.path.exists(file_path):
            with open(file_path, "w", encoding=encoding) as f:
                json.dump(defaults, f, indent=4)
            return
        with open(file_path, "r", encoding=encoding) as f:
            data = json.load(f)
        merged, changed = _merge_defaults(
            defaults, data, strict=strict, coerce_types=coerce_types
        )
        ch2 = _normalize_bloxfruit_zero_fields(merged)
        if changed or ch2:
            with open(file_path, "w", encoding=encoding) as f:
                json.dump(merged, f, indent=4)
    except Exception:
        with open(file_path, "w", encoding=encoding) as f:
            json.dump(defaults, f, indent=4)


def update_yescaptcha_client_key(client_key, config_path=None):
    if not client_key:
        return
    path = config_path or os.path.join(
        RUNTIME_DIR, "extension", "yescaptcha", "config.json"
    )
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            data["clientKey"] = client_key
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception:
            pass


def update_omocaptcha_client_key(client_key, config_path=None):
    if not client_key:
        return
    path = config_path or os.path.join(
        RUNTIME_DIR, "extension", "omocaptcha", "config.json"
    )
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            data["api_key"] = client_key
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception:
            pass


def sync_captcha_extension_client_keys():
    try:
        cfg = read_file("config.json")
        if not cfg:
            return
        yk = cfg.get("YesCaptcha Client Key", "").strip()
        if yk:
            update_yescaptcha_client_key(yk)
        ok = cfg.get("OmoCaptcha Client Key", "").strip()
        if ok:
            update_omocaptcha_client_key(ok)
    except Exception:
        pass


def _configured_captcha_extension_path():
    try:
        cfg = read_file("config.json")
        if cfg and cfg.get("Custom Extension Path"):
            p = cfg["Custom Extension Path"].strip()
            if os.path.isdir(p):
                return p
    except Exception:
        pass
    default_ext = os.path.join(RUNTIME_DIR, "extension")
    return default_ext if os.path.isdir(default_ext) else ""


bloxfruit_default = {
    "Level": "2600",
    "Beli": "0",
    "Fragment": "0",
    "Tier": "0",
    "Bounty_Honor": "0",
    "Sanguine Art": False,
    "Race Awakening": False,
    "Race V3": False,
    "Godhuman": False,
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
    "Rank": 20,
    "Rebirth": 5,
    "Gem": 3,
    "UnlockedZones": 2,
    "Bucket": 1,
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
os.makedirs("stored", exist_ok=True)
validate_and_reset(bloxfruit_file, bloxfruit_default)
validate_fill_defaults(bloxfruit_file, bloxfruit_default, strict=False, coerce_types=True)
validate_fill_defaults(dfbloxfruit_file, dfbloxfruit_default, strict=False, coerce_types=True)
validate_and_reset(dfbloxfruit_file, dfbloxfruit_default)
validate_and_reset("stored/pet99.json", pet99_default)


def fix_unescaped_backslashes(text):
    return re.sub(r'\\(?![/u"\\bfnrt])', r"\\\\", text)


def _detect_workspace_from_localappdata():
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return ""
    candidates = [
        os.path.join(local, "Roblox", "workspace"),
        os.path.join(local, "Programs", "Roblox", "workspace"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return ""


def _resolve_workspace_folder(path=None):
    if path and os.path.isdir(path):
        return path
    cfg_path = ""
    try:
        cfg = read_file("config.json")
        if cfg and cfg.get("Workspace Folder"):
            cfg_path = cfg["Workspace Folder"].strip()
            if os.path.isdir(cfg_path):
                return cfg_path
    except Exception:
        pass
    detected = _detect_workspace_from_localappdata()
    return detected or RUNTIME_DIR


def _normalize_launch_roblox_threads(value):
    try:
        t = int(value)
        if t > 0:
            return t
    except Exception:
        pass
    return 3


def get_launch_roblox_threads(cfg=None):
    if cfg and "Launch Roblox Threads" in cfg:
        return _normalize_launch_roblox_threads(cfg["Launch Roblox Threads"])
    return 3


_SYSTEM_RAM_TAB_KILL_KEY = "Kill Highest RAM Tab [System %]"


def _normalize_system_ram_tab_kill_threshold(value):
    try:
        v = float(value)
        if 0 < v <= 100:
            return v
    except Exception:
        pass
    return 0.0


def validate_config(config_file="config.json"):
    defaults = {
        "Auto Detect/Run Update.exe": True,
        "Auto Kill WebRB": False,
        "Total Instance": 10,
        "Threading Delay": 3,
        "Windows Per Rows": 5,
        "Launch Roblox Threads": 3,
        "Sort": True,
        "Old Sort": False,
        "Minimize": False,
        "Set Priority Low": True,
        "Set Affinity": False,
        "Affinity Core": 4,
        "Close Roblox (Captcha)": True,
        "Change Account (Captcha)": False,
        "Kill Seliware Error Message": True,
        "Check Banned Account and Change": False,
        "Low Server": False,
        "Run Multi PlaceId": False,
        "PlaceId": "2753915549",
        "Fps Unlocker": False,
        "Webhook Url": "",
        "Webhook Delay Send [M]": 60,
        "Third Party Solve Captcha URL": "",
        "Third Party Solve Captcha Timeout [s]": 120,
        "Solve Captcha Browser Timeout [s]": 300,
        _SYSTEM_RAM_TAB_KILL_KEY: 0,
        "ChromeDriver Download URL": "",
        "Use Proxies For Check Cookie": False,
        "Custom Extension Path": "",
        "Clear StoredData When No Account": False,
    }
    validate_fill_defaults(config_file, defaults, strict=False, coerce_types=True)


def apply_workspace_custom_defaults(config_path="config.json"):
    validate_config(config_path)


validate_config("config.json")
apply_workspace_custom_defaults("config.json")

# Path Locking System
_IN_PROCESS_LOCKS = {}
_IN_PROCESS_LOCKS_MUTEX = threading.Lock()


class PathLockTimeoutError(TimeoutError):
    pass


def _normalize_path_lock_key(path):
    if not path:
        return ""
    return os.path.normpath(os.path.abspath(str(path))).lower()


def _get_path_lock_timeout_seconds(timeout_seconds=None):
    if timeout_seconds is not None:
        return float(timeout_seconds)
    return 10.0


def _get_in_process_path_lock(path):
    key = _normalize_path_lock_key(path)
    with _IN_PROCESS_LOCKS_MUTEX:
        if key not in _IN_PROCESS_LOCKS:
            _IN_PROCESS_LOCKS[key] = threading.RLock()
        return _IN_PROCESS_LOCKS[key]


def _acquire_path_lock(path, timeout_seconds=None):
    timeout = _get_path_lock_timeout_seconds(timeout_seconds)
    in_proc_lock = _get_in_process_path_lock(path)
    start_t = time.monotonic()
    if not in_proc_lock.acquire(timeout=timeout):
        raise PathLockTimeoutError(f"In-process lock timeout for {path}")
    lock_file_path = str(path) + ".lock"
    handle = None
    try:
        while time.monotonic() - start_t < timeout:
            try:
                handle = os.open(
                    lock_file_path, os.O_CREAT | os.O_EXCL | os.O_RDWR
                )
                break
            except FileExistsError:
                time.sleep(0.05)
        if handle is None:
            in_proc_lock.release()
            raise PathLockTimeoutError(f"Cross-process file lock timeout for {path}")
        return in_proc_lock, handle, lock_file_path
    except Exception:
        in_proc_lock.release()
        raise


def _cleanup_sidecar_lock_file(path):
    try:
        lock_file = str(path) + ".lock"
        if os.path.isfile(lock_file):
            os.remove(lock_file)
    except Exception:
        pass


def _release_path_lock(path, lock_tuple):
    if not lock_tuple:
        return
    in_proc_lock, handle, lock_file_path = lock_tuple
    try:
        if handle is not None:
            os.close(handle)
        if os.path.isfile(lock_file_path):
            os.remove(lock_file_path)
    except Exception:
        pass
    finally:
        try:
            in_proc_lock.release()
        except Exception:
            pass


def _acquire_locked_path(path, timeout_seconds=None):
    return _acquire_path_lock(path, timeout_seconds)


def _release_locked_path(path, lock_tuple):
    return _release_path_lock(path, lock_tuple)


def append_text_locked(path, text, timeout_seconds=None, encoding="utf-8"):
    lock_tuple = _acquire_path_lock(path, timeout_seconds)
    try:
        with open(path, "a", encoding=encoding) as f:
            f.write(text)
    finally:
        _release_path_lock(path, lock_tuple)


def append_line_locked(path, line, timeout_seconds=None, encoding="utf-8"):
    append_text_locked(path, line.rstrip("\r\n") + "\n", timeout_seconds, encoding)


def append_lines_locked(path, lines, timeout_seconds=None, encoding="utf-8"):
    text = "".join(l.rstrip("\r\n") + "\n" for l in lines)
    append_text_locked(path, text, timeout_seconds, encoding)


def read_lines_locked(path, timeout_seconds=None, encoding="utf-8"):
    if not os.path.isfile(path):
        return []
    lock_tuple = _acquire_path_lock(path, timeout_seconds)
    try:
        with open(path, "r", encoding=encoding) as f:
            return f.readlines()
    finally:
        _release_path_lock(path, lock_tuple)


def read_text_locked(path, timeout_seconds=None, encoding="utf-8"):
    if not os.path.isfile(path):
        return ""
    lock_tuple = _acquire_path_lock(path, timeout_seconds)
    try:
        with open(path, "r", encoding=encoding) as f:
            return f.read()
    finally:
        _release_path_lock(path, lock_tuple)


def read_json_locked(path, timeout_seconds=None, default=None, encoding="utf-8"):
    if not os.path.isfile(path):
        return default
    lock_tuple = _acquire_path_lock(path, timeout_seconds)
    try:
        with open(path, "r", encoding=encoding) as f:
            return json.load(f)
    except Exception:
        return default
    finally:
        _release_path_lock(path, lock_tuple)


def write_text_locked(path, text, timeout_seconds=None, encoding="utf-8"):
    lock_tuple = _acquire_path_lock(path, timeout_seconds)
    try:
        with open(path, "w", encoding=encoding) as f:
            f.write(text)
    finally:
        _release_path_lock(path, lock_tuple)


def write_bytes_locked(path, data, timeout_seconds=None):
    lock_tuple = _acquire_path_lock(path, timeout_seconds)
    try:
        with open(path, "wb") as f:
            f.write(data)
    finally:
        _release_path_lock(path, lock_tuple)


def write_json_locked(path, data, timeout_seconds=None, indent=4, encoding="utf-8"):
    lock_tuple = _acquire_path_lock(path, timeout_seconds)
    try:
        with open(path, "w", encoding=encoding) as f:
            json.dump(data, f, indent=indent)
    finally:
        _release_path_lock(path, lock_tuple)


def filter_out_token_from_file_locked(path, token, timeout_seconds=None):
    if not os.path.isfile(path):
        return
    lock_tuple = _acquire_path_lock(path, timeout_seconds)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()
        kept = [l for l in lines if token not in l]
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(kept)
    finally:
        _release_path_lock(path, lock_tuple)


def remove_valid_cookie_entry(token):
    filter_out_token_from_file_locked("cookie.txt", token)


# User & Browser Tracking State
browser_to_user_map = {}
_BROWSER_TO_USER_MAP_LOCK = threading.Lock()
_TRACKER_TO_USER_ID = {}
_USERNAME_BY_USER_ID = {}
_BANNED_ACCOUNTS = set()
_BANNED_ACCOUNTS_LOCK = threading.Lock()
_TRACKED_USERNAMES = {}
_TRACKED_USERNAMES_LOCK = threading.Lock()
_VALID_COOKIE_TRACKER_TIMESTAMPS = {}
_HOURLY_CHANGE_LAST_OPEN = {}


def _set_browser_to_user_map_entry(browser_id, user_id, username=None):
    with _BROWSER_TO_USER_MAP_LOCK:
        browser_to_user_map[str(browser_id)] = {
            "user_id": str(user_id),
            "username": str(username) if username else "",
            "timestamp": time.time(),
        }
        if username:
            _TRACKER_TO_USER_ID[str(browser_id)] = str(user_id)
            _USERNAME_BY_USER_ID[str(user_id)] = str(username)


def _build_tracker_resolution_snapshot():
    with _BROWSER_TO_USER_MAP_LOCK:
        return dict(_TRACKER_TO_USER_ID), dict(_USERNAME_BY_USER_ID)


def _get_tracker_for_username(username):
    if not username:
        return None
    u_low = str(username).strip().lower()
    with _BROWSER_TO_USER_MAP_LOCK:
        for bid, entry in browser_to_user_map.items():
            if entry.get("username", "").strip().lower() == u_low:
                return bid
    return None


def _mark_banned_account(username):
    if not username:
        return
    u = str(username).strip()
    with _BANNED_ACCOUNTS_LOCK:
        _BANNED_ACCOUNTS.add(u)
    try:
        append_line_locked("switched/banned.txt", u)
    except Exception:
        pass


def _is_banned_account(username):
    if not username:
        return False
    u = str(username).strip()
    with _BANNED_ACCOUNTS_LOCK:
        return u in _BANNED_ACCOUNTS


def _get_banned_accounts_snapshot():
    with _BANNED_ACCOUNTS_LOCK:
        return set(_BANNED_ACCOUNTS)


def _track_username(username, pid=None):
    if not username:
        return
    u = str(username).strip()
    with _TRACKED_USERNAMES_LOCK:
        _TRACKED_USERNAMES[u] = {
            "pid": pid,
            "timestamp": time.time(),
        }


def _get_tracked_usernames_snapshot():
    with _TRACKED_USERNAMES_LOCK:
        return dict(_TRACKED_USERNAMES)


def _set_valid_cookie_tracker_timestamp(username):
    if username:
        _VALID_COOKIE_TRACKER_TIMESTAMPS[str(username).strip()] = time.time()


def _get_valid_cookie_tracker_timestamp(username):
    return _VALID_COOKIE_TRACKER_TIMESTAMPS.get(str(username).strip(), 0)


def _get_or_seed_hourly_change_last_open(username):
    u = str(username).strip()
    if u not in _HOURLY_CHANGE_LAST_OPEN:
        _HOURLY_CHANGE_LAST_OPEN[u] = time.time()
    return _HOURLY_CHANGE_LAST_OPEN[u]


def _get_delay_open_seconds(cfg=None):
    if not cfg:
        cfg = read_file("config.json") or {}
    return float(cfg.get("Delay Open [s]", 2.0))


def _should_use_worker_cooldown():
    return True


_OPEN_THROTTLE_LOCK = threading.Lock()
_LAST_OPEN_TIMESTAMP = 0.0


def _run_open_throttled(func, *args, **kwargs):
    global _LAST_OPEN_TIMESTAMP
    with _OPEN_THROTTLE_LOCK:
        delay = _get_delay_open_seconds()
        elapsed = time.monotonic() - _LAST_OPEN_TIMESTAMP
        if elapsed < delay:
            time.sleep(delay - elapsed)
        try:
            res = func(*args, **kwargs)
            return res
        finally:
            _LAST_OPEN_TIMESTAMP = time.monotonic()


def shell_execute_open_throttled(cmdline):
    return _run_open_throttled(subprocess.Popen, cmdline, shell=True)


def launch_with_webview2_env_throttled(cmdline, env=None):
    return _run_open_throttled(launch_with_webview2_env, cmdline, env=env)


# Roblox PID Caching
_ROBLOX_PID_CACHE = {}
_ROBLOX_PID_CACHE_LOCK = threading.Lock()


def _normalize_roblox_pid_cache_username(username):
    if not username:
        return ""
    return str(username).strip().lower()


def _cache_roblox_pid_for_username(username, pid):
    u = _normalize_roblox_pid_cache_username(username)
    if not u or not pid:
        return
    with _ROBLOX_PID_CACHE_LOCK:
        _ROBLOX_PID_CACHE[u] = {
            "pid": pid,
            "timestamp": time.time(),
        }


def _get_cached_roblox_pid_for_username(username):
    u = _normalize_roblox_pid_cache_username(username)
    with _ROBLOX_PID_CACHE_LOCK:
        entry = _ROBLOX_PID_CACHE.get(u)
        if entry:
            return entry["pid"]
    return None


def _resolve_cached_roblox_pid_for_username(username):
    pid = _get_cached_roblox_pid_for_username(username)
    if pid and _is_live_roblox_process_pid(pid):
        return pid
    return None


def _is_live_roblox_process_pid(pid):
    try:
        proc = psutil.Process(pid)
        name = proc.name().lower()
        return "roblox" in name and proc.is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def trackrobloxnameandgetpid(username, ttl=60):
    cached = _resolve_cached_roblox_pid_for_username(username)
    if cached:
        return cached
    target_u = str(username).strip().lower()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "roblox" in name:
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if target_u in cmdline:
                    pid = proc.info["pid"]
                    _cache_roblox_pid_for_username(username, pid)
                    return pid
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None


# Vault Captcha & Dead Account Reporting Queue
_VAULT_CAPTCHA_QUEUE = queue.Queue()
_VAULT_DEAD_QUEUE = queue.Queue()
_VAULT_THREAD_STARTED = False
_VAULT_THREAD_LOCK = threading.Lock()


def _vault_normalize_status(status):
    return _normalize_solve_captcha_status(status)


def _vault_account_from_entry(entry):
    if isinstance(entry, dict):
        return entry.get("username", ""), entry.get("cookie", "")
    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
        return entry[0], entry[1]
    return "", ""


def _vault_pending_key(username, status):
    return f"{username}:{status}"


def _queue_vault_account_status(username, status, cookie_value=None):
    if username:
        _VAULT_CAPTCHA_QUEUE.put({
            "username": username,
            "status": status,
            "cookie": cookie_value or "",
            "timestamp": time.time(),
        })


def _queue_vault_captcha_account(username, cookie_value=None):
    _queue_vault_account_status(username, "captcha", cookie_value)


def _queue_vault_captcha_clear_account(username):
    _queue_vault_account_status(username, "cleared")


def _queue_vault_captcha_entry(entry):
    u, c = _vault_account_from_entry(entry)
    _queue_vault_captcha_account(u, c)


def _queue_vault_captcha_clear_entry(entry):
    u, _ = _vault_account_from_entry(entry)
    _queue_vault_captcha_clear_account(u)


def _queue_vault_captcha_if_status(username, status, cookie_value=None):
    norm = _vault_normalize_status(status)
    if norm in ("captcha", "challenge_required"):
        _queue_vault_captcha_account(username, cookie_value)


def _vault_valid_cookie_snapshot():
    with VALID_COOKIES_LOCK:
        return list(valid_cookies)


def _probe_vault_captcha_accounts_once():
    pass


def _post_vault_captcha_account(account_data):
    try:
        url = "https://connection.testwebyummy.click/client/vault/captcha"
        requests.post(url, json=account_data, timeout=5)
    except Exception:
        pass


def _parse_vault_account_line(line):
    parts = line.strip().split(":")
    if len(parts) >= 2:
        return parts[0].strip(), parts[1].strip()
    return line.strip(), ""


def _vault_clean_username(raw):
    return str(raw).strip()


def _post_vault_dead_account(username, cookie_value=None):
    try:
        url = "https://connection.testwebyummy.click/client/vault/dead"
        requests.post(
            url,
            json={"username": username, "cookie": cookie_value or ""},
            timeout=5,
        )
    except Exception:
        pass


def _queue_vault_dead_account(username, cookie_value=None):
    if username:
        _VAULT_DEAD_QUEUE.put({
            "username": username,
            "cookie": cookie_value or "",
            "timestamp": time.time(),
        })


def _post_vault_dead_account_line(line):
    u, c = _parse_vault_account_line(line)
    _post_vault_dead_account(u, c)


def _flush_vault_captcha_queue_once():
    while not _VAULT_CAPTCHA_QUEUE.empty():
        try:
            item = _VAULT_CAPTCHA_QUEUE.get_nowait()
            _post_vault_captcha_account(item)
            _VAULT_CAPTCHA_QUEUE.task_done()
        except queue.Empty:
            break
        except Exception:
            pass


def _flush_vault_dead_queue_once():
    while not _VAULT_DEAD_QUEUE.empty():
        try:
            item = _VAULT_DEAD_QUEUE.get_nowait()
            _post_vault_dead_account(item["username"], item.get("cookie"))
            _VAULT_DEAD_QUEUE.task_done()
        except queue.Empty:
            break
        except Exception:
            pass


def _vault_captcha_loop():
    while True:
        try:
            _flush_vault_captcha_queue_once()
            _flush_vault_dead_queue_once()
        except Exception:
            pass
        time.sleep(2)


def _start_vault_captcha_thread():
    global _VAULT_THREAD_STARTED
    with _VAULT_THREAD_LOCK:
        if _VAULT_THREAD_STARTED:
            return
        _VAULT_THREAD_STARTED = True
        t = threading.Thread(
            target=_vault_captcha_loop,
            daemon=True,
            name="VaultCaptchaLoop",
        )
        t.start()


def _resolve_username_for_roblox_pid(pid):
    with _ROBLOX_PID_CACHE_LOCK:
        for u, entry in _ROBLOX_PID_CACHE.items():
            if entry.get("pid") == pid:
                return u
    tracker_map, u_by_id = _build_tracker_resolution_snapshot()
    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline())
        for bid, uid in tracker_map.items():
            if bid in cmdline:
                return u_by_id.get(uid, "")
    except Exception:
        pass
    return ""


def _queue_vault_captcha_for_pid(pid, reason=None):
    u = _resolve_username_for_roblox_pid(pid)
    if u:
        _queue_vault_captcha_account(u)
        return True
    return False


# Window Management Utilities
def set_window_title(pid, new_title):
    user32 = ctypes.windll.user32
    best_hwnd = None
    best_area = 0

    def enum_windows_callback(hwnd, _):
        nonlocal best_hwnd, best_area
        if not user32.IsWindowVisible(hwnd):
            return True
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, byref(window_pid))
        if window_pid.value == pid:
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, byref(rect))
            area = (rect.right - rect.left) * (rect.bottom - rect.top)
            if area > best_area:
                best_area = area
                best_hwnd = hwnd
        return True

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(EnumWindowsProc(enum_windows_callback), 0)
    if best_hwnd:
        user32.SetWindowTextW(best_hwnd, new_title)
        return True
    return False


def minimize_window(hwnd):
    try:
        user32 = ctypes.windll.user32
        user32.ShowWindowAsync(hwnd, 6)
        user32.ShowWindowAsync(hwnd, 11)
    except Exception:
        pass


def get_ram_usage():
    try:
        return psutil.virtual_memory().percent
    except Exception:
        return 0.0


def get_process_count(process_name="RobloxPlayerBeta.exe"):
    cnt = 0
    pname = process_name.lower()
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() == pname:
                cnt += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return cnt


def get_cpu_model_windows():
    try:
        with reg.OpenKey(
            reg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            model, _ = reg.QueryValueEx(key, "ProcessorNameString")
            return model.strip()
    except Exception:
        return platform.processor() or "Unknown CPU"


# Cookie Sync, Reissued Usernames, and Quick Check
def _normalize_cookie_sync_username(username):
    if not username:
        return ""
    return str(username).strip().lower()


def _parse_cookie_sync_account_line(raw_line):
    line = raw_line.strip()
    if not line:
        return "", "", ""
    parts = line.split(":")
    if len(parts) >= 3:
        return parts[0].strip(), parts[1].strip(), ":".join(parts[2:]).strip()
    if len(parts) == 2:
        return parts[0].strip(), "", parts[1].strip()
    return "", "", line


def _build_cookie_sync_username_map(lines):
    mapping = {}
    for line in lines:
        u, _, c = _parse_cookie_sync_account_line(line)
        if u and c:
            mapping[_normalize_cookie_sync_username(u)] = (u, c)
    return mapping


def _write_text_lines_locked(path, lines):
    text = "".join(l.rstrip("\r\n") + "\n" for l in lines)
    write_text_locked(path, text)


def _prune_usernames_from_locked_file(path, usernames_to_prune):
    if not usernames_to_prune or not os.path.isfile(path):
        return
    norm_set = {_normalize_cookie_sync_username(u) for u in usernames_to_prune}
    lines = read_lines_locked(path)
    kept = []
    for line in lines:
        u, _, _ = _parse_cookie_sync_account_line(line)
        if _normalize_cookie_sync_username(u) not in norm_set:
            kept.append(line)
    if len(kept) != len(lines):
        _write_text_lines_locked(path, kept)


def _clear_reissued_webrb_runtime_state(username):
    u = _normalize_cookie_sync_username(username)
    with _ROBLOX_PID_CACHE_LOCK:
        _ROBLOX_PID_CACHE.pop(u, None)
    with _BANNED_ACCOUNTS_LOCK:
        _BANNED_ACCOUNTS.discard(username)


def _handle_reissued_webrb_usernames(reissued_usernames):
    if not reissued_usernames:
        return
    _prune_usernames_from_locked_file("switched/storeddata.txt", reissued_usernames)
    _prune_usernames_from_locked_file("switched/deadcookie.txt", reissued_usernames)
    _prune_usernames_from_locked_file("switched/banned.txt", reissued_usernames)
    for u in reissued_usernames:
        _clear_reissued_webrb_runtime_state(u)


def _merge_incoming_cookie_lines_into_cookie_file(cookie_lines, target_file="cookie.txt"):
    if not cookie_lines:
        return
    incoming_map = _build_cookie_sync_username_map(cookie_lines)
    existing_lines = read_lines_locked(target_file)
    existing_map = _build_cookie_sync_username_map(existing_lines)

    reissued = [u for u in incoming_map if u in existing_map]
    if reissued:
        _handle_reissued_webrb_usernames(reissued)

    updated_lines = [l for l in existing_lines if _normalize_cookie_sync_username(_parse_cookie_sync_account_line(l)[0]) not in incoming_map]
    for raw in cookie_lines:
        u, _, c = _parse_cookie_sync_account_line(raw)
        if u and c:
            updated_lines.append(f"{u}:{c}\n")
    _write_text_lines_locked(target_file, updated_lines)


def _mark_dead_cookie(cookie_entry):
    global totaldeadcookie
    totaldeadcookie += 1
    token = cookie_entry.get("cookie") if isinstance(cookie_entry, dict) else str(cookie_entry)
    if token:
        try:
            append_line_locked("switched/deadcookie.txt", token)
            remove_valid_cookie_entry(token)
        except Exception:
            pass


_COOKIE_IDENTITY_CACHE = {}


def _cache_cookie_identity(cookie_val, user_id, username):
    _COOKIE_IDENTITY_CACHE[cookie_val[:32]] = {
        "user_id": user_id,
        "username": username,
        "timestamp": time.time(),
    }


def _get_fresh_cookie_cache_entry(cookie_val):
    entry = _COOKIE_IDENTITY_CACHE.get(cookie_val[:32])
    if entry and time.time() - entry["timestamp"] < 300:
        return entry
    return None


# CSRF Token Cache
_CSRF_CACHE = {}
_CSRF_CACHE_LOCK = threading.Lock()


def _csrf_cache_key(cookie_val):
    return cookie_val[:32] if cookie_val else "default"


def _request_with_cached_csrf(method, url, headers=None, cookies=None, **kwargs):
    req_headers = _roblox_api_headers(headers)
    cookie_str = ""
    if cookies:
        if isinstance(cookies, dict):
            cookie_str = cookies.get(".ROBLOSECURITY", "")
        else:
            cookie_str = str(cookies)
    key = _csrf_cache_key(cookie_str)
    with _CSRF_CACHE_LOCK:
        cached_csrf = _CSRF_CACHE.get(key)
    if cached_csrf:
        req_headers["x-csrf-token"] = cached_csrf

    r = requests.request(method, url, headers=req_headers, cookies=cookies, **kwargs)
    if r.status_code == 403 and "x-csrf-token" in r.headers:
        new_csrf = r.headers["x-csrf-token"]
        with _CSRF_CACHE_LOCK:
            _CSRF_CACHE[key] = new_csrf
        req_headers["x-csrf-token"] = new_csrf
        r = requests.request(method, url, headers=req_headers, cookies=cookies, **kwargs)
    return r


def _request_auth_ticket_with_csrf_cache(session, cookie_val):
    headers = {
        "RBX-Current-Url": "https://www.roblox.com/games/2753915549",
        "Referer": "https://www.roblox.com/",
    }
    cookies = {".ROBLOSECURITY": cookie_val}
    url = "https://auth.roblox.com/v1/authentication-ticket"
    r = _request_with_cached_csrf("POST", url, headers=headers, cookies=cookies)
    ticket = r.headers.get("rbx-authentication-ticket")
    return ticket


def _normalize_check_cookie_proxy_url(proxy_url):
    if not proxy_url:
        return None
    p = str(proxy_url).strip()
    if not p.startswith("http://") and not p.startswith("https://"):
        p = "http://" + p
    return p


def _get_check_cookie_proxy_urls_for_session(cfg=None):
    if not cfg:
        cfg = read_file("config.json") or {}
    if not cfg.get("Use Proxies For Check Cookie", False):
        return []
    proxy_file = os.path.join(RUNTIME_DIR, "proxies.txt")
    if os.path.isfile(proxy_file):
        lines = read_lines_locked(proxy_file)
        return [_normalize_check_cookie_proxy_url(l) for l in lines if l.strip()]
    return []


async def _lookup_roblox_user_by_username_async(username, session=None):
    url = "https://users.roblox.com/v1/usernames/users"
    payload = {"usernames": [username], "excludeBannedUsers": False}
    close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        close_session = True
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("data"):
                    return data["data"][0]
    except Exception:
        pass
    finally:
        if close_session:
            await session.close()
    return None


def _lookup_roblox_user_by_username(username):
    return _run_coro_sync(_lookup_roblox_user_by_username_async(username))


def _resolve_named_cookie_with_quick_api(cookie_val):
    url = "https://users.roblox.com/v1/users/authenticated"
    headers = _roblox_api_headers()
    cookies = {".ROBLOSECURITY": cookie_val}
    try:
        r = requests.get(url, headers=headers, cookies=cookies, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("id"), data.get("name")
    except Exception:
        pass
    return None, None


def _run_coro_sync(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


async def _quick_check_cookie_async(cookie_entry, session=None):
    cookie_val = (
        cookie_entry.get("cookie")
        if isinstance(cookie_entry, dict)
        else str(cookie_entry)
    )
    url = "https://users.roblox.com/v1/users/authenticated"
    headers = {"User-Agent": ROBLOX_API_USER_AGENT, "Accept": "application/json"}
    cookies = {".ROBLOSECURITY": cookie_val}
    try:
        async with session.get(
            url,
            headers=headers,
            cookies=cookies,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return "valid", data.get("id"), data.get("name")
            if resp.status == 401:
                return "dead", None, None
            return "timeout", None, None
    except Exception:
        return "timeout", None, None


def _build_check_cookie_requests_proxies(proxy_url):
    if not proxy_url:
        return None
    p = _normalize_check_cookie_proxy_url(proxy_url)
    return {"http": p, "https": p}


def _session_get_with_check_cookie_proxy_retry(session, url, **kwargs):
    return session.get(url, **kwargs)


# Prefill & Private Server Distribution
VALID_COOKIES_LOCK = threading.Lock()
valid_cookies = []
valid_cookies_trimmed = []
cookies_list = []
totaldeadcookie = 0
total_timeout = 0
_COOKIE_CHECK_ACTIVITY = 0
_COOKIE_CHECK_ACTIVITY_LOCK = threading.Lock()


def _increment_cookie_check_activity():
    global _COOKIE_CHECK_ACTIVITY
    with _COOKIE_CHECK_ACTIVITY_LOCK:
        _COOKIE_CHECK_ACTIVITY += 1


def _decrement_cookie_check_activity():
    global _COOKIE_CHECK_ACTIVITY
    with _COOKIE_CHECK_ACTIVITY_LOCK:
        _COOKIE_CHECK_ACTIVITY = max(0, _COOKIE_CHECK_ACTIVITY - 1)


def _is_cookie_check_active():
    with _COOKIE_CHECK_ACTIVITY_LOCK:
        return _COOKIE_CHECK_ACTIVITY > 0


def _vip_debug(msg):
    logging.info(f"[VIP] {msg}")


def _load_private_server_lines(path=None):
    ps_file = path or os.path.join("stored", "privateserver.txt")
    if os.path.isfile(ps_file):
        return [l.strip() for l in read_lines_locked(ps_file) if l.strip()]
    return []


def _publish_runtime_valid_cookie_snapshot(cookie_positions, switched_usernames, total_tab):
    banned_snapshot = _get_banned_accounts_snapshot()
    ps_lines = _load_private_server_lines()
    snapshot = publish_valid_cookie_snapshot(
        valid_cookies_trimmed,
        cookie_positions,
        switched_usernames,
        banned_snapshot,
        total_tab,
        ps_lines,
    )
    with VALID_COOKIES_LOCK:
        pass
    return snapshot


def _refresh_valid_cookie_private_servers_from_file():
    ps_lines = _load_private_server_lines()
    with VALID_COOKIES_LOCK:
        refreshed = apply_private_server_lines(valid_cookies, ps_lines)
        assigned_count = sum(1 for c in valid_cookies if isinstance(c, dict) and c.get("private_server"))
    _vip_debug(
        f"refresh privateserver.txt valid_cookies={len(valid_cookies)} ps_lines={len(ps_lines)} assigned={assigned_count}"
    )
    return refreshed


def _maybe_reset_storeddata_and_retry_check_cookie():
    cfg = read_file("config.json") or {}
    if cfg.get("Clear StoredData When No Account", False):
        try:
            write_text_locked("switched/storeddata.txt", "")
        except Exception:
            pass


def _reset_check_cookie_retry_state():
    pass


def _check_cookie_unlocked(thread_count, refresh_switched, suppress_fatal_exit=False):
    global valid_cookies, valid_cookies_trimmed, cookies_list, totaldeadcookie, total_timeout
    raw_lines = read_lines_locked("cookie.txt")
    cookies_list = [l.strip() for l in raw_lines if l.strip()]
    switched_lines = read_lines_locked("switched/storeddata.txt") if not refresh_switched else []
    switched_usernames = {
        _normalize_cookie_sync_username(_parse_cookie_sync_account_line(l)[0])
        for l in switched_lines
    }

    total_tab = int((read_file("config.json") or {}).get("Total Instance", 10))
    startup_ready_sent = False
    startup_ready_event = threading.Event()
    cookie_positions = {}
    found_valid = []

    def check_worker(entry):
        nonlocal startup_ready_sent
        u, _, c = _parse_cookie_sync_account_line(entry)
        if not c:
            return
        if _normalize_cookie_sync_username(u) in switched_usernames:
            return
        status, uid, name = _resolve_named_cookie_with_quick_api(c)
        if status == 200 or uid:
            username_resolved = name or u
            cookie_dict = {"username": username_resolved, "cookie": c, "user_id": uid}
            with VALID_COOKIES_LOCK:
                found_valid.append(cookie_dict)
                curr_cnt = len(found_valid)
            _set_valid_cookie_tracker_timestamp(username_resolved)
            _publish_runtime_valid_cookie_snapshot(cookie_positions, switched_usernames, total_tab)
            status_obj = build_prefill_status(curr_cnt, total_tab, startup_ready_sent)
            if status_obj.should_signal_ready:
                startup_ready_event.set()
                startup_ready_sent = True
        elif status == 401:
            _mark_dead_cookie({"username": u, "cookie": c})

    with ThreadPoolExecutor(max_workers=min(thread_count, 30)) as executor:
        futures = [executor.submit(check_worker, entry) for entry in cookies_list]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception:
                pass

    with VALID_COOKIES_LOCK:
        valid_cookies = found_valid
        _refresh_valid_cookie_private_servers_from_file()
        valid_cookies_trimmed = valid_cookies[:total_tab]

    return len(valid_cookies)


def check_cookie(thread_count=30, refresh_switched=False, suppress_fatal_exit=False):
    _increment_cookie_check_activity()
    try:
        return _check_cookie_unlocked(thread_count, refresh_switched, suppress_fatal_exit)
    finally:
        _decrement_cookie_check_activity()


def _startup_check_cookie_worker(thread_count, ready_event, done_event):
    try:
        check_cookie(thread_count=thread_count)
        if ready_event:
            ready_event.set()
    finally:
        if done_event:
            done_event.set()


def _wait_for_startup_cookie_prefill(thread_count=30):
    ready_event = threading.Event()
    done_event = threading.Event()
    t = threading.Thread(
        target=_startup_check_cookie_worker,
        args=(thread_count, ready_event, done_event),
        daemon=True,
        name="StartupCookiePrefill",
    )
    t.start()
    ready_event.wait(timeout=15.0)


def build_cookie_report_line(
    total_cookies,
    configured_cookies,
    valid_cookies_count,
    dead_cookies_count,
    check_failed_count,
    switched_count,
    checking=False,
):
    checking_tag = " (Checking...)" if checking else ""
    return (
        f"Cookies: Total={total_cookies} | Configured={configured_cookies} | "
        f"Valid={valid_cookies_count} | Dead={dead_cookies_count} | "
        f"Failed={check_failed_count} | Switched={switched_count}{checking_tag}"
    )


# High RAM Tab Killer
def _get_highest_ram_roblox_process():
    highest_proc = None
    max_rss = 0
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            if (p.info.get("name") or "").lower() == "robloxplayerbeta.exe":
                rss = p.info["memory_info"].rss
                if rss > max_rss:
                    max_rss = rss
                    highest_proc = p
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return highest_proc, max_rss


def _kill_highest_ram_roblox_tab_when_system_ram_high(threshold_percent=None):
    if threshold_percent is None:
        cfg = read_file("config.json") or {}
        threshold_percent = _normalize_system_ram_tab_kill_threshold(
            cfg.get(_SYSTEM_RAM_TAB_KILL_KEY, 0)
        )
    if threshold_percent <= 0:
        return
    while True:
        try:
            curr_ram = get_ram_usage()
            if curr_ram >= threshold_percent:
                proc, rss = _get_highest_ram_roblox_process()
                if proc:
                    logging.warning(
                        f"System RAM {curr_ram}% >= threshold {threshold_percent}%. "
                        f"Killing highest RAM Roblox tab PID {proc.pid} (RSS: {rss // (1024*1024)} MB)"
                    )
                    proc.kill()
                    time.sleep(10)
        except Exception:
            pass
        time.sleep(10)


# User Data & File Helpers
def save_user_data(data, file_path="stored/savedata.txt"):
    lines = [f"{k}:{v}" for k, v in data.items()]
    write_text_locked(file_path, "\n".join(lines) + "\n")


def _read_text_with_fallback(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            pass
        except Exception:
            break
    return ""


def load_user_data(file_path="stored/savedata.txt"):
    loaded = {}
    if not os.path.isfile(file_path):
        return loaded
    lines = read_lines_locked(file_path)
    for line in lines:
        line = line.strip()
        if ":" in line:
            parts = line.split(":", 1)
            loaded[parts[0].strip()] = parts[1].strip()
    return loaded


def read_cookie_file(cookie_file="cookie.txt"):
    if not os.path.isfile(cookie_file):
        return []
    lines = read_lines_locked(cookie_file)
    return [l.strip() for l in lines if l.strip()]


# Workspace Heartbeat Cache Thread
_WORKSPACE_HEARTBEAT_CACHE = {}
_WORKSPACE_HEARTBEAT_LOCK = threading.Lock()


def _normalize_checkyum_username_from_filename(filename):
    name = os.path.basename(filename)
    if name.endswith("_checkyum.txt"):
        return name[:-13].strip().lower()
    return ""


def _refresh_workspace_heartbeat_cache():
    ws = _resolve_workspace_folder()
    if not ws or not os.path.isdir(ws):
        return
    now = time.time()
    try:
        for entry in os.scandir(ws):
            if entry.is_file() and entry.name.endswith("_checkyum.txt"):
                u = _normalize_checkyum_username_from_filename(entry.name)
                if u:
                    try:
                        mtime = entry.stat().st_mtime
                        with _WORKSPACE_HEARTBEAT_LOCK:
                            _WORKSPACE_HEARTBEAT_CACHE[u] = mtime
                    except Exception:
                        pass
    except Exception:
        pass


def _has_fresh_cached_online_heartbeat(username, max_age_seconds=120):
    u = _normalize_check_sync_username(username) if "_normalize_check_sync_username" in globals() else str(username).strip().lower()
    with _WORKSPACE_HEARTBEAT_LOCK:
        mtime = _WORKSPACE_HEARTBEAT_CACHE.get(u, 0)
    return (time.time() - mtime) <= max_age_seconds


def _workspace_heartbeat_cache_loop():
    while True:
        try:
            _refresh_workspace_heartbeat_cache()
        except Exception:
            pass
        time.sleep(5)


def _start_workspace_heartbeat_cache_thread():
    t = threading.Thread(
        target=_workspace_heartbeat_cache_loop,
        daemon=True,
        name="WorkspaceHeartbeatCache",
    )
    t.start()


# HTTP Broker Client (SIOThreadClient)
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
        self.base_url = base_url.rstrip("/")
        auth_data = _get_remote_error_auth_payload()
        self.user_id = str(user_id or auth_data.get("UserID (UUID)", "")).strip()
        self.user_key = str(user_key or auth_data.get("Key", "")).strip()
        self.on_action = on_action
        self.on_error = on_error
        self.wait_timeout = wait_timeout
        self._running = False
        self._thread = None
        self._heartbeat_thread = None
        self._session = None

    def start(self, daemon=True):
        if self._running:
            return
        self._running = True
        self._session = requests.Session()
        self._thread = threading.Thread(target=self._run_loop, daemon=daemon, name="BrokerRunner")
        self._thread.start()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True, name="BrokerHeartbeat")
        self._heartbeat_thread.start()

    def stop(self, timeout=5):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def is_running(self):
        return self._running

    def _run_loop(self):
        while self._running:
            try:
                self._runner()
            except Exception as exc:
                self._notify_error({"error": str(exc)})
                time.sleep(2)

    def _read_file_text(self, path):
        return read_text_locked(path)

    def _get_http(self, timeout=None):
        return self._session or requests.Session()

    def _notify_error(self, payload, result=None):
        if self.on_error:
            try:
                self.on_error(payload)
            except Exception:
                pass

    def _build_broker_headers(self):
        return {
            "Content-Type": "application/json",
            "X-Client-ID": self.user_id,
            "X-Client-Key": self.user_key,
            "User-Agent": ROBLOX_API_USER_AGENT,
        }

    def _build_broker_url(self, path):
        return f"{self.base_url}/{path.lstrip('/')}"

    def _sleep_or_stop(self, seconds):
        start = time.time()
        while self._running and (time.time() - start < seconds):
            time.sleep(0.2)

    def _command_id_from_source(self, source_data):
        if isinstance(source_data, dict):
            return source_data.get("command_id") or source_data.get("id")
        return None

    def _copy_json(self, value):
        try:
            return json.loads(json.dumps(value))
        except Exception:
            return value

    def _normalize_broker_status(self, status):
        return _normalize_solve_captcha_status(status)

    def _normalize_broker_user_keys(self, values):
        normalized = []
        if isinstance(values, list):
            for v in values:
                if v and str(v).strip():
                    normalized.append(str(v).strip())
        elif values:
            normalized.append(str(values).strip())
        return normalized

    def _payload_user_key_matches(self, payload):
        if not isinstance(payload, dict):
            return False
        keys = self._normalize_broker_user_keys(payload.get("user_keys") or payload.get("key"))
        if not keys:
            return True
        return self.user_key in keys

    def _normalize_broker_command(self, command):
        if not isinstance(command, dict):
            return None
        return command

    def _build_result_payload(self, source_data, result):
        cid = self._command_id_from_source(source_data)
        return {
            "command_id": cid,
            "user_id": self.user_id,
            "result": result,
            "timestamp": time.time(),
        }

    def _post_broker_result(self, source_data, status, message=None, result=None):
        cid = self._command_id_from_source(source_data)
        if not cid:
            return
        url = self._build_broker_url(f"commands/{cid}/result")
        payload = {
            "status": status,
            "message": message or "",
            "result": result or {},
            "user_id": self.user_id,
        }
        try:
            s = self._get_http()
            s.post(url, json=payload, headers=self._build_broker_headers(), timeout=10)
        except Exception as exc:
            logging.error(f"Post broker result error: {exc}")

    def _pull_next_command(self, session):
        url = self._build_broker_url("commands/pull")
        payload = {"user_id": self.user_id, "user_key": self.user_key}
        try:
            r = session.post(url, json=payload, headers=self._build_broker_headers(), timeout=self.wait_timeout)
            if r.status_code == 200:
                data = r.json()
                return data.get("command") or data
        except Exception:
            pass
        return None

    def _send_heartbeat(self, session=None):
        s = session or self._get_http()
        url = self._build_broker_url("clients/heartbeat")
        payload = {
            "user_id": self.user_id,
            "user_key": self.user_key,
            "version": version,
            "timestamp": time.time(),
        }
        try:
            s.post(url, json=payload, headers=self._build_broker_headers(), timeout=5)
        except Exception:
            pass

    def _heartbeat_loop(self):
        while self._running:
            self._send_heartbeat()
            self._sleep_or_stop(30)

    def _action_to_post(self, action):
        return f"{self.base_url}/api/{action}"

    def _emit_client_action(self, payload):
        if self.on_action:
            try:
                self.on_action(payload)
            except Exception as e:
                logging.error(f"Emit client action error: {e}")

    def _emit_client_ack(self, payload):
        pass

    def _send_action_ack(
        self,
        requested_action,
        status="success",
        user_id=None,
        user_key=None,
        source_data=None,
        **kwargs,
    ):
        self._post_broker_result(source_data, status, result=kwargs)

    def _send_action_payload(
        self, action, user_id=None, user_key=None, source_data=None, **kwargs
    ):
        pass

    def _write_text(self, path, text):
        write_text_locked(path, text)

    def _write_json_pretty(self, path, raw_text):
        try:
            obj = json.loads(raw_text)
            write_json_locked(path, obj)
        except Exception:
            write_text_locked(path, raw_text)

    def _refresh_remote_settings(self):
        pass

    def _apply_incoming_action(self, action, data):
        if action == "update_cookie":
            lines = data.get("cookies", [])
            _merge_incoming_cookie_lines_into_cookie_file(lines)
        elif action == "delete_file":
            fn = data.get("file_name")
            if fn:
                _delete_webrb_changeacc_file_and_sync(fn)

    def _handle_action_event(self, data, event_name="action"):
        action = data.get("action")
        if action:
            self._apply_incoming_action(action, data)
        self._emit_client_action(data)

    def _runner(self):
        session = self._get_http()
        cmd = self._pull_next_command(session)
        if cmd:
            self._handle_action_event(cmd)
        else:
            time.sleep(1)


def start_realtime_client(on_action, on_error):
    client = SIOThreadClient(on_action=on_action, on_error=on_error)
    client.start(daemon=True)
    return client


# Conditions & Change Account
def check_conditions(data):
    if not isinstance(data, dict):
        return False
    return True


def check_int_conditions(data, field):
    try:
        val = int(data.get(field, 0))
        return val > 0
    except Exception:
        return False


def pushchange(file_path):
    pass


def _read_changeacc_text(path):
    return read_text_locked(path)


def _normalize_changeacc_text(value):
    if not value:
        return ""
    return str(value).strip()


def _count_changeacc_lines(path):
    return len(read_lines_locked(path))


def _build_webrb_changeacc_manifest():
    ws = _resolve_workspace_folder()
    manifest = {}
    if ws and os.path.isdir(ws):
        for entry in os.scandir(ws):
            if entry.is_file() and entry.name.endswith(".txt"):
                manifest[entry.name] = entry.stat().st_size
    return manifest


def _sync_changeacc_manifest_sync(manifest=None):
    pass


async def _sync_changeacc_manifest_async(manifest=None):
    pass


def _upload_changeacc_sync(path):
    pass


async def _upload_changeacc_async(path):
    pass


def _normalize_changeacc_target_file_name(name):
    if not name:
        return ""
    return os.path.basename(str(name).strip())


def _extract_changeacc_target_file_name(entry):
    if isinstance(entry, dict):
        return entry.get("file_name") or entry.get("name") or ""
    return str(entry).strip()


def _collect_changeacc_delete_matches(target_name):
    norm = _normalize_changeacc_target_file_name(target_name).lower()
    matches = []
    ws = _resolve_workspace_folder()
    if ws and os.path.isdir(ws):
        for root, dirs, files in os.walk(ws):
            for f in files:
                if f.lower() == norm:
                    matches.append(os.path.join(root, f))
    return matches


def _cleanup_empty_changeacc_dirs(path):
    pass


def _delete_webrb_changeacc_file_and_sync(target_name):
    matches = _collect_changeacc_delete_matches(target_name)
    for p in matches:
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass


# Cryptography & Windows CNG
def xor_decrypt(ciphertext_hex, key):
    try:
        data = bytes.fromhex(ciphertext_hex)
        key_bytes = key.encode("utf-8") if isinstance(key, str) else bytes(key)
        key_len = len(key_bytes)
        decrypted = bytes(b ^ key_bytes[i % key_len] for i, b in enumerate(data))
        return decrypted.decode("utf-8", errors="ignore")
    except Exception:
        return ""


# Windows CNG (bcrypt.dll)
_BCRYPT = None
_CNG_INITIALIZED = False
_CNG_AVAILABLE = False


def _init_windows_cng_api():
    global _BCRYPT, _CNG_INITIALIZED, _CNG_AVAILABLE
    if _CNG_INITIALIZED:
        return _CNG_AVAILABLE
    _CNG_INITIALIZED = True
    try:
        _BCRYPT = ctypes.windll.bcrypt
        _CNG_AVAILABLE = True
    except Exception:
        _CNG_AVAILABLE = False
    return _CNG_AVAILABLE


def _raise_ntstatus(status, operation):
    if status != 0:
        raise OSError(f"Windows CNG error in {operation}: NTSTATUS 0x{status:08X}")


def _bcrypt_get_ulong_property(h_alg, prop_name):
    val = wintypes.ULONG()
    cb_result = wintypes.ULONG()
    status = _BCRYPT.BCryptGetProperty(
        h_alg,
        ctypes.c_wchar_p(prop_name),
        byref(val),
        ctypes.sizeof(val),
        byref(cb_result),
        0,
    )
    _raise_ntstatus(status, f"BCryptGetProperty({prop_name})")
    return val.value


def _windows_cng_crypt(key_bytes, iv_bytes, input_bytes, encrypt=True):
    if not _init_windows_cng_api():
        raise RuntimeError("Windows CNG API not available")
    h_alg = wintypes.HANDLE()
    status = _BCRYPT.BCryptOpenAlgorithmProvider(byref(h_alg), ctypes.c_wchar_p("AES"), None, 0)
    _raise_ntstatus(status, "BCryptOpenAlgorithmProvider")
    try:
        status = _BCRYPT.BCryptSetProperty(
            h_alg,
            ctypes.c_wchar_p("ChainingMode"),
            ctypes.c_wchar_p("ChainingModeCBC"),
            len("ChainingModeCBC") * 2 + 2,
            0,
        )
        _raise_ntstatus(status, "BCryptSetProperty(ChainingMode)")

        obj_length = _bcrypt_get_ulong_property(h_alg, "ObjectLength")
        key_obj = (ctypes.c_ubyte * obj_length)()
        h_key = wintypes.HANDLE()
        key_buf = (ctypes.c_ubyte * len(key_bytes)).from_buffer_copy(key_bytes)
        status = _BCRYPT.BCryptGenerateSymmetricKey(
            h_alg, byref(h_key), key_obj, obj_length, key_buf, len(key_bytes), 0
        )
        _raise_ntstatus(status, "BCryptGenerateSymmetricKey")
        try:
            iv_copy = (ctypes.c_ubyte * len(iv_bytes)).from_buffer_copy(iv_bytes)
            in_buf = (ctypes.c_ubyte * len(input_bytes)).from_buffer_copy(input_bytes)
            cb_result = wintypes.ULONG()
            if encrypt:
                status = _BCRYPT.BCryptEncrypt(
                    h_key, in_buf, len(input_bytes), None, iv_copy, len(iv_bytes), None, 0, byref(cb_result), 0
                )
                _raise_ntstatus(status, "BCryptEncrypt(size)")
                out_buf = (ctypes.c_ubyte * cb_result.value)()
                status = _BCRYPT.BCryptEncrypt(
                    h_key, in_buf, len(input_bytes), None, iv_copy, len(iv_bytes), out_buf, cb_result.value, byref(cb_result), 0
                )
                _raise_ntstatus(status, "BCryptEncrypt")
            else:
                status = _BCRYPT.BCryptDecrypt(
                    h_key, in_buf, len(input_bytes), None, iv_copy, len(iv_bytes), None, 0, byref(cb_result), 0
                )
                _raise_ntstatus(status, "BCryptDecrypt(size)")
                out_buf = (ctypes.c_ubyte * cb_result.value)()
                status = _BCRYPT.BCryptDecrypt(
                    h_key, in_buf, len(input_bytes), None, iv_copy, len(iv_bytes), out_buf, cb_result.value, byref(cb_result), 0
                )
                _raise_ntstatus(status, "BCryptDecrypt")
            return bytes(out_buf[:cb_result.value])
        finally:
            _BCRYPT.BCryptDestroyKey(h_key)
    finally:
        _BCRYPT.BCryptCloseAlgorithmProvider(h_alg, 0)


def _try_warmup_windows_cng():
    try:
        k = b"\x00" * 16
        iv = b"\x00" * 16
        _windows_cng_crypt(k, iv, k, encrypt=True)
        return True
    except Exception:
        return False


def _pkcs7_pad(data, block_size=16):
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _pkcs7_unpad(data, block_size=16):
    if not data or len(data) % block_size != 0:
        raise ValueError("Invalid PKCS7 padding length")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > block_size or data[-pad_len:] != bytes([pad_len] * pad_len):
        raise ValueError("Invalid PKCS7 padding bytes")
    return data[:-pad_len]


def _encrypt_cbc_via_windows_cng(key_bytes, iv_bytes, plaintext):
    padded = _pkcs7_pad(plaintext, 16)
    return _windows_cng_crypt(key_bytes, iv_bytes, padded, encrypt=True)


def _decrypt_cbc_via_windows_cng(key_bytes, iv_bytes, ciphertext):
    dec = _windows_cng_crypt(key_bytes, iv_bytes, ciphertext, encrypt=False)
    return _pkcs7_unpad(dec, 16)


def _decrypt_cbc_via_cryptography(key_bytes, iv_bytes, ciphertext):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv_bytes))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    return _pkcs7_unpad(padded, 16)


def aes_decrypt(ciphertext, key, iv):
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(iv, str):
        iv = iv.encode("utf-8")
    try:
        return _decrypt_cbc_via_windows_cng(key, iv, ciphertext)
    except Exception:
        pass
    if AES is not None:
        try:
            cipher = AES.new(key, AES.MODE_CBC, iv)
            padded = cipher.decrypt(ciphertext)
            return unpad(padded, 16)
        except Exception:
            pass
    try:
        return _decrypt_cbc_via_cryptography(key, iv, ciphertext)
    except Exception:
        pass
    return b""


def aes_encrypt(plaintext, key, iv):
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(iv, str):
        iv = iv.encode("utf-8")
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    try:
        return _encrypt_cbc_via_windows_cng(key, iv, plaintext)
    except Exception:
        pass
    if AES is not None:
        try:
            cipher = AES.new(key, AES.MODE_CBC, iv)
            return cipher.encrypt(pad(plaintext, 16))
        except Exception:
            pass
    return b""


def get_hardware_info():
    info_parts = []
    try:
        with reg.OpenKey(
            reg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\BIOS",
        ) as key:
            serial, _ = reg.QueryValueEx(key, "BaseBoardSerialNumber")
            if serial and serial.strip():
                info_parts.append(serial.strip())
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["wmic", "baseboard", "get", "serialnumber"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = [l.strip() for l in r.stdout.splitlines() if l.strip() and "serialnumber" not in l.lower()]
        if lines:
            info_parts.append(lines[0])
    except Exception:
        pass
    if not info_parts:
        try:
            r = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", "(Get-CimInstance Win32_BaseBoard).SerialNumber"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            val = r.stdout.strip()
            if val:
                info_parts.append(val)
        except Exception:
            pass
    raw = ":".join(info_parts) if info_parts else platform.node()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Roblox Execution Loops & Window Controls
def checkcode(valid, textvalid):
    return True


def startroblox(cookie_file, process_name="RobloxPlayerBeta.exe"):
    global valid_cookies, valid_cookies_trimmed
    cfg = read_file("config.json") or {}
    threads = get_launch_roblox_threads(cfg)
    total_instance = int(cfg.get("Total Instance", 10))

    with VALID_COOKIES_LOCK:
        available = list(valid_cookies_trimmed)

    if not available:
        return

    place_id = cfg.get("PlaceId", "2753915549")
    for cookie_entry in available:
        username = cookie_entry.get("username", "")
        cookie_val = cookie_entry.get("cookie", "")
        if not cookie_val:
            continue
        if _is_banned_account(username):
            continue
        running_pid = trackrobloxnameandgetpid(username)
        if running_pid:
            continue

        ticket = _request_auth_ticket_with_csrf_cache(requests.Session(), cookie_val)
        if not ticket:
            continue

        browser_id = str(uuid.uuid4())
        _set_browser_to_user_map_entry(browser_id, cookie_entry.get("user_id", ""), username)
        _track_username(username)

        cmd = (
            f'start roblox-player:1+launchmode:play+gameinfo:{ticket}+launchtime:{int(time.time()*1000)}'
            f'+placelauncherurl:https%3A%2F%2Fassetgame.roblox.com%2Fgame%2FPlaceLauncher.ashx%3Frequest%3DRequestGame'
            f'%26browserTrackerId%3D{browser_id}%26placeId%3D{place_id}%26isPlayTogetherGame%3Dfalse'
        )
        try:
            launch_with_webview2_env_throttled(cmd)
        except Exception:
            shell_execute_open_throttled(cmd)

        time.sleep(float(cfg.get("Threading Delay", 3.0)))


def restartroblox(process_name="RobloxPlayerBeta.exe"):
    while True:
        try:
            cfg = read_file("config.json") or {}
            max_inst = int(cfg.get("Total Instance", 10))
            curr = get_process_count(process_name)
            if curr < max_inst:
                startroblox("cookie.txt", process_name)
        except Exception:
            pass
        time.sleep(15)


def close_specific_windows(titles_to_close):
    user32 = ctypes.windll.user32
    while True:
        try:
            for title in titles_to_close:
                hwnd = user32.FindWindowW(None, title)
                if hwnd:
                    user32.PostMessageW(hwnd, 0x0010, 0, 0)
        except Exception:
            pass
        time.sleep(2)


def set_low_priority_for_roblox(process_name="RobloxPlayerBeta.exe"):
    while True:
        try:
            for p in psutil.process_iter(["name"]):
                if (p.info["name"] or "").lower() == process_name.lower():
                    try:
                        p.nice(psutil.IDLE_PRIORITY_CLASS)
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(30)


def openfpsuncloker():
    cfg = read_file("config.json") or {}
    if cfg.get("Fps Unlocker", False):
        exe = os.path.join(RUNTIME_DIR, "allscript", "rbxfpsunlocker.exe")
        if os.path.isfile(exe):
            try:
                subprocess.Popen([exe], cwd=os.path.dirname(exe))
            except Exception:
                pass


def runautoupdate():
    while True:
        time.sleep(300)


def killproc():
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() == "robloxplayerbeta.exe":
                p.kill()
        except Exception:
            pass


def kill_captcha():
    pass


def minimize_all_roblox_windows():
    user32 = ctypes.windll.user32
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if (p.info["name"] or "").lower() == "robloxplayerbeta.exe":
                def enum_cb(hwnd, _):
                    wpid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, byref(wpid))
                    if wpid.value == p.info["pid"]:
                        minimize_window(hwnd)
                    return True
                user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(enum_cb), 0)
        except Exception:
            pass


# Seliware Error Message Killer
def scan_seli_errormess():
    pass


def kill_seliware_errormess():
    while True:
        try:
            scan_seli_errormess()
        except Exception:
            pass
        time.sleep(5)


# Window Arrangement & Sorting
def arrange_roblox_windows_once(force_foreground=False):
    tracked = _get_tracked_usernames_snapshot()
    windows = []
    user32 = ctypes.windll.user32

    def enum_cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value
                if "roblox" in title.lower():
                    windows.append(hwnd)
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(enum_cb), 0)

    cfg = read_file("config.json") or {}
    cols = int(cfg.get("Windows Per Rows", 5))
    width = 300
    height = 200
    for idx, hwnd in enumerate(windows):
        r = idx // cols
        c = idx % cols
        x = c * width
        y = r * height
        user32.SetWindowPos(hwnd, 0, x, y, width, height, 0x0040)


def arrange_roblox_windows_hotkey_once():
    arrange_roblox_windows_once(force_foreground=True)


def arrange_roblox_windows():
    while True:
        try:
            arrange_roblox_windows_once()
        except Exception:
            pass
        time.sleep(30)


def sort_roblox_tabs():
    arrange_roblox_windows()


# CPU Affinity & CPU Sets System
class SYSTEM_CPU_SET_DATA(ctypes.Structure):
    _fields_ = [
        ("Id", wintypes.DWORD),
        ("Group", wintypes.WORD),
        ("LogicalProcessorIndex", wintypes.BYTE),
        ("CoreIndex", wintypes.BYTE),
        ("LastLevelCacheIndex", wintypes.BYTE),
        ("NumaNodeIndex", wintypes.BYTE),
        ("EfficiencyClass", wintypes.BYTE),
        ("AllFlags", wintypes.BYTE),
        ("SchedulingClass", wintypes.BYTE),
        ("AllocationTag", wintypes.DWORD),
    ]


class SYSTEM_CPU_SET_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("CpuSet", SYSTEM_CPU_SET_DATA),
    ]


def _fallback_cpu_slots():
    total_cpus = psutil.cpu_count(logical=True) or 4
    slots = []
    for i in range(total_cpus):
        slots.append({
            "id": i,
            "group": 0,
            "logical": i,
            "core": i // 2,
            "flat_index": i,
        })
    return slots


def _spread_cpu_slots_by_core(cpu_slots):
    core_buckets = defaultdict(list)
    for slot in cpu_slots:
        core_buckets[(slot["group"], slot["core"])].append(slot)
    ordered_slots = []
    depth = 0
    bucket_keys = sorted(core_buckets.keys())
    while True:
        added = False
        for bk in bucket_keys:
            b = core_buckets[bk]
            if depth < len(b):
                ordered_slots.append(b[depth])
                added = True
        if not added:
            break
        depth += 1
    return ordered_slots


def _get_cpu_slots():
    kernel32 = ctypes.windll.kernel32
    GetSystemCpuSetInformation = getattr(kernel32, "GetSystemCpuSetInformation", None)
    if not GetSystemCpuSetInformation:
        return _fallback_cpu_slots()
    returned_len = wintypes.ULONG(0)
    GetSystemCpuSetInformation(None, 0, byref(returned_len), None, 0)
    if returned_len.value == 0:
        return _fallback_cpu_slots()
    buffer = ctypes.create_string_buffer(returned_len.value)
    if not GetSystemCpuSetInformation(buffer, returned_len.value, byref(returned_len), None, 0):
        return _fallback_cpu_slots()
    raw_slots = []
    offset = 0
    hdr_size = ctypes.sizeof(SYSTEM_CPU_SET_INFORMATION)
    flat_idx = 0
    while offset + hdr_size <= returned_len.value:
        entry = SYSTEM_CPU_SET_INFORMATION.from_buffer_copy(buffer, offset)
        if entry.Type == 0:
            cpu = entry.CpuSet
            raw_slots.append({
                "id": cpu.Id,
                "group": cpu.Group,
                "logical": cpu.LogicalProcessorIndex,
                "core": cpu.CoreIndex,
                "flat_index": flat_idx,
            })
            flat_idx += 1
        offset += entry.Size if entry.Size > 0 else hdr_size
    if not raw_slots:
        return _fallback_cpu_slots()
    raw_slots.sort(key=lambda s: s["flat_index"])
    return _spread_cpu_slots_by_core(raw_slots)


def _build_affinity_chunks(group_size=4):
    norm_size = max(1, int(group_size or 4))
    slots = _get_cpu_slots()
    chunks = []
    for i in range(0, len(slots), norm_size):
        chunks.append(slots[i : i + norm_size])
    return chunks or [slots]


def _set_process_default_cpu_sets(pid, cpu_slots):
    kernel32 = ctypes.windll.kernel32
    SetProcessDefaultCpuSets = getattr(kernel32, "SetProcessDefaultCpuSets", None)
    if not SetProcessDefaultCpuSets:
        return False
    cpu_ids = [s["id"] for s in cpu_slots]
    PROCESS_SET_INFORMATION = 0x0200
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h_proc = kernel32.OpenProcess(
        PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not h_proc:
        return False
    try:
        arr_type = wintypes.DWORD * len(cpu_ids)
        arr = arr_type(*cpu_ids)
        return bool(SetProcessDefaultCpuSets(h_proc, arr, len(cpu_ids)))
    finally:
        kernel32.CloseHandle(h_proc)


def _set_process_affinity_slots(pid, cpu_slots):
    if _set_process_default_cpu_sets(pid, cpu_slots):
        return True
    try:
        logical_indexes = sorted(
            s.get("flat_index", s.get("logical", 0)) for s in cpu_slots
        )
        proc = psutil.Process(pid)
        proc.cpu_affinity(logical_indexes)
        return True
    except Exception:
        return False


def set_low_priority_and_affinity_4core_group():
    while True:
        try:
            cfg = read_file("config.json") or {}
            if cfg.get("Set Affinity", False):
                group_size = int(cfg.get("Affinity Core", 4))
                chunks = _build_affinity_chunks(group_size)
                procs = []
                for p in psutil.process_iter(["pid", "name"]):
                    if (p.info.get("name") or "").lower() == "robloxplayerbeta.exe":
                        procs.append(p.info["pid"])
                for idx, pid in enumerate(procs):
                    chunk = chunks[idx % len(chunks)]
                    _set_process_affinity_slots(pid, chunk)
        except Exception:
            pass
        time.sleep(150)


# Clean Folder & Webhook
def clean_folder():
    switched_dir = "switched"
    if os.path.isdir(switched_dir):
        for fn in os.listdir(switched_dir):
            if fn in ("storeddata.txt", "not_in_changeacc.txt"):
                continue
            fp = os.path.join(switched_dir, fn)
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
            except Exception:
                pass


def send_webhook():
    while True:
        try:
            cfg = read_file("config.json") or {}
            webhook_url = cfg.get("Webhook Url", "").strip()
            if webhook_url:
                pc_name = socket.gethostname()
                cpu_info = get_cpu_model_windows()
                phys_cores = psutil.cpu_count(logical=False) or 0
                log_threads = psutil.cpu_count(logical=True) or 0
                total_ram = f"{psutil.virtual_memory().total / (1024**3):.2f} GB"
                platform_info = platform.platform()
                ram_pct = f"{get_ram_usage():.1f}%"
                roblox_cnt = get_process_count("RobloxPlayerBeta.exe")

                embed = {
                    "title": "Information",
                    "description": (
                        f"**PC Name**: `{pc_name}`\n"
                        f"**CPU**: `{cpu_info}`\n"
                        f"**CPU Cores/Threads**: `{phys_cores} cores/ {log_threads} threads`\n"
                        f"**Total RAM**: `{total_ram}`\n"
                        f"**Platform**: `{platform_info}`"
                    ),
                    "author": {"name": "PC STATUS"},
                    "color": 65280,
                    "fields": [
                        {"name": "**RAM :film_frames:**", "value": f"```{ram_pct}```", "inline": True},
                        {"name": "**ROBLOX ACTIVE** :video_game:", "value": f"```{roblox_cnt}```", "inline": True},
                    ],
                    "footer": {"text": "| https://discord.gg/yummyauto |"},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                payload = {"embeds": [embed]}
                requests.post(webhook_url, json=payload, timeout=30)
            time.sleep(int(cfg.get("Webhook Delay Send [M]", 60)) * 60)
        except Exception:
            time.sleep(60)


def read_file(file_name):
    files = {
        "cookie.txt": ("cookie.txt", "text"),
        "bloxfruit.json": ("stored/bloxfruit.json", "json"),
        "dfbloxfruit.json": ("stored/dfbloxfruit.json", "json"),
        "pet99.json": ("stored/pet99.json", "json"),
        "config.json": ("config.json", "json"),
    }
    if file_name in files:
        path, mode = files[file_name]
        try:
            if mode == "json":
                return read_json_locked(path, default={})
            return read_text_locked(path)
        except Exception:
            return {} if mode == "json" else ""
    return None


def write_file(file_name, content):
    files = {
        "cookie.txt": ("cookie.txt", "text"),
        "bloxfruit.json": ("stored/bloxfruit.json", "json"),
        "dfbloxfruit.json": ("stored/dfbloxfruit.json", "json"),
        "pet99.json": ("stored/pet99.json", "json"),
        "config.json": ("config.json", "json"),
    }
    if file_name in files:
        path, mode = files[file_name]
        try:
            if mode == "json":
                data = json.loads(content) if isinstance(content, str) else content
                write_json_locked(path, data)
            else:
                write_text_locked(path, str(content))
        except Exception as e:
            logging.error(f"Write File ERROR {file_name}: {e}")


def count_lines_in_file(path):
    if not os.path.isfile(path):
        return 0
    lines = read_lines_locked(path)
    return len([l for l in lines if l.strip()])


def send_payload(ws, payload):
    try:
        ws.send(json.dumps(payload))
    except Exception:
        pass


def build_payload(file_name, content, action, user_id):
    return {
        "action": action,
        "file_name": file_name,
        "content": content,
        "user_id": user_id,
        "timestamp": time.time(),
    }


def on_open(ws):
    content = read_file("cookie.txt")
    payload = build_payload("cookie.txt", content, "upload", RESULT_USER_ID)
    send_payload(ws, payload)


def on_action(action):
    pass


def on_error(_, client_label=None):
    pass


# Signing Key & PoW
def _signing_key_store_path(service, account):
    identity = f"{service}:{account}".strip()
    filename = hashlib.sha256(identity.encode("utf-8", errors="ignore")).hexdigest() + ".key"
    return os.path.join(RUNTIME_DIR, "stored", "signing_keys", filename)


def _read_signing_key_bytes(path):
    if not os.path.isfile(path):
        return None
    try:
        encoded = read_text_locked(path).strip()
        return base64.b64decode(encoded)
    except Exception:
        return None


def _write_signing_key_bytes(path, key_bytes):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        encoded = base64.b64encode(key_bytes).decode("ascii") + "\n"
        write_text_locked(path, encoded)
    except Exception:
        pass


def load_or_create_signing_key(SERVICE, ACCOUNT):
    store_path = _signing_key_store_path(SERVICE, ACCOUNT)
    key_bytes = _read_signing_key_bytes(store_path)
    if key_bytes and len(key_bytes) == 32:
        return SigningKey(key_bytes)
    sk = SigningKey.generate()
    _write_signing_key_bytes(store_path, bytes(sk))
    return sk


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def pow_solve(nonce_b64: str, zeros: int) -> str:
    nonce = base64.urlsafe_b64decode(nonce_b64)
    target_prefix = "0" * zeros
    i = 0
    while True:
        h = hashlib.sha256(nonce + str(i).encode("utf-8")).hexdigest()
        if h.startswith(target_prefix):
            return str(i)
        i += 1


def http_post_retry(url, max_attempts=5, **kwargs):
    last_err = None
    for attempt in range(max_attempts):
        try:
            r = requests.post(url, **kwargs)
            return r
        except Exception as e:
            last_err = e
            time.sleep(1)
    if last_err:
        raise last_err
    return None


def get_webplayer_version():
    url = "https://auth.testwebyummy.click/webplayer-version"
    for _ in range(3):
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                return r.text.strip()
        except Exception:
            pass
        time.sleep(1)
    return "ABC"


def verify_certificate_with_pem(host="yummyauto.app", port=443, pem_path="log/yummeo.pem"):
    return True


def main_flow_then_check_key():
    return True


RESULT_USER_ID = ""
FIXED_KEY = "724c25ba06e56f164da74d098b7068d6"

# Main Entry Point Execution
if __name__ == "__main__":
    auth = read_json_locked("auth.json", default={})
    missing = [f for f in ("UserID (UUID)", "Key", "Note") if not auth.get(f)]
    if missing:
        error(f"The following fields are missing in auth.json: {', '.join(missing)}")
        error("Please fill in UUID, Key, and Note in auth.json.")
        _safe_input("Press Enter to exit...")
        sys.exit(1)

    try:
        uuid.UUID(str(auth["UserID (UUID)"]))
    except Exception:
        error("UserID is not in valid UUID format")
        _safe_input("Press Enter to exit...")
        sys.exit(1)

    RESULT_USER_ID = str(auth["UserID (UUID)"])
    info("Loading Whitelist ...")
    info("Disable IPv6 if this step takes too long.")

    start_global_hotkey_listener()
    _start_workspace_heartbeat_cache_thread()

    cfg = read_file("config.json") or {}
    t_restart = threading.Thread(target=restartroblox, daemon=True, name="RestartRoblox")
    t_restart.start()

    _wait_for_startup_cookie_prefill(thread_count=30)
    _start_vault_captcha_thread()

    place_id = str(cfg.get("PlaceId", "2753915549"))
    if place_id == "2753915549":
        pushchange("allscript/bloxfruitchangeacc.txt")
    elif place_id == "8737899170":
        pushchange("allscript/pet99changeacc.txt")
    pushchange("allscript/checkyummy.lua")

    if cfg.get("Minimize", False):
        t_min = threading.Thread(target=minimize_all_roblox_windows, daemon=True)
        t_min.start()
    elif cfg.get("Sort", False):
        t_sort = threading.Thread(target=arrange_roblox_windows, daemon=True)
        t_sort.start()

    titles_to_close = [
        "Authentication Failed",
        "ERROR",
        "WerFault.exe - Application Error",
        "Error",
        "Notice",
    ]
    t_close = threading.Thread(target=close_specific_windows, args=(titles_to_close,), daemon=True)
    t_close.start()

    if cfg.get("Set Affinity", False):
        t_aff = threading.Thread(target=set_low_priority_and_affinity_4core_group, daemon=True)
        t_aff.start()

    if cfg.get("Set Priority Low", True):
        t_prio = threading.Thread(target=set_low_priority_for_roblox, daemon=True)
        t_prio.start()

    ram_thresh = _normalize_system_ram_tab_kill_threshold(cfg.get(_SYSTEM_RAM_TAB_KILL_KEY, 0))
    if ram_thresh > 0:
        t_ram = threading.Thread(
            target=_kill_highest_ram_roblox_tab_when_system_ram_high,
            args=(ram_thresh,),
            daemon=True,
        )
        t_ram.start()

    t_fps = threading.Thread(target=openfpsuncloker, daemon=True)
    t_fps.start()

    if cfg.get("Webhook Url", "").strip():
        t_hook = threading.Thread(target=send_webhook, daemon=True)
        t_hook.start()

    if cfg.get("Kill Seliware Error Message", True):
        t_seli = threading.Thread(target=kill_seliware_errormess, daemon=True)
        t_seli.start()

    client = start_realtime_client(on_action, on_error)

    # Main Status Loop
    while True:
        try:
            time.sleep(1)
            _clear_console()
            _thread_safe_print("-------------------------------------------------------------------------------------------------------------")
            switched_cnt = count_lines_in_file("switched/storeddata.txt")
            rep = build_cookie_report_line(
                len(cookies_list),
                len(valid_cookies_trimmed),
                len(valid_cookies),
                totaldeadcookie,
                total_timeout,
                switched_cnt,
                _is_cookie_check_active(),
            )
            info(rep)
            _thread_safe_print("-------------------------------------------------------------------------------------------------------------")

            if not valid_cookies:
                warn("No Found More Account please input more. (First check if u changed account in switched/storeddata.txt)")
                warn("Retry check cookie after 30 seconds...")
                time.sleep(30)
                check_cookie(30, refresh_switched=True)
            else:
                startroblox("cookie.txt")
                time.sleep(float(cfg.get("Threading Delay", 3.0)))
        except Exception as exc:
            logging.error("Main status loop exception", exc_info=True, extra={"remote_context": "main-status-loop", "remote_kind": "loop_exception"})
            time.sleep(1)
