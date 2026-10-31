import base64
from datetime import datetime
import json
import os
from pathlib import Path
import random
import re
import select
import shutil
import socket
import socketserver
import sys
from tempfile import mkdtemp
import threading
import time
import traceback
import unicodedata
from urllib.parse import quote, urlparse
from colorama import Fore, init
import requests
import pow

init(autoreset=True)

USER_AGENT_FILE = "user-agent.txt"
_user_agents = None
_user_agents_lock = threading.Lock()


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


def infos(msg: str):
    log(msg, "~")


def success(msg: str):
    log(msg, "+")


def warn(msg: str):
    log(msg, "!")


def error(msg: str):
    log(msg, "-")


def purple(msg: str):
    log(msg, "^")


def _load_user_agents() -> list[str]:
    candidates = [
        Path(sys.argv[0]).resolve().parent / USER_AGENT_FILE,
        Path.cwd() / USER_AGENT_FILE,
        Path(USER_AGENT_FILE),
    ]
    items = []
    for path in candidates:
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            items.append(line)
                if items:
                    break
            except Exception:
                pass
    return items


def choose_user_agent() -> str:
    global _user_agents
    with _user_agents_lock:
        if _user_agents is None:
            _user_agents = _load_user_agents()
        if _user_agents:
            return random.choice(_user_agents)
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    )


