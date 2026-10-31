import base64
import io
import json
import os
import platform
import random
import re
import select
import shutil
import socket
import socketserver
import subprocess
import sys
import time
import threading
import unicodedata
import traceback
import zipfile
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp
from urllib.parse import quote, urlparse

import pow
import requests
from colorama import Fore, init

try:
    import winreg
except Exception:
    winreg = None

init(autoreset=True)

_LOG_PRINTER = None


def set_log_printer(printer) -> None:
    global _LOG_PRINTER
    _LOG_PRINTER = printer if callable(printer) else None


def log(message: str, level: str = "~") -> None:
    colors = {
        "~": Fore.CYAN,
        "+": Fore.GREEN,
        "!": Fore.YELLOW,
        "-": Fore.RED,
        "^": Fore.MAGENTA,
    }
    color = colors.get(level.upper(), Fore.WHITE)
    now = datetime.now().strftime("%H:%M:%S")
    printer = _LOG_PRINTER
    if callable(printer):
        try:
            printer(str(message or ""), str(level or "~"))
            return
        except Exception:
            pass
    print(f"{color}[{now}] [{level.upper():^3}] {message}")


def infos(msg: str) -> None:
    log(msg, "~")


def success(msg: str) -> None:
    log(msg, "+")


def warn(msg: str) -> None:
    log(msg, "!")


def error(msg: str) -> None:
    log(msg, "-")


def purple(msg: str) -> None:
    log(msg, "^")


USER_AGENT_FILE = "user-agent.txt"
_user_agents = None
_user_agents_lock = threading.Lock()
CHROMEDRIVER_EXECUTABLE = "chromedriver.exe"
CHROMEDRIVER_BUILD_ENDPOINT = (
    "https://googlechromelabs.github.io/chrome-for-testing/latest-patch-versions-per-build-with-downloads.json"
)
CHROMEDRIVER_MILESTONE_ENDPOINT = (
    "https://googlechromelabs.github.io/chrome-for-testing/latest-versions-per-milestone-with-downloads.json"
)
CHROMEDRIVER_LAST_KNOWN_GOOD_ENDPOINT = (
    "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
)
SOLVE_BROWSER_DEFAULT_TIMEOUT_SECONDS = 300


def _normalize_solve_browser_timeout(value, default_seconds: int = 300) -> int:
    try:
        fallback = max(1, int(default_seconds))
    except Exception:
        fallback = 300
    try:
        normalized = int(float(value))
    except Exception:
        return fallback
    return max(1, normalized)


def _normalize_hybrid_app_type(value, default: str = "windows") -> str:
    raw = str(value or "").strip().lower()
    alias_map = {
        "android": "android",
        "ios": "ios",
        "mobile": "ios",
        "iphone": "ios",
        "ipad": "ios",
        "windows": "windows",
        "win": "windows",
        "desktop": "windows",
    }
    if raw in alias_map:
        return alias_map[raw]
    fallback = str(default or "").strip().lower()
    return alias_map.get(fallback, "windows")


def _load_user_agents():
    exe_dir = Path(sys.argv[0]).resolve().parent if sys.argv else Path.cwd()
    candidates = [
        exe_dir / USER_AGENT_FILE,
        Path(USER_AGENT_FILE),
    ]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            with path.open("r", encoding="utf-8-sig") as f:
                items = [
                    line.strip()
                    for line in f
                    if line.strip() and not line.strip().startswith("#")
                ]
            if items:
                return items
        except Exception:
            continue
    return []


def choose_user_agent():
    global _user_agents
    override_agent = str(
        os.environ.get("YUMMYAUTO_USER_AGENT")
        or os.environ.get("YUMMYAUTO_ROBLOX_USER_AGENT")
        or ""
    ).strip()
    if override_agent:
        return override_agent
    if _user_agents is None:
        with _user_agents_lock:
            if _user_agents is None:
                _user_agents = _load_user_agents()
    if _user_agents:
        return random.choice(_user_agents)
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"


def _close_driver_quietly(driver):
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass


def _summarize_browser_exception(exc) -> str:
    exc_name = type(exc).__name__
    text = str(exc or "").replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith("stacktrace"):
            break
        if lowered.startswith("for documentation on this error"):
            continue
        if stripped.startswith("Message:"):
            stripped = stripped.split(":", 1)[1].strip()
        if stripped:
            raw_lines.append(stripped)
    if exc_name == "SessionNotCreatedException":
        summary_lines = raw_lines[:2]
    else:
        summary_lines = raw_lines[:1]
    summary_text = re.sub(r"\s+", " ", " ".join(summary_lines)).strip()
    if len(summary_text) > 220:
        summary_text = summary_text[:217].rstrip() + "..."
    if summary_text:
        return f"browser exception={exc_name}: {summary_text}"
    return f"browser exception={exc_name}"