def solve_captcha(
    username: str,
    cookie_value: str,
    chrome_path: str = "",
    pow_only: bool = False,
    extension_path: str = "",
    stage_callback=None,
    browser_lang: str = "en",
) -> str:
    STATUS_NO_CAPTCHA = "no_captcha"
    STATUS_SOLVED = "solved"
    STATUS_FAILED = "failed"
    METHOD = "POST"
    URL = "https://gamejoin.roblox.com/v1/join-game"
    AVATAR_URL = "https://avatar.roblox.com/v1/avatar"
    PROXY_PATH = "proxies.txt"
    TIMEOUT_SECONDS = 15.0
    REQUEST_RETRIES = 3
    REQUEST_RETRY_BACKOFF = 2
    CAPTCHA_RETRIES = 1
    TOTAL_RETRIES = 2
    WINDOW_SIZE = (380, 280)
    WINDOW_SIZE_JITTER = (16, 12)
    WINDOW_GAP = -30
    SOLVE_TIMEOUT_SECONDS = 300
    BASE_DIR = Path(sys.argv[0]).resolve().parent

    def normalize_browser_lang(value: str) -> str:
        text = (value or "").strip()
        return text if text else "en"

    def _notify_stage(stage: str):
        if callable(stage_callback):
            try:
                stage_callback(stage)
            except Exception:
                pass

    CHALLENGE_BASE_URL = "https://www.roblox.com/challenge/cdn/hybrid"
    HEADERS = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    def build_request_headers() -> dict:
        headers = {}
        for key, value in HEADERS.items():
            headers[key] = str(value).encode("latin-1", "ignore").decode("latin-1")
        user_agent = choose_user_agent()
        if user_agent:
            headers["User-Agent"] = user_agent
        return headers

    JSON_PAYLOAD = {
        "gameId": "00000000-0000-0000-0000-000000000000",
        "gameJoinAttemptId": "00000000-0000-0000-0000-000000000000",
        "placeId": 0,
        "gamerTag": "",
        "isPartyLeader": True,
        "isPlayTogetherGame": True,
        "browserTrackerId": 0,
        "isTeleport": True,
    }

    if chrome_path and not Path(chrome_path).is_file():
        warn(f"Chrome not found: {chrome_path}")

    def normalize_proxy_value(raw: str) -> str:
        value = (raw or "").strip()
        if "://" in value:
            value = value.split("://", 1)[1].strip()
        return value

    def is_valid_proxy_value(value: str) -> bool:
        if not value:
            return False
        candidate = value if "://" in value else f"http://{value}"
        try:
            parsed = urlparse(candidate)
            host = parsed.hostname
            port = parsed.port
            return bool(host and port and 1 <= port <= 65535)
        except Exception:
            return False

    def parse_proxy_value(value: str) -> dict | None:
        normalized = normalize_proxy_value(value)
        if not is_valid_proxy_value(normalized):
            return None
        candidate = normalized if "://" in normalized else f"http://{normalized}"
        parsed = urlparse(candidate)
        hostport = f"{parsed.hostname}:{parsed.port}"
        user = parsed.username or ""
        pw = parsed.password or ""
        return {"hostport": hostport, "username": user, "password": pw}

    class _ProxyAuthForwarder:
        def __init__(self, upstream_hostport: str, username: str, password: str):
            self._upstream_hostport = upstream_hostport
            raw_auth = f"{username}:{password}".encode("utf-8", "ignore")
            self._auth_header = "Basic " + base64.b64encode(raw_auth).decode("ascii", "ignore")
            if ":" not in upstream_hostport:
                raise ValueError("upstream_hostport must be host:port")
            host_part, port_part = upstream_hostport.rsplit(":", 1)
            self._upstream_host = host_part
            self._upstream_port = int(port_part)
            parent = self

            class Handler(socketserver.BaseRequestHandler):
                @staticmethod
                def _recv_until(sock: socket.socket, marker: bytes, limit: int = 65536) -> bytes:
                    data = b""
                    while marker not in data and len(data) < limit:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    return data

                @staticmethod
                def _connect_upstream() -> socket.socket:
                    s = socket.create_connection(
                        (parent._upstream_host, parent._upstream_port),
                        timeout=15,
                    )
                    s.settimeout(15)
                    return s

                @staticmethod
                def _relay(a: socket.socket, b: socket.socket):
                    a.settimeout(60)
                    b.settimeout(60)
                    sockets = [a, b]
                    while True:
                        r, _, e = select.select(sockets, [], sockets, 60)
                        if e or not r:
                            break
                        for s in r:
                            other = b if s is a else a
                            data = s.recv(8192)
                            if not data:
                                return
                            other.sendall(data)

                def handle(self):
                    try:
                        header_block = self._recv_until(self.request, b"\r\n\r\n")
                        if not header_block:
                            return
                        header_text = header_block.decode("iso-8859-1", errors="replace")
                        lines = header_text.split("\r\n")
                        first = lines[0].split(" ", 2)
                        if len(first) < 2:
                            return
                        method = first[0].upper()
                        target = first[1]
                        version = first[2] if len(first) > 2 else "HTTP/1.1"
                        if method == "CONNECT":
                            upstream = self._connect_upstream()
                            connect_req = (
                                f"CONNECT {target} {version}\r\n"
                                f"Host: {target}\r\n"
                                f"Proxy-Authorization: {parent._auth_header}\r\n"
                                "Proxy-Connection: keep-alive\r\n"
                                "Connection: keep-alive\r\n\r\n"
                            )
                            upstream.sendall(connect_req.encode("iso-8859-1", "ignore"))
                            resp = self._recv_until(upstream, b"\r\n\r\n")
                            if b" 200 " not in resp and b" 201 " not in resp:
                                self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                                upstream.close()
                                return
                            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                            self._relay(self.request, upstream)
                            upstream.close()
                        else:
                            out_lines = [lines[0]]
                            saw_auth = False
                            for line in lines[1:]:
                                if not line:
                                    continue
                                key = line.split(":", 1)[0].strip().lower()
                                if key == "proxy-authorization":
                                    out_lines.append(f"Proxy-Authorization: {parent._auth_header}")
                                    saw_auth = True
                                elif key not in {"connection", "proxy-connection"}:
                                    out_lines.append(line)
                            if not saw_auth:
                                out_lines.append(f"Proxy-Authorization: {parent._auth_header}")
                            out_lines.append("Proxy-Connection: close")
                            out_lines.append("Connection: close")
                            out = ("\r\n".join(out_lines) + "\r\n\r\n").encode("iso-8859-1", "ignore")
                            upstream = self._connect_upstream()
                            upstream.sendall(out)
                            extra = header_block.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in header_block else b""
                            if extra:
                                upstream.sendall(extra)
                            while True:
                                data = upstream.recv(8192)
                                if not data:
                                    break
                                self.request.sendall(data)
                            upstream.close()
                    except Exception:
                        pass

            self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
            self._server.daemon_threads = True
            self.host = "127.0.0.1"
            self.port = self._server.server_address[1]
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()

        def close(self):
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass

    def verify_join_game_clear(cookie_value: str, proxy_value: str = "") -> tuple[bool, str]:
        cookies = {".ROBLOSECURITY": cookie_value}
        proxies = {"http": f"http://{proxy_value}", "https": f"http://{proxy_value}"} if proxy_value else None
        response = None
        for attempt in range(REQUEST_RETRIES):
            try:
                response = requests.request(
                    METHOD,
                    URL,
                    headers=build_request_headers(),
                    cookies=cookies,
                    json=JSON_PAYLOAD,
                    proxies=proxies,
                    timeout=TIMEOUT_SECONDS,
                )
                break
            except requests.RequestException:
                if attempt + 1 < REQUEST_RETRIES:
                    time.sleep(REQUEST_RETRY_BACKOFF)
                else:
                    return False, "request_error"
        if response is None:
            return False, "no_response"
        if response.status_code == 200:
            return True, ""
        challenge_id = response.headers.get("rblx-challenge-id")
        challenge_metadata = response.headers.get("rblx-challenge-metadata")
        challenge_type = (response.headers.get("rblx-challenge-type", "") or "").strip().lower()
        if (
            response.status_code == 403
            and "Challenge is required to authorize the request" in response.text
            and challenge_id
            and challenge_metadata
        ):
            normalized = challenge_type.replace("-", "").replace("_", "")
            return False, normalized or "challenge"
        return False, f"status_{response.status_code}"

    def is_user_moderated(cookie_value: str, proxies: dict = None) -> bool:
        cookies = {".ROBLOSECURITY": cookie_value}
        headers = {"Accept": "application/json"}
        for used_proxy in (proxies, None):
            for attempt in range(REQUEST_RETRIES):
                try:
                    response = requests.request(
                        "GET",
                        AVATAR_URL,
                        headers=headers,
                        cookies=cookies,
                        proxies=used_proxy,
                        timeout=TIMEOUT_SECONDS,
                    )
                    if response.status_code == 403:
                        data = response.json()
                        for err in data.get("errors") or []:
                            if "User is moderated" in str(err.get("message", "")):
                                return True
                    break
                except Exception:
                    if attempt + 1 < REQUEST_RETRIES:
                        time.sleep(REQUEST_RETRY_BACKOFF)
        return False

    def get_log_path() -> Path:
        base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        return base / "log.txt"

    def log_error(message: str):
        try:
            with open(get_log_path(), "a", encoding="utf-8") as handle:
                handle.write(message + "\n")
        except Exception:
            pass

    def format_response_debug(response, max_len: int = 800) -> str:
        if response is None:
            return "response=None"
        status = getattr(response, "status_code", "unknown")
        reason = getattr(response, "reason", "")
        text = (getattr(response, "text", "") or "").strip()
        if len(text) > max_len:
            text = text[:max_len] + "..."
        return f"status={status} reason={reason} body={text}"

    def build_challenge_url(challenge_id: str, challenge_metadata: str, challenge_type: str = "captcha") -> str:
        raw_type = (challenge_type or "").strip().lower()
        if raw_type in ("proofofwork", "proof-of-work", "proof_of_work", "pow"):
            norm_type = "proofofwork"
        elif raw_type in ("proofofspace", "proof-of-space", "proof_of_space", "pos"):
            norm_type = "proofofspace"
        else:
            norm_type = "captcha"
        return (
            f"{CHALLENGE_BASE_URL}?generic-challenge-type={quote(norm_type, safe='')}"
            f"&app-type=windows&generic-challenge-id={quote(challenge_id, safe='')}"
            f"&challenge-metadata-json={quote(challenge_metadata, safe='')}"
            "&challenge-type=generic&dark-mode=false"
        )

    def decode_challenge_metadata(metadata_b64: str) -> dict | None:
        if not metadata_b64:
            return None
        padded = metadata_b64 + "=" * ((4 - len(metadata_b64) % 4) % 4)
        for decoder in (base64.urlsafe_b64decode, base64.b64decode):
            try:
                raw = decoder(padded)
                text = raw.decode("utf-8", errors="replace")
                start = text.find("{")
                end = text.rfind("}")
                if start >= 0 and end > start:
                    return json.loads(text[start : end + 1])
            except Exception:
                continue
        return None

    def run_pow_solver(session_id: str, challenge_id: str, cookie_value: str, proxy_value: str = "") -> tuple[bool, str | None]:
        if not session_id or not challenge_id:
            return False, "missing session_id or challenge_id"
        try:
            result = pow.main(
                cookie=cookie_value,
                session_id=session_id,
                challenge_id=challenge_id,
                debug=True,
                proxy_value=proxy_value,
            )
            if isinstance(result, tuple):
                ok, info = result
                if ok:
                    return True, None
                if isinstance(info, dict):
                    parts = [f"step={info.get('step', 'unknown')}"]
                    if info.get("status") is not None:
                        parts.append(f"status={info['status']}")
                    if info.get("detail"):
                        parts.append(f"detail={info['detail']}")
                    if info.get("error"):
                        parts.append(f"error={info['error']}")
                    return False, ", ".join(parts)
            elif result:
                return True, None
            return False, "unknown"
        except Exception as e:
            return False, f"exception={e}"

    def create_profile_dir() -> Path:
        base = Path.cwd().resolve() / "chrome-profile"
        base.mkdir(parents=True, exist_ok=True)
        return Path(mkdtemp(prefix="profile-", dir=str(base)))

    def get_screen_size() -> tuple[int, int]:
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            width = root.winfo_screenwidth()
            height = root.winfo_screenheight()
            root.destroy()
            return width, height
        except Exception:
            return 0, 0

    def window_position_for_slot(slot: int) -> tuple[int, int]:
        screen_w, _screen_h = get_screen_size()
        win_w, win_h = WINDOW_SIZE
        step_x = max(120, win_w + WINDOW_GAP)
        step_y = max(120, win_h + WINDOW_GAP)
        cols = max(1, screen_w // step_x) if screen_w > 0 else 4
        x = (slot % cols) * step_x
        y = (slot // cols) * step_y
        return x, y

    def randomized_window_size() -> tuple[int, int]:
        base_w, base_h = WINDOW_SIZE
        jitter_w, jitter_h = WINDOW_SIZE_JITTER
        width = max(320, base_w + random.randint(-jitter_w, jitter_w))
        height = max(240, base_h + random.randint(-jitter_h, jitter_h))
        return width, height

    def load_proxies() -> list[str]:
        if not Path(PROXY_PATH).is_file():
            return []
        try:
            lines = Path(PROXY_PATH).read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        proxies = []
        for line in lines:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            lower = raw.lower()
            if "example" in lower or "only ip:port" in lower:
                continue
            normalized = normalize_proxy_value(raw)
            if is_valid_proxy_value(normalized):
                proxies.append(normalized)
        return proxies

    def launch_challenge_browser(
        url: str,
        chrome_path: str = None,
        username: str = "",
        cookie_value: str = "",
        window_position: tuple[int, int] = None,
        raw_line: str = "",
        proxy_value: str = "",
        extension_path: str = "",
        pow_mode: bool = False,
        browser_lang: str = "en",
        reason_out: list = None,
    ) -> bool:
        failure_reason = ""

        def set_failure_reason(value: str):
            nonlocal failure_reason
            text = (value or "").strip()
            if text:
                failure_reason = text
                if isinstance(reason_out, list):
                    reason_out.clear()
                    reason_out.append(text)

        def _read_client_key(config_js_path: str) -> str:
            try:
                with open(config_js_path, "r", encoding="utf-8") as f:
                    text = f.read()
                m = re.search(r"clientKey\s*:\s*(['\"])(.*?)\1", text)
                return m.group(2).strip() if m else ""
            except Exception:
                return ""

        def _is_placeholder_key(value: str) -> bool:
            v = (value or "").strip().lower()
            return not v or v in {"your_clien_key", "your_client_key", "your_api_key"}

        def random_mobile_metrics() -> tuple[int, int, float]:
            width = 360 + random.randint(-8, 8)
            height = 740 + random.randint(-12, 12)
            device_scale = round(random.uniform(3.6, 4.2), 2)
            return width, height, device_scale

        def choose_locale_headers(lang_value: str) -> tuple[str, str]:
            raw = (lang_value or "").strip().replace("_", "-")
            lower = raw.lower()
            if lower in {"", "english", "en", "en-us"}:
                return "en-US", "en-US,en;q=0.9"
            if lower in {"vietnamese", "vi", "vi-vn"}:
                return "vi-VN", "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"
            if "-" in raw:
                p0, p1 = raw.split("-", 1)
                locale = f"{p0.lower()}-{p1.upper()}"
                primary = p0.lower()
                return locale, f"{locale},{primary};q=0.9,en-US;q=0.8,en;q=0.7"
            return "en-US", "en-US,en;q=0.9"

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.common.by import By
            from selenium.common.exceptions import TimeoutException
        except Exception:
            log_error("Selenium import failed:\n" + traceback.format_exc())
            set_failure_reason("selenium import failed")
            return False

        driver = None
        profile_dir = create_profile_dir()
        proxy_forwarder = None
        try:
            window_width, window_height = randomized_window_size()
            options = webdriver.ChromeOptions()
            user_agent = choose_user_agent()
            options.enable_webextensions = True
            options.enable_bidi = True
            options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)
            options.add_argument("--no-default-browser-check")
            options.add_argument("--no-first-run")
            options.add_argument("--disable-notifications")
            options.add_argument("--disable-popup-blocking")
            options.add_argument("--mute-audio")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--force-device-scale-factor=1.0")
            options.add_argument("--high-dpi-support=1.0")
            options.add_argument(f"--user-agent={user_agent}")
            options.add_argument(f"--user-data-dir={profile_dir}")
            locale_code, accept_language = choose_locale_headers(browser_lang)
            options.add_argument(f"--lang={locale_code}")
            options.add_argument(f"--window-size={window_width},{window_height}")
            if window_position:
                options.add_argument(f"--window-position={window_position[0]},{window_position[1]}")
            options.add_argument(f"--app={url}")
            if chrome_path:
                options.binary_location = chrome_path

            proxy_info = parse_proxy_value(proxy_value)
            if proxy_info:
                if proxy_info["username"]:
                    proxy_forwarder = _ProxyAuthForwarder(
                        upstream_hostport=proxy_info["hostport"],
                        username=proxy_info["username"],
                        password=proxy_info["password"],
                    )
                    proxy_server = f"{proxy_forwarder.host}:{proxy_forwarder.port}"
                else:
                    proxy_server = proxy_info["hostport"]
                options.add_argument(f"--proxy-server={proxy_server}")

            prefs = {
                "profile.default_content_setting_values.notifications": 2,
                "intl.accept_languages": accept_language,
            }
            options.add_experimental_option("prefs", prefs)
            options.set_capability(
                "goog:loggingPrefs",
                {"performance": "ALL", "browser": "OFF", "driver": "OFF"},
            )
            options.set_capability(
                "timeouts",
                {"implicit": 0, "pageLoad": 45000, "script": 10000},
            )
            service = Service(log_path=os.devnull)
            driver = webdriver.Chrome(service=service, options=options)
            driver.execute_cdp_cmd("Emulation.setLocaleOverride", {"locale": locale_code})
            ua_for_override = user_agent or driver.execute_script("return navigator.userAgent")
            driver.execute_cdp_cmd(
                "Network.setUserAgentOverride",
                {
                    "userAgent": ua_for_override,
                    "acceptLanguage": accept_language,
                    "platform": "Windows",
                },
            )
            driver.set_page_load_timeout(45)
            driver.set_window_size(window_width, window_height)
            if window_position:
                driver.set_window_position(window_position[0], window_position[1])

            def _set_yummeow_title():
                try:
                    driver.execute_script('try { document.title = "Yummeow"; } catch (_err) {}')
                except Exception:
                    pass

            def _safe_get(target_url: str, label: str):
                try:
                    driver.get(target_url)
                except TimeoutException:
                    warn(f"{label} load timeout, keep browser session alive.")
                    try:
                        driver.execute_script("window.stop();")
                    except Exception:
                        pass

            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": """
                        (() => {
                            try { document.title = "Yummeow"; } catch (_err) {}
                            if (!window.chrome) window.chrome = {};
                            if (!window.chrome.webview) {
                                const listeners = {};
                                window.chrome.webview = {
                                    postMessage: () => {},
                                    addEventListener: (name, fn) => {
                                        if (!listeners[name]) listeners[name] = [];
                                        listeners[name].push(fn);
                                    },
                                    removeEventListener: (name, fn) => {
                                        const arr = listeners[name] || [];
                                        const idx = arr.indexOf(fn);
                                        if (idx >= 0) arr.splice(idx, 1);
                                    },
                                };
                            }
                        })();
                    """
                },
            )

            extension_paths = []
            if extension_path:
                ext_path = extension_path if Path(extension_path).is_absolute() else os.path.join(BASE_DIR, extension_path)
                if os.path.isdir(ext_path):
                    extension_paths.append(ext_path)
            else:
                yes_dir = os.path.join(BASE_DIR, "yescaptcha")
                omo_dir = os.path.join(BASE_DIR, "omocaptcha")
                if os.path.isdir(omo_dir):
                    try:
                        with open(os.path.join(omo_dir, "configs.json"), "r", encoding="utf-8") as handle:
                            data = json.load(handle)
                        omo_key = str(data.get("api_key", "")).strip()
                        if not _is_placeholder_key(omo_key):
                            extension_paths.append(omo_dir)
                    except Exception:
                        pass
                if os.path.isdir(yes_dir):
                    yes_key = _read_client_key(os.path.join(yes_dir, "config.js"))
                    if not _is_placeholder_key(yes_key):
                        extension_paths.append(yes_dir)

            for ext_p in extension_paths:
                try:
                    driver.webextension.install(path=ext_p)
                except Exception:
                    pass

            _safe_get("https://www.roblox.com/challenge/cdn/hybrid", "Hybrid page")
            driver.add_cookie(
                {
                    "name": ".ROBLOSECURITY",
                    "value": cookie_value,
                    "domain": ".roblox.com",
                    "secure": True,
                }
            )
            time.sleep(1)
            _safe_get(url, "Challenge page")
            _set_yummeow_title()

            solved_event = threading.Event()
            timeout_event = threading.Event()
            deadline = time.time() + SOLVE_TIMEOUT_SECONDS
            next_random_click_at = time.monotonic() + random.uniform(1.8, 4.2)

            def maybe_random_click():
                if random.random() > 0.28:
                    return
                try:
                    driver.switch_to.default_content()
                    driver.execute_script(
                        """
                        const margin = 16;
                        const width = Math.max(
                            document.documentElement.clientWidth || 0,
                            window.innerWidth || 0
                        );
                        const height = Math.max(
                            document.documentElement.clientHeight || 0,
                            window.innerHeight || 0
                        );
                        if (!width || !height) return false;
                        const x = Math.floor(margin + Math.random() * Math.max(1, width - margin * 2));
                        const y = Math.floor(margin + Math.random() * Math.max(1, height - margin * 2));
                        const target = document.elementFromPoint(x, y);
                        if (!target) return false;
                        if (target.closest && target.closest("a,button,input,select,textarea,label,[role='button'],iframe")) {
                            return false;
                        }
                        const ev = new MouseEvent("click", {
                            bubbles: true,
                            cancelable: true,
                            view: window,
                            clientX: x,
                            clientY: y
                        });
                        target.dispatchEvent(ev);
                        return true;
                        """
                    )
                except Exception:
                    pass

            def mark_solved():
                if not solved_event.is_set() and not timeout_event.is_set():
                    if pow_mode:
                        purple("Proof of work completed. Waiting 5s...")
                    solved_event.set()

            def watch_continue_success():
                target_path = "/challenge/v1/continue"
                while not solved_event.is_set() and not timeout_event.is_set():
                    if time.time() >= deadline:
                        timeout_event.set()
                        break
                    try:
                        logs = driver.get_log("performance")
                        for entry in logs:
                            message = json.loads(entry.get("message", "{}")).get("message", {})
                            if message.get("method") == "Network.responseReceived":
                                response = message.get("params", {}).get("response", {})
                                url_val = response.get("url", "")
                                status = int(response.get("status", 0))
                                if target_path in url_val and 200 <= status < 300:
                                    mark_solved()
                                    return
                    except Exception:
                        pass
                    time.sleep(1)

            def watch_verification_success():
                target_phrases = [
                    "you've proven you're a human. continue your action.",
                    "you have proven you're a human. continue your action.",
                    "ban da chung minh ban la con nguoi. tiep tuc hanh dong cua ban.",
                    "ban da chung minh ban la nguoi. tiep tuc hanh dong cua ban.",
                    "xac minh hoan tat! ban da chung minh ban la con nguoi. tiep tuc hanh dong cua ban.",
                    "xac minh hoan tat",
                    "ban da chung minh ban la con",
                ]

                def _normalize(s: str) -> str:
                    norm = unicodedata.normalize("NFKD", s or "")
                    return "".join(c for c in norm if not unicodedata.combining(c)).strip().lower()

                normalized_phrases = [_normalize(p) for p in target_phrases]

                def check_success(_driver) -> bool:
                    try:
                        _driver.switch_to.default_content()
                        outer = _driver.find_element(
                            By.CSS_SELECTOR,
                            'iframe[title="Verification challenge"], iframe[aria-label="Verification challenge"], iframe[src*="arkoselabs.roblox.com"]',
                        )
                        _driver.switch_to.frame(outer)
                        inner = _driver.find_element(
                            By.CSS_SELECTOR,
                            "iframe#game-core-frame, iframe[src*='game-core']",
                        )
                        _driver.switch_to.frame(inner)
                        elements = _driver.find_elements(
                            By.CSS_SELECTOR,
                            "p[data-theme='victory.subtitle'], h2[data-theme='victory.title'], h2[aria-label]",
                        )
                        for element in elements:
                            text = _normalize(element.text)
                            aria = _normalize(element.get_attribute("aria-label") or "")
                            combined = f"{text} {aria}"
                            if any(p in combined for p in normalized_phrases):
                                return True
                    except Exception:
                        pass
                    finally:
                        try:
                            _driver.switch_to.default_content()
                        except Exception:
                            pass
                    return False

                while not solved_event.is_set() and not timeout_event.is_set():
                    if time.time() >= deadline:
                        timeout_event.set()
                        break
                    if check_success(driver):
                        mark_solved()
                        return
                    time.sleep(0.25)

            def watch_pow_progress():
                selector = "div.progress-bar.progress-bar-striped[role='progressbar'][aria-valuenow='100']"

                def check_progress(_driver) -> bool:
                    elements = _driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        text = (element.text or "").strip()
                        aria = (element.get_attribute("aria-valuenow") or "").strip()
                        if aria == "100" or "100%" in text:
                            return True
                    return False

                while not solved_event.is_set() and not timeout_event.is_set():
                    if time.time() >= deadline:
                        timeout_event.set()
                        break
                    try:
                        driver.switch_to.default_content()
                        if check_progress(driver):
                            mark_solved()
                            return
                        frames = driver.find_elements(By.CSS_SELECTOR, "iframe")
                        for frame in frames:
                            driver.switch_to.frame(frame)
                            if check_progress(driver):
                                mark_solved()
                                return
                            driver.switch_to.default_content()
                    except Exception:
                        pass
                    time.sleep(0.25)

            threading.Thread(target=watch_continue_success, daemon=True).start()
            if pow_mode:
                threading.Thread(target=watch_pow_progress, daemon=True).start()
            else:
                threading.Thread(target=watch_verification_success, daemon=True).start()

            while not solved_event.is_set() and not timeout_event.is_set():
                if time.time() >= deadline:
                    timeout_event.set()
                    set_failure_reason("solve timeout")
                    break
                try:
                    if not driver.window_handles:
                        set_failure_reason("browser window closed")
                        break
                except Exception:
                    set_failure_reason("browser handle check failed")
                    break
                now_mono = time.monotonic()
                if now_mono >= next_random_click_at:
                    maybe_random_click()
                    next_random_click_at = now_mono + random.uniform(3.6, 8.4)
                time.sleep(1)

            if solved_event.is_set():
                time.sleep(5)
                ok, reason = verify_join_game_clear(cookie_value=cookie_value, proxy_value=proxy_value)
                if ok:
                    purple("Captcha solved.")
                    return True
                set_failure_reason(reason or "join-game still blocked")
                return False
            return False
        except Exception as exc:
            log_error("Launch attempt failed:\n" + traceback.format_exc())
            set_failure_reason(f"browser exception={type(exc).__name__}: {exc}")
            return False
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            if proxy_forwarder is not None:
                proxy_forwarder.close()
            shutil.rmtree(profile_dir, ignore_errors=True)

    browser_lang = normalize_browser_lang(browser_lang)
    raw_line = f"{username}:{cookie_value}"
    cookies = {".ROBLOSECURITY": cookie_value}
    proxy_pool = load_proxies()
    proxy_value = random.choice(proxy_pool) if proxy_pool else ""
    proxies = {"http": f"http://{proxy_value}", "https": f"http://{proxy_value}"} if proxy_value else None

    for total_attempt in range(1, TOTAL_RETRIES + 1):
        response = None
        for used_proxy in (proxies, None):
            for attempt in range(REQUEST_RETRIES):
                try:
                    response = requests.request(
                        METHOD,
                        URL,
                        headers=build_request_headers(),
                        cookies=cookies,
                        json=JSON_PAYLOAD,
                        proxies=used_proxy,
                        timeout=TIMEOUT_SECONDS,
                    )
                    break
                except requests.RequestException as exc:
                    if attempt + 1 < REQUEST_RETRIES:
                        time.sleep(REQUEST_RETRY_BACKOFF)
                    elif used_proxy is None:
                        error(f"{username}: Fail: request timeout/error ({exc})")
                        return STATUS_FAILED
            if response is not None:
                break

        if response is None:
            return STATUS_FAILED
        if response.status_code == 200:
            return STATUS_NO_CAPTCHA

        challenge_id = response.headers.get("rblx-challenge-id")
        challenge_metadata = response.headers.get("rblx-challenge-metadata")
        challenge_type = (response.headers.get("rblx-challenge-type", "") or "").strip()
        if (
            response.status_code == 403
            and "Challenge is required to authorize the request" in response.text
            and challenge_id
            and challenge_metadata
        ):
            normalized = (challenge_type or "captcha").lower().replace("-", "").replace("_", "")
            challenge_url = build_challenge_url(challenge_id, challenge_metadata, challenge_type=normalized)
            if normalized in ("proofofwork", "proofofspace"):
                _notify_stage("pos_started" if normalized == "proofofspace" else "pow_started")
                metadata_obj = decode_challenge_metadata(challenge_metadata) or {}
                session_id = metadata_obj.get("sessionId", "")
                ok, info = run_pow_solver(
                    session_id=session_id,
                    challenge_id=challenge_id,
                    cookie_value=cookie_value,
                    proxy_value=proxy_value,
                )
                if ok:
                    clear_ok, _ = verify_join_game_clear(cookie_value=cookie_value, proxy_value=proxy_value)
                    if clear_ok:
                        success(f"Solved Proof Of Work: {username}")
                        return STATUS_SOLVED
                reason_out = []
                if launch_challenge_browser(
                    challenge_url,
                    chrome_path=chrome_path,
                    username=username,
                    cookie_value=cookie_value,
                    window_position=window_position_for_slot(0),
                    raw_line=raw_line,
                    proxy_value=proxy_value,
                    extension_path=extension_path,
                    pow_mode=True,
                    browser_lang=browser_lang,
                    reason_out=reason_out,
                ):
                    return STATUS_SOLVED
                return STATUS_FAILED

            if pow_only:
                warn(f"Skip Captcha (pow-only): {username}")
                return "skipped"

            _notify_stage("captcha_started")
            if launch_challenge_browser(
                challenge_url,
                chrome_path=chrome_path,
                username=username,
                cookie_value=cookie_value,
                window_position=window_position_for_slot(0),
                raw_line=raw_line,
                proxy_value=proxy_value,
                extension_path=extension_path,
                pow_mode=False,
                browser_lang=browser_lang,
            ):
                return STATUS_SOLVED
            return STATUS_FAILED

        if is_user_moderated(cookie_value=cookie_value, proxies=proxies):
            error(f"Banned (moderated): {username}")
            return STATUS_FAILED

    return STATUS_FAILED