def _should_suppress_console_failure_reason(reason) -> bool:
    normalized = str(reason or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith("browser exception=sessionnotcreatedexception:"):
        return True
    if not normalized.startswith("browser exception="):
        return False
    transient_markers = (
        "timed out receiving message from renderer",
        "timed out waiting",
        "not connected to devtools",
        "disconnected:",
        "target frame detached",
    )
    if normalized.startswith("browser exception=timeoutexception:"):
        return any(marker in normalized for marker in transient_markers)
    if normalized.startswith("browser exception=webdriverexception:"):
        return any(marker in normalized for marker in transient_markers)
    return False


def _should_log_safe_get_retry_warning(exc) -> bool:
    exc_name = type(exc).__name__.strip().lower()
    message = str(exc or "").strip().lower()
    if exc_name in ("timeoutexception", "webdriverexception") and "timed out receiving message from renderer" in message:
        return False
    return True


def _should_try_chromedriver_fallback(exc) -> bool:
    if type(exc).__name__ == "NoSuchDriverException":
        return True
    text = str(exc or "").strip().lower()
    if not text:
        return False
    markers = (
        "unable to obtain driver for chrome",
        "unable to obtain chromedriver",
        "unable to discover proper chromedriver version",
        "chromedriver executable needs to be in path",
        "chromedriver executable needs to be available",
        "selenium manager",
        "driver location",
    )
    return any(marker in text for marker in markers)


def _normalize_challenge_type_key(challenge_type) -> str:
    value = str(challenge_type or "").strip().lower()
    if value in ("proofofwork", "proof-of-work", "proof_of_work", "pow"):
        return "proofofwork"
    if value in ("proofofspace", "proof-of-space", "proof_of_space", "pos"):
        return "proofofspace"
    if value in ("", "captcha"):
        return "captcha"
    return value


def _should_skip_challenge_type(challenge_type, captcha_only: bool = False) -> bool:
    if not captcha_only:
        return False
    return _normalize_challenge_type_key(challenge_type) in {"proofofwork", "proofofspace"}


def _should_use_pow_mode_for_browser(challenge_type) -> bool:
    return _normalize_challenge_type_key(challenge_type) == "proofofwork"


def _should_enable_legacy_progress_watch(challenge_type) -> bool:
    return _normalize_challenge_type_key(challenge_type) == "proofofspace"


def _get_challenge_display_name(challenge_type) -> str:
    normalized = _normalize_challenge_type_key(challenge_type)
    if normalized == "proofofwork":
        return "Proof Of Work"
    if normalized == "proofofspace":
        return "Proof Of Space"
    if normalized == "captcha":
        return "Captcha"
    cleaned = str(challenge_type or "").strip().replace("-", " ").replace("_", " ").title()
    return cleaned or "Challenge"


def _extract_version_token(raw_text) -> str:
    match = re.search(r"(\d+\.\d+\.\d+\.\d+)", str(raw_text or ""))
    return match.group(1) if match else ""


def _major_version(version) -> str:
    token = _extract_version_token(version)
    return token.split(".", 1)[0] if token else ""


def _normalized_paths(candidates):
    seen = set()
    resolved = []
    for candidate in candidates:
        if not candidate:
            continue
        try:
            path = Path(candidate).expanduser()
        except Exception:
            continue
        try:
            key = str(path.resolve()).lower()
        except Exception:
            key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    return resolved


def _read_binary_version(binary_path) -> str:
    if not binary_path:
        return ""
    try:
        proc = subprocess.run(
            [str(binary_path), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return ""
    return _extract_version_token(f"{proc.stdout or ''}\n{proc.stderr or ''}")


def _chromedriver_cache_root(base_dir="") -> Path:
    if base_dir:
        return Path(base_dir)
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "yummyauto" / "chromedriver-cache"
    exe_dir = Path(sys.argv[0]).resolve().parent if sys.argv else Path.cwd()
    return exe_dir / ".chromedriver-cache"


def _chromedriver_fixed_driver_path(base_dir="") -> Path:
    if base_dir:
        return Path(base_dir) / CHROMEDRIVER_EXECUTABLE
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "yummyauto" / CHROMEDRIVER_EXECUTABLE
    exe_dir = Path(sys.argv[0]).resolve().parent if sys.argv else Path.cwd()
    return exe_dir / CHROMEDRIVER_EXECUTABLE


def _chromedriver_platform_tag() -> str:
    return "win64"


def _iter_chrome_binary_candidates(chrome_path=""):
    exe_dir = Path(sys.argv[0]).resolve().parent if sys.argv else Path.cwd()
    raw_candidates = [
        chrome_path,
        os.environ.get("CHROME_BINARY", ""),
        os.environ.get("GOOGLE_CHROME_SHIM", ""),
        exe_dir / "chrome.exe",
        Path.cwd() / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("google-chrome"),
    ]
    return [path for path in _normalized_paths(raw_candidates) if path.is_file()]


def _find_browser_version(chrome_path="") -> str:
    for binary in _iter_chrome_binary_candidates(chrome_path=chrome_path):
        version = _read_binary_version(binary)
        if version:
            return version
        parent_version = _extract_version_token(binary.parent.name)
        if parent_version:
            return parent_version
        try:
            sibling_versions = sorted(
                (
                    _extract_version_token(child.name)
                    for child in binary.parent.iterdir()
                    if child.is_dir()
                ),
                key=lambda item: tuple(int(part) for part in item.split(".")) if item else (),
                reverse=True,
            )
            if sibling_versions and sibling_versions[0]:
                return sibling_versions[0]
        except Exception:
            pass
    for version in _iter_windows_chrome_registry_candidates():
        if version:
            return version
    return ""


def _iter_windows_chrome_registry_candidates():
    if winreg is None:
        return []
    key_specs = (
        (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon", "version"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon", "version"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Google\Chrome\BLBeacon", "version"),
        (
            winreg.HKEY_CURRENT_USER,
            r"Software\Google\Update\Clients\{8A69D345-D564-463c-AFF1-A69D9E530F96}",
            "pv",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\Google\Update\Clients\{8A69D345-D564-463c-AFF1-A69D9E530F96}",
            "pv",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\WOW6432Node\Google\Update\Clients\{8A69D345-D564-463c-AFF1-A69D9E530F96}",
            "pv",
        ),
    )
    versions = []
    for root, subkey, value_name in key_specs:
        try:
            with winreg.OpenKey(root, subkey) as key:
                raw_value, _ = winreg.QueryValueEx(key, value_name)
            token = _extract_version_token(raw_value)
            if token:
                versions.append(token)
        except Exception:
            continue
    return versions


def _iter_chromedriver_candidates(search_roots, path_entries, executable_name=CHROMEDRIVER_EXECUTABLE):
    direct_candidates = []
    for entry in path_entries or []:
        if not entry:
            continue
        base = Path(entry)
        if base.is_file():
            direct_candidates.append(base)
        else:
            direct_candidates.append(base / executable_name)
    for root in search_roots or []:
        if not root:
            continue
        base = Path(root)
        if base.is_file():
            direct_candidates.append(base)
            continue
        direct_candidates.append(base / executable_name)
        try:
            if base.is_dir():
                for child in sorted(base.iterdir(), reverse=True):
                    if child.is_dir():
                        direct_candidates.append(child / executable_name)
        except Exception:
            continue
    yielded = set()
    for candidate in _normalized_paths(direct_candidates):
        if not candidate.is_file():
            continue
        key = str(candidate).lower()
        if key in yielded:
            continue
        yielded.add(key)
        yield candidate


def _find_existing_chromedriver(search_roots, path_entries, browser_version="", executable_name=CHROMEDRIVER_EXECUTABLE) -> str:
    expected_major = _major_version(browser_version)
    unknown_matches = []
    for candidate in _iter_chromedriver_candidates(
        search_roots=search_roots,
        path_entries=path_entries,
        executable_name=executable_name,
    ):
        driver_version = _read_binary_version(candidate)
        if not expected_major:
            return str(candidate)
        if driver_version:
            if _major_version(driver_version) == expected_major:
                return str(candidate)
            continue
        parent_major = _major_version(candidate.parent.name)
        if parent_major and parent_major == expected_major:
            return str(candidate)
        if not parent_major:
            unknown_matches.append(str(candidate))
    return unknown_matches[0] if unknown_matches else ""


def _resolve_cft_chromedriver_download(browser_version, platform_tag, build_doc, milestone_doc):
    token = _extract_version_token(browser_version)
    if not token:
        return "", ""
    parts = token.split(".")
    if len(parts) < 3:
        return "", ""
    build_prefix = ".".join(parts[:3])
    milestone = parts[0]

    def _entry_download(entry):
        return _extract_chromedriver_download_from_entry(entry, platform_tag=platform_tag)

    builds = (build_doc or {}).get("builds") or {}
    if build_prefix in builds:
        version, url = _entry_download(builds.get(build_prefix))
        if url:
            return version, url
    milestones = (milestone_doc or {}).get("milestones") or {}
    if milestone in milestones:
        version, url = _entry_download(milestones.get(milestone))
        if url:
            return version, url
    return "", ""


def _extract_chromedriver_download_from_entry(entry, platform_tag):
    if not isinstance(entry, dict):
        return "", ""
    downloads = ((entry.get("downloads") or {}).get("chromedriver")) or []
    for item in downloads:
        if isinstance(item, dict) and item.get("platform") == platform_tag and item.get("url"):
            return str(entry.get("version", "")).strip(), str(item["url"]).strip()
    return "", ""


def _resolve_last_known_good_chromedriver_download(platform_tag, last_known_doc, channel="Stable"):
    channels = (last_known_doc or {}).get("channels") or {}
    entry = channels.get(channel) or {}
    return _extract_chromedriver_download_from_entry(entry, platform_tag=platform_tag)


def resolve_chromedriver_download(browser_version, platform_tag, override_url="", build_doc=None, milestone_doc=None, last_known_doc=None):
    override = str(override_url or "").strip()
    if override:
        version = _extract_version_token(override) or _extract_version_token(browser_version) or "custom"
        return version, override, "override"
    version, url = _resolve_cft_chromedriver_download(
        browser_version=browser_version,
        platform_tag=platform_tag,
        build_doc=build_doc,
        milestone_doc=milestone_doc,
    )
    if url:
        return version, url, "detected"
    version, url = _resolve_last_known_good_chromedriver_download(
        platform_tag=platform_tag,
        last_known_doc=last_known_doc,
        channel="Stable",
    )
    if url:
        return version, url, "stable"
    return "", "", ""


def _install_chromedriver_from_zip(zip_bytes: bytes, destination_dir, executable_name=CHROMEDRIVER_EXECUTABLE) -> str:
    destination_root = Path(destination_dir)
    destination_root.mkdir(parents=True, exist_ok=True)
    target_path = destination_root / executable_name
    if target_path.is_file():
        return str(target_path)
    staging_dir = Path(mkdtemp(prefix="chromedriver-", dir=str(destination_root)))
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            member_name = None
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if Path(info.filename).name.lower() == executable_name.lower():
                    member_name = info.filename
                    break
            if not member_name:
                raise FileNotFoundError("chromedriver binary not found in archive")
            extracted_path = Path(archive.extract(member_name, path=str(staging_dir)))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(extracted_path), str(target_path))
        try:
            target_path.chmod(0o755)
        except Exception:
            pass
        return str(target_path)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def _download_fallback_chromedriver(chrome_path="", base_dir="", chromedriver_download_url="") -> str:
    browser_version = _find_browser_version(chrome_path=chrome_path)
    exe_dir = Path(sys.argv[0]).resolve().parent if sys.argv else Path.cwd()
    cache_root = _chromedriver_cache_root(base_dir=base_dir)
    fixed_driver_path = _chromedriver_fixed_driver_path(base_dir=base_dir)
    search_roots = [
        fixed_driver_path.parent,
        exe_dir,
        exe_dir / "drivers",
        exe_dir / "driver",
        exe_dir / "bin",
        Path.cwd(),
        cache_root,
    ]
    existing_driver = _find_existing_chromedriver(
        search_roots=search_roots,
        path_entries=os.environ.get("PATH", "").split(";"),
        browser_version=browser_version,
    )
    if existing_driver:
        return existing_driver
    platform_tag = _chromedriver_platform_tag()
    override_url = str(chromedriver_download_url or "").strip()
    build_doc = None
    milestone_doc = None
    last_known_doc = None
    try:
        if not override_url:
            if browser_version:
                infos(f"Detected Chrome version for driver fallback: {browser_version}")
                build_resp = requests.get(CHROMEDRIVER_BUILD_ENDPOINT, timeout=20)
                build_resp.raise_for_status()
                milestone_resp = requests.get(CHROMEDRIVER_MILESTONE_ENDPOINT, timeout=20)
                milestone_resp.raise_for_status()
                build_doc = build_resp.json()
                milestone_doc = milestone_resp.json()
            else:
                warn("Chrome version detection failed for driver fallback.")
        resolved_version, download_url, source = resolve_chromedriver_download(
            browser_version=browser_version,
            platform_tag=platform_tag,
            override_url=override_url,
            build_doc=build_doc,
            milestone_doc=milestone_doc,
            last_known_doc=last_known_doc,
        )
        if not download_url and not override_url:
            last_known_resp = requests.get(CHROMEDRIVER_LAST_KNOWN_GOOD_ENDPOINT, timeout=20)
            last_known_resp.raise_for_status()
            last_known_doc = last_known_resp.json()
            resolved_version, download_url, source = resolve_chromedriver_download(
                browser_version=browser_version,
                platform_tag=platform_tag,
                override_url=override_url,
                build_doc=build_doc,
                milestone_doc=milestone_doc,
                last_known_doc=last_known_doc,
            )
        if not download_url:
            return ""
        if source == "override":
            infos("Using configured ChromeDriver download URL override.")
        elif source == "stable":
            infos(f"Using Stable ChromeDriver fallback: {resolved_version or 'unknown'}")
        else:
            infos(f"Using version-matched ChromeDriver fallback: {resolved_version}")
        destination_dir = cache_root / (resolved_version or _major_version(browser_version) or source or "custom")
        cached_driver = destination_dir / CHROMEDRIVER_EXECUTABLE
        if cached_driver.is_file():
            return str(cached_driver)
        archive_resp = requests.get(download_url, timeout=60)
        archive_resp.raise_for_status()
        installed_driver = _install_chromedriver_from_zip(
            zip_bytes=archive_resp.content,
            destination_dir=destination_dir,
        )
        try:
            fixed_driver_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(installed_driver, fixed_driver_path)
            return str(fixed_driver_path)
        except Exception:
            return installed_driver
    except Exception as exc:
        warn(f"Chrome driver fallback failed: {exc}")
        return ""


def solve_captcha(
    username: str,
    cookie_value: str,
    chrome_path: str = "",
    chromedriver_download_url: str = "",
    pow_only: bool = False,
    captcha_only: bool = False,
    extension_path: str = "",
    stage_callback=None,
    solve_timeout_seconds=None,
    strict_join_verification: bool = False,
    hybrid_app_type: str = "windows",
):
    API_METHOD = "POST"
    API_URL = "https://gamejoin.roblox.com/v1/join-game"
    AVATAR_API_URL = "https://avatar.roblox.com/v1/avatar"
    PROXY_FILE = "proxies.txt"
    TIMEOUT = 15.0
    MAX_PROXY_RETRIES = 3
    WINDOW_SIZE = (380, 280)
    WINDOW_GAP = (16, 12)
    TASKBAR_BUFFER = 40
    WINDOW_OFFSET_Y = -30
    browser_timeout_seconds = _normalize_solve_browser_timeout(solve_timeout_seconds)
    normalized_hybrid_app_type = _normalize_hybrid_app_type(hybrid_app_type)
    exe_dir = Path(sys.argv[0]).resolve().parent if sys.argv else Path.cwd()
    selected_user_agent = choose_user_agent()

    def _notify_stage(stage: str):
        if not callable(stage_callback):
            return
        try:
            stage_callback(str(stage or "").strip())
        except Exception:
            pass

    def build_challenge_url(challenge_id: str, challenge_type: str, metadata_b64: str) -> str:
        base_url = "https://www.roblox.com/challenge/cdn/hybrid"
        query_items = [
            f"challenge-id={quote(challenge_id or '')}",
            f"challenge-type={quote(challenge_type or 'captcha')}",
            f"challenge-metadata={quote(metadata_b64 or '')}",
            f"app-type={quote(normalized_hybrid_app_type)}",
            "dark-mode=true",
            "starting-state=ready",
            "origin=https://www.roblox.com",
        ]
        return f"{base_url}?{'&'.join(query_items)}"

    _window_lock = threading.Lock()
    _next_window_slot = 0
    _screen_size = None
    _extension_paths = None

    def get_screen_size():
        nonlocal _screen_size
        if _screen_size:
            return _screen_size
        try:
            import ctypes
            user32 = ctypes.windll.user32
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass
            width = int(user32.GetSystemMetrics(0))
            height = int(user32.GetSystemMetrics(1))
            if width > 0 and height > 0:
                _screen_size = (width, max(200, height - TASKBAR_BUFFER))
                return _screen_size
        except Exception:
            pass
        _screen_size = (1920, 1040)
        return _screen_size

    def allocate_window_slot():
        nonlocal _next_window_slot
        with _window_lock:
            slot = _next_window_slot
            _next_window_slot += 1
        return slot

    def window_position_for_slot(slot: int):
        screen_w, _screen_h = get_screen_size()
        win_w, win_h = WINDOW_SIZE
        step_x = win_w + WINDOW_GAP[0]
        step_y = win_h + WINDOW_GAP[1]
        cols = max(1, screen_w // step_x)
        x = (slot % cols) * step_x
        y = max(0, (slot // cols) * step_y + WINDOW_OFFSET_Y)
        return x, y

    def parse_proxy_line(line: str):
        raw = (line or "").strip()
        if not raw or raw.startswith("#"):
            return None
        for prefix in ("http://", "https://", "socks5://", "socks4://"):
            if raw.lower().startswith(prefix):
                raw = raw[len(prefix):]
                break
        host = port = user = password = None
        if "@" in raw:
            first, second = raw.split("@", 1)
            if ":" in first and ":" in second:
                f1, f2 = first.split(":", 1)
                s1, s2 = second.split(":", 1)
                if s2.isdigit() and not f2.isdigit():
                    user, password = f1, f2
                    host, port = s1, s2
                elif f2.isdigit() and not s2.isdigit():
                    host, port = f1, f2
                    user, password = s1, s2
                else:
                    user, password = f1, f2
                    host, port = s1, s2
        else:
            parts = raw.split(":")
            if len(parts) == 2:
                host, port = parts
            elif len(parts) == 4:
                host, port, user, password = parts
        if not host or not port:
            return None
        try:
            port_num = int(port)
        except ValueError:
            return None
        return {
            "host": host.strip(),
            "port": port_num,
            "user": user.strip() if user else None,
            "password": password.strip() if password else None,
            "raw": line.strip(),
        }

    def load_proxies(path: str):
        candidates = []
        raw_path = Path(path)
        if raw_path.is_absolute():
            candidates.append(raw_path)
        else:
            candidates.append(exe_dir / raw_path)
            candidates.append(raw_path)
        proxy_file = next((p for p in candidates if p.is_file()), None)
        if not proxy_file:
            return []
        proxies = []
        with proxy_file.open("r", encoding="utf-8-sig") as f:
            for line in f:
                parsed = parse_proxy_line(line)
                if parsed:
                    proxies.append(parsed)
        return proxies

    def build_requests_proxies(proxy_info):
        if not proxy_info:
            return None
        auth = ""
        if proxy_info.get("user") and proxy_info.get("password"):
            u = quote(proxy_info["user"], safe="")
            p = quote(proxy_info["password"], safe="")
            auth = f"{u}:{p}@"
        url = f"http://{auth}{proxy_info['host']}:{proxy_info['port']}"
        return {"http": url, "https": url}

    def format_proxy_label(proxy_info):
        if not proxy_info:
            return "NO_PROXY"
        return f"{proxy_info['host']}:{proxy_info['port']}"

    class LocalProxyBridge:
        def __init__(self, upstream):
            self.upstream = upstream
            self.server = None
            self.thread = None
            self.local_port = None

        def start(self):
            upstream = self.upstream

            class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
                allow_reuse_address = True
                daemon_threads = True

            class ProxyHandler(socketserver.StreamRequestHandler):
                def _open_upstream(self):
                    sock = socket.create_connection(
                        (upstream["host"], upstream["port"]), timeout=15
                    )
                    sock.settimeout(15)
                    return sock

                def _auth_header(self):
                    user = upstream.get("user")
                    password = upstream.get("password")
                    if not user or password is None:
                        return b""
                    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
                    return f"Proxy-Authorization: Basic {token}\r\n".encode("iso-8859-1")

                def _pipe_Forever(self, a, b):
                    sockets = [a, b]
                    try:
                        while True:
                            r, _, _ = select.select(sockets, [], [], 30)
                            if not r:
                                break
                            for s in r:
                                data = s.recv(65536)
                                if not data:
                                    return
                                target = b if s is a else a
                                target.sendall(data)
                    except Exception:
                        return

                def handle(self):
                    first_line = self.rfile.readline(65537)
                    if not first_line:
                        return
                    headers = []
                    while True:
                        line = self.rfile.readline(65537)
                        if not line or line in (b"\r\n", b"\n"):
                            break
                        headers.append(line)

                    parts = first_line.decode("iso-8859-1", errors="ignore").strip().split()
                    if len(parts) < 3:
                        return
                    method = parts[0].upper()

                    upstream_sock = None
                    try:
                        upstream_sock = self._open_upstream()
                        auth_line = self._auth_header()

                        if method == "CONNECT":
                            target = parts[1]
                            req = [
                                f"CONNECT {target} HTTP/1.1\r\n".encode("iso-8859-1"),
                                f"Host: {target}\r\n".encode("iso-8859-1"),
                            ]
                            if auth_line:
                                req.append(auth_line)
                            req.append(b"Proxy-Connection: Keep-Alive\r\n\r\n")
                            upstream_sock.sendall(b"".join(req))

                            resp = b""
                            while b"\r\n\r\n" not in resp and len(resp) < 65536:
                                chunk = upstream_sock.recv(4096)
                                if not chunk:
                                    break
                                resp += chunk
                            status_line = resp.split(b"\r\n", 1)[0]
                            if b" 200 " not in status_line:
                                self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                                return
                            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                            self.wfile.flush()
                            self._pipe_Forever(self.connection, upstream_sock)
                        else:
                            filtered = [
                                h
                                for h in headers
                                if not h.lower().startswith(b"proxy-authorization:")
                            ]
                            content_len = 0
                            for h in filtered:
                                if h.lower().startswith(b"content-length:"):
                                    try:
                                        content_len = int(h.split(b":", 1)[1].strip())
                                    except ValueError:
                                        content_len = 0
                            body = self.rfile.read(content_len) if content_len > 0 else b""
                            out = [first_line]
                            out.extend(filtered)
                            if auth_line:
                                out.append(auth_line)
                            out.append(b"\r\n")
                            out.append(body)
                            upstream_sock.sendall(b"".join(out))
                            self._pipe_Forever(self.connection, upstream_sock)
                    except Exception:
                        return
                    finally:
                        if upstream_sock:
                            try:
                                upstream_sock.close()
                            except Exception:
                                pass

            self.server = ThreadedTCPServer(("127.0.0.1", 0), ProxyHandler)
            self.local_port = self.server.server_address[1]
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            return f"http://127.0.0.1:{self.local_port}"

        def close(self):
            if not self.server:
                return
            try:
                self.server.shutdown()
            except Exception:
                pass
            try:
                self.server.server_close()
            except Exception:
                pass
            self.server = None

    def get_chrome_extensions():
        nonlocal _extension_paths
        if _extension_paths is not None:
            return _extension_paths
        ext_paths = []
        raw_override = str(extension_path or "").strip()
        if raw_override:
            override_candidates = []
            p = Path(raw_override)
            if p.is_absolute():
                override_candidates.append(p)
            else:
                override_candidates.append(exe_dir / p)
                override_candidates.append(p)
            for cand in override_candidates:
                if (cand / "manifest.json").is_file():
                    ext_paths.append(str(cand.resolve()))
                    _extension_paths = ext_paths
                    return _extension_paths
                if cand.is_dir():
                    for child in sorted(cand.iterdir()):
                        if child.is_dir() and (child / "manifest.json").is_file():
                            ext_paths.append(str(child.resolve()))
                    if ext_paths:
                        _extension_paths = ext_paths
                        return _extension_paths
            warn(f"Extension path khong hop le hoac thieu manifest.json: {raw_override}")

        ext_root_candidates = [exe_dir / "extensions", Path("extensions")]
        ext_root = next((p for p in ext_root_candidates if p.is_dir()), None)
        if ext_root:
            if (ext_root / "manifest.json").is_file():
                ext_paths.append(str(ext_root.resolve()))
            else:
                for child in sorted(ext_root.iterdir()):
                    if child.is_dir() and (child / "manifest.json").is_file():
                        ext_paths.append(str(child.resolve()))
        _extension_paths = ext_paths
        return _extension_paths

    def configure_extension_api_key(driver):
        return

    def build_mobile_user_agent(desktop_user_agent: str):
        chrome_match = re.search(r"Chrome/(\d+\.\d+\.\d+\.\d+)", desktop_user_agent or "")
        chrome_ver = chrome_match.group(1) if chrome_match else "141.0.0.0"
        return (
            f"Mozilla/5.0 (Linux; Android 10; K) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{chrome_ver} Mobile Safari/537.36"
        )

    def launch_challenge_browser(
        username: str,
        cookie_value: str,
        challenge_url: str,
        proxy_info=None,
        pow_mode: bool = False,
        enable_legacy_progress_watch: bool = False,
    ):
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
        except Exception as exc:
            error(f"[{username}] Khong the import selenium: {exc}")
            return False, None, None, "selenium import error"

        proxy_bridge = None
        driver = None
        try:
            win_w, win_h = WINDOW_SIZE
            slot = allocate_window_slot()
            pos_x, pos_y = window_position_for_slot(slot)
            options = Options()
            options.page_load_strategy = "eager"
            if chrome_path and Path(chrome_path).is_file():
                options.binary_location = str(Path(chrome_path).resolve())
            user_agent = (
                selected_user_agent
                if pow_mode
                else build_mobile_user_agent(selected_user_agent)
            )
            options.add_argument(f"--user-agent={user_agent}")
            if pow_mode:
                options.add_argument("--headless=new")
                options.add_argument("--disable-gpu")
                options.add_argument("--window-size=800,600")
            else:
                options.add_argument(f"--window-size={win_w},{win_h}")
                options.add_argument(f"--window-position={pos_x},{pos_y}")
                options.add_argument("--force-device-scale-factor=0.65")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--no-first-run")
            options.add_argument("--no-default-browser-check")
            options.add_argument("--disable-popup-blocking")
            options.add_argument("--log-level=3")
            options.add_argument("--silent")
            options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
            options.add_experimental_option("useAutomationExtension", False)
            if not pow_mode:
                ext_paths = get_chrome_extensions()
                if ext_paths:
                    joined_ext = ",".join(ext_paths)
                    options.add_argument(f"--disable-extensions-except={joined_ext}")
                    options.add_argument(f"--load-extension={joined_ext}")

            if proxy_info:
                if proxy_info.get("user") and proxy_info.get("password"):
                    proxy_bridge = LocalProxyBridge(proxy_info)
                    local_proxy_url = proxy_bridge.start()
                    options.add_argument(f"--proxy-server={local_proxy_url}")
                else:
                    options.add_argument(
                        f"--proxy-server=http://{proxy_info['host']}:{proxy_info['port']}"
                    )

            try:
                driver = webdriver.Chrome(options=options)
            except Exception as exc:
                if not _should_try_chromedriver_fallback(exc):
                    raise
                fallback_driver = _download_fallback_chromedriver(
                    chrome_path=chrome_path,
                    chromedriver_download_url=chromedriver_download_url,
                )
                if not fallback_driver:
                    raise
                service = Service(executable_path=fallback_driver)
                driver = webdriver.Chrome(service=service, options=options)

            if pow_mode:
                driver.set_page_load_timeout(20)
            else:
                driver.set_page_load_timeout(max(25, min(60, browser_timeout_seconds)))

            try:
                if pow_mode:
                    driver.execute_cdp_cmd(
                        "Emulation.setDeviceMetricsOverride",
                        {
                            "width": 1280,
                            "height": 720,
                            "deviceScaleFactor": 1,
                            "mobile": False,
                        },
                    )
                    driver.execute_cdp_cmd(
                        "Emulation.setTouchEmulationEnabled",
                        {"enabled": False},
                    )
                else:
                    driver.set_window_rect(x=pos_x, y=pos_y, width=win_w, height=win_h)
                    driver.execute_cdp_cmd(
                        "Emulation.setDeviceMetricsOverride",
                        {
                            "width": 390,
                            "height": 844,
                            "deviceScaleFactor": 2,
                            "mobile": True,
                        },
                    )
                    driver.execute_cdp_cmd(
                        "Emulation.setTouchEmulationEnabled",
                        {"enabled": True, "maxTouchPoints": 5},
                    )
            except Exception:
                pass

            def _set_roblosecurity_cookie_via_cdp():
                cookies = [
                    {
                        "name": ".ROBLOSECURITY",
                        "value": cookie_value,
                        "domain": ".roblox.com",
                        "path": "/",
                        "secure": True,
                        "httpOnly": True,
                    },
                    {
                        "name": ".ROBLOSECURITY",
                        "value": cookie_value,
                        "url": "https://www.roblox.com/",
                        "path": "/",
                        "secure": True,
                        "httpOnly": True,
                    },
                ]
                try:
                    driver.execute_cdp_cmd("Network.enable", {})
                except Exception:
                    pass
                for item in cookies:
                    try:
                        driver.execute_cdp_cmd("Network.setCookie", item)
                    except Exception:
                        continue

            def _page_ready_enough(expect_host: str = "") -> bool:
                try:
                    cur_url = str(driver.current_url or "")
                    ready_state = str(
                        driver.execute_script("return document.readyState || '';") or ""
                    ).lower()
                except Exception:
                    return False
                if expect_host and expect_host.lower() not in cur_url.lower():
                    return False
                return ready_state in ("interactive", "complete")

            def _stop_pending_load():
                try:
                    driver.execute_cdp_cmd("Page.stopLoading", {})
                    return
                except Exception:
                    pass
                try:
                    driver.execute_script("window.stop();")
                except Exception:
                    pass

            def safe_get(url: str, retries: int = 2, expect_host: str = "") -> None:
                last_exc = None
                for attempt in range(retries):
                    try:
                        driver.get(url)
                        return
                    except Exception as exc:
                        last_exc = exc
                        if _page_ready_enough(expect_host=expect_host):
                            _stop_pending_load()
                            return
                        if attempt < retries - 1:
                            if _should_log_safe_get_retry_warning(exc):
                                warn(
                                    f"[{username}] Load trang cham/loi ({attempt + 1}/{retries}), thu lai..."
                                )
                            _stop_pending_load()
                            time.sleep(1.5)
                        else:
                            raise last_exc

            if not pow_mode:
                configure_extension_api_key(driver)

            _set_roblosecurity_cookie_via_cdp()
            parsed_host = (urlparse(challenge_url).hostname or "www.roblox.com").strip()
            safe_get(challenge_url, expect_host=parsed_host)

            try:
                driver.add_cookie(
                    {
                        "name": ".ROBLOSECURITY",
                        "value": cookie_value,
                        "domain": ".roblox.com",
                        "path": "/",
                        "secure": True,
                    }
                )
            except Exception:
                pass

            if not pow_mode:
                try:
                    driver.execute_script("document.body.style.zoom='50%';")
                except Exception:
                    pass

                def click_trigger_with_watch(timeout_sec: int = 20) -> bool:
                    deadline = time.time() + timeout_sec

                    def try_click_in_current_context() -> bool:
                        try:
                            button = WebDriverWait(driver, 1.2).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, "button.sc-nkuzb1-0"))
                            )
                            driver.execute_script(
                                "arguments[0].scrollIntoView({block: 'center'});", button
                            )
                            try:
                                button.click()
                            except Exception:
                                driver.execute_script("arguments[0].click();", button)
                            return True
                        except Exception:
                            pass
                        try:
                            buttons = driver.find_elements(
                                By.XPATH,
                                "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'start puzzle') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'verify') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'bat dau') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'xac minh')]",
                            )
                            for btn in buttons:
                                if not btn.is_displayed() or not btn.is_enabled():
                                    continue
                                driver.execute_script(
                                    "arguments[0].scrollIntoView({block: 'center'});", btn
                                )
                                try:
                                    btn.click()
                                except Exception:
                                    driver.execute_script("arguments[0].click();", btn)
                                return True
                        except Exception:
                            pass
                        return False

                    def scan_frames(depth: int = 0) -> bool:
                        if depth > 4:
                            return False
                        if try_click_in_current_context():
                            return True
                        try:
                            frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
                        except Exception:
                            frames = []
                        for frame in frames:
                            try:
                                driver.switch_to.frame(frame)
                                if scan_frames(depth + 1):
                                    return True
                            except Exception:
                                pass
                            finally:
                                try:
                                    driver.switch_to.parent_frame()
                                except Exception:
                                    try:
                                        driver.switch_to.default_content()
                                    except Exception:
                                        pass
                        return False

                    while time.time() < deadline:
                        try:
                            driver.switch_to.default_content()
                            if scan_frames(0):
                                driver.switch_to.default_content()
                                return True
                        except Exception:
                            pass
                        time.sleep(0.5)
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
                    return False

                click_trigger_with_watch(timeout_sec=min(20, max(5, browser_timeout_seconds)))

            solved_event = threading.Event()
            timeout_event = threading.Event()
            deadline = time.time() + browser_timeout_seconds

            def mark_solved(reason: str):
                if solved_event.is_set():
                    return
                solved_event.set()
                success(f"[{username}] Challenge solved ({reason}). Dang dong Chrome...")
                _close_driver_quietly(driver)

            def watch_timeout():
                remain = max(0.0, deadline - time.time())
                if solved_event.wait(timeout=remain):
                    return
                timeout_event.set()
                warn(
                    f"[{username}] Qua {browser_timeout_seconds}s chua giai xong challenge. Dang dong Chrome..."
                )
                _close_driver_quietly(driver)

            def watch_pow_progress():
                selector = 'span.progress-bar[role="progressbar"]'

                def check_progress_in_current_context():
                    try:
                        bars = driver.find_elements(By.CSS_SELECTOR, selector)
                    except Exception:
                        return None
                    for bar in bars:
                        raw_now = (bar.get_attribute("aria-valuenow") or "").strip()
                        try:
                            now_val = float(raw_now)
                        except ValueError:
                            now_val = None
                        style = (bar.get_attribute("style") or "").replace(" ", "").lower()
                        text = (bar.text or "").strip()
                        if (now_val is not None and now_val >= 100) or "width:100%" in style or text == "100%":
                            return raw_now or text or "100"
                    return None

                def scan_progress_frames(depth: int = 0):
                    if depth > 4:
                        return None
                    matched = check_progress_in_current_context()
                    if matched is not None:
                        return matched
                    try:
                        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
                    except Exception:
                        frames = []
                    for frame in frames:
                        try:
                            driver.switch_to.frame(frame)
                            matched = scan_progress_frames(depth + 1)
                            if matched is not None:
                                return matched
                        except Exception:
                            pass
                        finally:
                            try:
                                driver.switch_to.parent_frame()
                            except Exception:
                                try:
                                    driver.switch_to.default_content()
                                except Exception:
                                    pass
                    return None

                while not solved_event.is_set() and not timeout_event.is_set() and time.time() < deadline:
                    try:
                        driver.switch_to.default_content()
                        matched_progress = scan_progress_frames(0)
                        driver.switch_to.default_content()
                        if matched_progress is not None:
                            mark_solved(f"PoW progress={matched_progress}%")
                            return
                    except Exception:
                        if timeout_event.is_set() or solved_event.is_set():
                            return
                        return
                    time.sleep(0.35)

            def watch_verification_success():
                target_phrases = [
                    "verification successful",
                    "xac minh thanh cong",
                    "xác minh thành công",
                    "xac thuc thanh cong",
                    "xác thực thành công",
                ]

                def _normalize(text: str) -> str:
                    text = (text or "").strip().lower()
                    text = unicodedata.normalize("NFD", text)
                    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")

                normalized_phrases = [_normalize(x) for x in target_phrases]

                def check_success(depth: int = 0) -> bool:
                    if depth > 5:
                        return False
                    try:
                        body_text = driver.execute_script(
                            "return (document.body && document.body.innerText) ? document.body.innerText : '';"
                        )
                        norm_body = _normalize(body_text)
                        if any(p in norm_body for p in normalized_phrases):
                            return True
                    except Exception:
                        pass

                    try:
                        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
                    except Exception:
                        frames = []

                    for frame in frames:
                        try:
                            driver.switch_to.frame(frame)
                            if check_success(depth + 1):
                                return True
                        except Exception:
                            pass
                        finally:
                            try:
                                driver.switch_to.parent_frame()
                            except Exception:
                                try:
                                    driver.switch_to.default_content()
                                except Exception:
                                    pass
                    return False

                while not solved_event.is_set() and not timeout_event.is_set() and time.time() < deadline:
                    try:
                        driver.switch_to.default_content()
                        if check_success(0):
                            driver.switch_to.default_content()
                            mark_solved("Verification Successful")
                            return
                    except Exception:
                        if timeout_event.is_set() or solved_event.is_set():
                            return
                        return
                    time.sleep(0.5)

            threading.Thread(target=watch_timeout, daemon=True).start()
            if pow_mode:
                watch_pow_progress()
            else:
                if enable_legacy_progress_watch:
                    threading.Thread(target=watch_pow_progress, daemon=True).start()
                watch_verification_success()

            if solved_event.is_set():
                return True, driver, proxy_bridge, "solved"
            if timeout_event.is_set():
                return False, driver, proxy_bridge, f"timeout {browser_timeout_seconds}s"
            return False, driver, proxy_bridge, "browser closed before solve"
        except Exception as exc:
            _close_driver_quietly(driver)
            if proxy_bridge:
                proxy_bridge.close()
            reason = _summarize_browser_exception(exc)
            if not _should_suppress_console_failure_reason(reason):
                error(f"[{username}] Loi khi mo trinh duyet challenge ({reason})")
            return False, None, None, reason

    def parse_challenge_headers(resp):
        h = resp.headers
        challenge_id = h.get("rblx-challenge-id", "")
        challenge_type = h.get("rblx-challenge-type", "")
        metadata_b64 = h.get("rblx-challenge-metadata", "")

        decoded_text = ""
        decoded_json = None
        if metadata_b64:
            try:
                padding = "=" * (-len(metadata_b64) % 4)
                raw = base64.b64decode(metadata_b64 + padding)
                decoded_text = raw.decode("utf-8", errors="replace")
                decoded_json = json.loads(decoded_text)
            except Exception:
                pass

        return {
            "id": challenge_id,
            "type": challenge_type,
            "metadata_b64": metadata_b64,
            "metadata_text": decoded_text,
            "metadata_json": decoded_json,
        }

    def is_http_429(resp) -> bool:
        return resp is not None and resp.status_code == 429

    def is_account_moderated_response(resp) -> bool:
        if resp is None or resp.status_code != 403:
            return False
        try:
            body = resp.json()
        except ValueError:
            return False
        if not isinstance(body, dict):
            return False
        errors = body.get("errors")
        if not isinstance(errors, list):
            return False
        for item in errors:
            if not isinstance(item, dict):
                continue
            msg = str(item.get("message", "")).strip().lower()
            if "user is moderated" in msg:
                return True
        return False

    def request_csrf_token(session: requests.Session, headers: dict, proxies=None):
        resp = session.request(API_METHOD, API_URL, headers=headers, proxies=proxies, timeout=TIMEOUT)
        return resp.headers.get("x-csrf-token", ""), resp

    def request_avatar_check(session: requests.Session, headers: dict, proxies=None):
        req_headers = dict(headers)
        req_headers.pop("Content-Type", None)
        req_headers["Accept"] = "application/json"
        return session.get(AVATAR_API_URL, headers=req_headers, proxies=proxies, timeout=TIMEOUT)

    def send_target_request(session: requests.Session, headers: dict, csrf_token: str, proxies=None):
        req_headers = dict(headers)
        if csrf_token:
            req_headers["x-csrf-token"] = csrf_token
        body = {
            "placeId": 9391468976,
            "isTeleport": False,
            "gameJoinAttemptId": "962272f4-8e62-4e78-8437-3fc0a4b5d1c7",
        }
        return session.request(
            API_METHOD, API_URL, headers=req_headers, json=body, proxies=proxies, timeout=TIMEOUT
        )

    def _join_response_allows_entry(resp) -> bool:
        if resp is None or resp.status_code != 200:
            return False
        if not strict_join_verification:
            return True
        ch = parse_challenge_headers(resp)
        if ch["id"] or ch["type"] or ch["metadata_b64"]:
            return False
        try:
            body = resp.json()
        except ValueError:
            return False
        if not isinstance(body, dict):
            return False
        if body.get("errors"):
            return False
        if body.get("jobId") or body.get("joinScriptUrl") or body.get("joinScript"):
            return True
        status_value = body.get("status")
        if isinstance(status_value, int) and status_value >= 2:
            return True
        return False

    def verify_account_resolved(session: requests.Session, base_headers: dict, proxies=None):
        token, first_resp = request_csrf_token(session, base_headers, proxies=proxies)
        if is_http_429(first_resp):
            return False, first_resp, token
        resp = send_target_request(session, base_headers, token, proxies=proxies)
        if is_http_429(resp):
            return False, resp, token
        if resp.status_code == 403 and "x-csrf-token" in resp.headers:
            token = resp.headers["x-csrf-token"]
            resp = send_target_request(session, base_headers, token, proxies=proxies)
            if is_http_429(resp):
                return False, resp, token
        ch = parse_challenge_headers(resp)
        has_challenge = bool(ch["id"] or ch["type"] or ch["metadata_b64"])
        resolved = (not has_challenge) and _join_response_allows_entry(resp)
        return resolved, resp, token

    proxy_pool = load_proxies(PROXY_FILE)
    base_headers = {
        "Cookie": f".ROBLOSECURITY={cookie_value}",
        "User-Agent": selected_user_agent,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://www.roblox.com",
        "Referer": "https://www.roblox.com/",
    }

    with requests.Session() as session:
        chosen_proxy = None
        req_proxies = None
        csrf_token = ""
        first_resp = None
        resp = None

        max_attempts = MAX_PROXY_RETRIES if proxy_pool else 1
        for attempt in range(1, max_attempts + 1):
            chosen_proxy = random.choice(proxy_pool) if proxy_pool else None
            req_proxies = build_requests_proxies(chosen_proxy)
            proxy_label = format_proxy_label(chosen_proxy)

            try:
                avatar_resp = request_avatar_check(session, base_headers, proxies=req_proxies)
                if is_http_429(avatar_resp):
                    if proxy_pool and attempt < max_attempts:
                        warn(
                            f"[{username}] Proxy {proxy_label} bi 429 Too Many Requests o avatar ({attempt}/{max_attempts}), doi proxy..."
                        )
                        continue
                    error(f"[{username}] Bi 429 Too Many Requests o avatar check.")
                    return False

                if is_account_moderated_response(avatar_resp):
                    warn(
                        f"[{username}] Phat hien 'User is moderated' o API avatar -> Tai khoan bi ban, bo qua."
                    )
                    return None

                csrf_token, first_resp = request_csrf_token(
                    session, base_headers, proxies=req_proxies
                )
                if is_http_429(first_resp):
                    if proxy_pool and attempt < max_attempts:
                        warn(
                            f"[{username}] Proxy {proxy_label} bi 429 Too Many Requests ({attempt}/{max_attempts}), doi proxy..."
                        )
                        continue
                    error(f"[{username}] Bi 429 Too Many Requests khi lay x-csrf-token.")
                    return False

                resp = send_target_request(
                    session, base_headers, csrf_token, proxies=req_proxies
                )
                if is_http_429(resp):
                    if proxy_pool and attempt < max_attempts:
                        warn(
                            f"[{username}] Proxy {proxy_label} bi 429 Too Many Requests ({attempt}/{max_attempts}), doi proxy..."
                        )
                        continue
                    error(f"[{username}] Bi 429 Too Many Requests khi goi request chinh.")
                    return False

                if resp.status_code == 403 and "x-csrf-token" in resp.headers:
                    csrf_token = resp.headers["x-csrf-token"]
                    resp = send_target_request(
                        session, base_headers, csrf_token, proxies=req_proxies
                    )
                    if is_http_429(resp):
                        if proxy_pool and attempt < max_attempts:
                            warn(
                                f"[{username}] Proxy {proxy_label} bi 429 Too Many Requests ({attempt}/{max_attempts}), doi proxy..."
                            )
                            continue
                        error(f"[{username}] Bi 429 Too Many Requests sau khi cap nhat x-csrf-token.")
                        return False
                break
            except requests.RequestException as exc:
                if proxy_pool and attempt < max_attempts:
                    warn(
                        f"[{username}] Proxy {proxy_label} loi ({attempt}/{max_attempts}): {exc}"
                    )
                    continue
                error(f"[{username}] Request loi: {exc}")
                return False

        if resp is None:
            error(f"[{username}] Khong the thuc hien request.")
            return False

        ch = parse_challenge_headers(resp)
        has_challenge = bool(ch["id"] or ch["type"] or ch["metadata_b64"])

        if has_challenge:
            max_challenge_rounds = 6
            round_idx = 0
            while has_challenge and round_idx < max_challenge_rounds:
                round_idx += 1
                ch_type = _normalize_challenge_type_key(ch["type"])
                ch_label = _get_challenge_display_name(ch["type"])
                if _should_skip_challenge_type(ch["type"], captcha_only=captcha_only):
                    infos(f"[{username}] Phat hien {ch_label} -> bo qua do dang bat Captcha Only.")
                    return False

                if ch_type in ("proofofwork", "proofofspace"):
                    _notify_stage("Solving PoW")
                    purple(f"[{username}] Phat hien {ch_label} (round {round_idx})")
                elif ch_type == "captcha":
                    if pow_only:
                        infos(f"[{username}] Phat hien Captcha -> bo qua do dang bat PoW Only.")
                        return False
                    _notify_stage("Solving Captcha")
                    infos(f"[{username}] Phat hien Captcha (round {round_idx})")
                else:
                    if pow_only:
                        infos(
                            f"[{username}] Phat hien challenge={ch['type']} -> bo qua do dang bat PoW Only."
                        )
                        return False
                    _notify_stage("Solving Captcha")
                    infos(
                        f"[{username}] Phat hien challenge={ch['type'] or 'unknown'} (round {round_idx})"
                    )

                if ch_type == "proofofwork":
                    session_id = ""
                    if isinstance(ch["metadata_json"], dict):
                        session_id = str(ch["metadata_json"].get("sessionId", "")).strip()
                    if not session_id:
                        error(f"[{username}] Khong lay duoc sessionId de giai PoW API.")
                        return False
                    pow_ok, pow_info = pow.solve_pow_via_api(
                        session=session,
                        base_headers=base_headers,
                        csrf_token=csrf_token,
                        challenge_id=ch["id"],
                        session_id=session_id,
                        proxies=req_proxies,
                        timeout=TIMEOUT,
                    )
                    if not pow_ok:
                        reason_text = str((pow_info or {}).get("reason", "unknown"))
                        t_val = (pow_info or {}).get("t")
                        if (
                            isinstance(t_val, int)
                            and t_val > 40000001
                            and "T qua lon" in reason_text
                        ):
                            warn(
                                f"[{username}] PoW API co T={t_val} (>40000001), lam moi challenge de lay T nho hon..."
                            )
                            try:
                                resolved, latest_resp, csrf_token = verify_account_resolved(
                                    session, base_headers, proxies=req_proxies
                                )
                            except requests.RequestException as exc:
                                error(f"[{username}] Loi khi lam moi PoW challenge: {exc}")
                                return False
                            if is_http_429(latest_resp):
                                if proxy_pool:
                                    for retry_429 in range(1, MAX_PROXY_RETRIES + 1):
                                        chosen_proxy = random.choice(proxy_pool)
                                        req_proxies = build_requests_proxies(chosen_proxy)
                                        proxy_label = format_proxy_label(chosen_proxy)
                                        warn(
                                            f"[{username}] Bi 429 khi lam moi PoW, doi proxy {proxy_label} ({retry_429}/{MAX_PROXY_RETRIES})..."
                                        )
                                        try:
                                            resolved, latest_resp, csrf_token = (
                                                verify_account_resolved(
                                                    session, base_headers, proxies=req_proxies
                                                )
                                            )
                                        except requests.RequestException:
                                            continue
                                        if not is_http_429(latest_resp):
                                            break
                                if is_http_429(latest_resp):
                                    error(
                                        f"[{username}] Bi 429 Too Many Requests khi lam moi PoW challenge."
                                    )
                                    return False
                            if resolved:
                                success(f"[{username}] Verify OK: Khong con challenge.")
                                return True
                            ch = parse_challenge_headers(latest_resp)
                            has_challenge = bool(ch["id"] or ch["type"] or ch["metadata_b64"])
                            if not has_challenge:
                                warn(
                                    f"[{username}] Khong thay challenge header sau khi lam moi PoW (HTTP {latest_resp.status_code})."
                                )
                                return False
                            continue
                        error(f"[{username}] Giai PoW API that bai ({reason_text})")
                        return False
                    csrf_token = str((pow_info or {}).get("csrf_token", csrf_token) or csrf_token)
                    purple(
                        f"[{username}] Giai PoW API thanh cong (t={(pow_info or {}).get('t')}, a={(pow_info or {}).get('a')}, {((pow_info or {}).get('elapsed') or 0.0):.2f}s)"
                    )
                else:
                    use_pow_browser_mode = _should_use_pow_mode_for_browser(ch["type"])
                    enable_legacy_progress_watch = _should_enable_legacy_progress_watch(ch["type"])
                    if ch_type == "proofofspace":
                        infos(
                            f"[{username}] Proof Of Space dung browser flow giong Captcha de tranh loi trang trang."
                        )
                    challenge_url = build_challenge_url(
                        ch["id"], ch["type"], ch["metadata_b64"]
                    )
                    solved, driver, proxy_bridge, reason = launch_challenge_browser(
                        username,
                        cookie_value,
                        challenge_url,
                        proxy_info=chosen_proxy,
                        pow_mode=use_pow_browser_mode,
                        enable_legacy_progress_watch=enable_legacy_progress_watch,
                    )
                    _close_driver_quietly(driver)
                    if proxy_bridge:
                        proxy_bridge.close()

                    if not solved:
                        if not _should_suppress_console_failure_reason(reason):
                            error(f"[{username}] Giai challenge that bai ({reason})")
                        return False

                try:
                    resolved, latest_resp, csrf_token = verify_account_resolved(
                        session, base_headers, proxies=req_proxies
                    )
                except requests.RequestException as exc:
                    error(f"[{username}] Loi khi verify lai sau khi giai: {exc}")
                    return False

                if is_http_429(latest_resp):
                    if proxy_pool:
                        for retry_429 in range(1, MAX_PROXY_RETRIES + 1):
                            chosen_proxy = random.choice(proxy_pool)
                            req_proxies = build_requests_proxies(chosen_proxy)
                            proxy_label = format_proxy_label(chosen_proxy)
                            warn(
                                f"[{username}] Bi 429 khi verify lai, doi proxy {proxy_label} ({retry_429}/{MAX_PROXY_RETRIES})..."
                            )
                            try:
                                resolved, latest_resp, csrf_token = verify_account_resolved(
                                    session, base_headers, proxies=req_proxies
                                )
                            except requests.RequestException:
                                continue
                            if not is_http_429(latest_resp):
                                break
                    if is_http_429(latest_resp):
                        error(f"[{username}] Bi 429 Too Many Requests khi verify lai.")
                        return False

                if resolved:
                    success(f"[{username}] Verify OK: Khong con challenge.")
                    return True

                ch = parse_challenge_headers(latest_resp)
                has_challenge = bool(ch["id"] or ch["type"] or ch["metadata_b64"])
                if not has_challenge:
                    warn(
                        f"[{username}] Khong thay challenge header nhung request van bi chan (HTTP {latest_resp.status_code})."
                    )
                    return False

                next_type = (ch["type"] or "unknown").lower()
                if pow_only and next_type != "proofofwork":
                    infos(
                        f"[{username}] Sau PoW xuat hien challenge={ch['type']} -> bo qua do dang bat PoW Only."
                    )
                    return False
                if _should_skip_challenge_type(ch["type"], captcha_only=captcha_only):
                    infos(
                        f"[{username}] Sau challenge xuat hien {_get_challenge_display_name(ch['type'])} -> bo qua do dang bat Captcha Only."
                    )
                    return False
                warn(
                    f"[{username}] Sau khi giai xong van con challenge tiep theo: type={ch['type'] or 'unknown'} (round {round_idx + 1})"
                )

            error(f"[{username}] Vuot qua so vong giai challenge ({max_challenge_rounds}).")
            return False
        else:
            if _join_response_allows_entry(resp):
                return True
            warn(
                f"[{username}] Khong co challenge nhung join-game chua hop le (HTTP {resp.status_code})."
            )
            return False
