import asyncio
import json
import os
import re
import threading
import time
from datetime import datetime
import requests
import websocket
from solve_api import OmoCaptcha, YesCaptcha

PORT = 9223
CDP_JSON_URL = f"http://127.0.0.1:{PORT}/json"
RUN_DEADLINE = None
SOLVER_PROVIDER = "yescaptcha"
SOLVER_CLIENT_KEY = None
SOLVER_TIMEOUT_SECONDS = 120
SOLVER_REQUEST_TIMEOUT_SECONDS = 25
SOLVER_HTTP_RETRY_ATTEMPTS = 2
SOLVER_RETRY_ATTEMPTS = 20
SOLVER_RETRY_BACKOFF_SECONDS = 0.5
INSTRUCTION_RETRY_ATTEMPTS = 20
INSTRUCTION_RETRY_INTERVAL_SECONDS = 0.2
CAPTURE_FAIL_STREAK_LIMIT = 8
START_CLICK_STALL_LIMIT = 8
SOLVE_IN_APP_LOG_PATH = os.path.join("log", "logs.txt")

JS_CLICKER = """
(function(action) {
    const findAndClick = (target) => {
        // --- 1. Logic tìm theo Selector (Dành cho Next/Previous/Start) ---
        const selectors = {
            'next': '[aria-label="Navigate to next image"], .core-next, [aria-label*="next"]',
            'prev': '[aria-label="Navigate to previous image"], .core-prev, [aria-label*="previous"]',
            'start': '[aria-label="Start Puzzle"], #game_children_button, .match-game-start-button',
            'reload': 'button[aria-label*="Reload"], button[aria-label*="reload"], button[aria-label*="Refresh"], button[aria-label*="refresh"], [role="button"][aria-label*="Reload"], [role="button"][aria-label*="reload"]',
            'refresh': 'button[aria-label*="Reload"], button[aria-label*="reload"], button[aria-label*="Refresh"], button[aria-label*="refresh"], [role="button"][aria-label*="Reload"], [role="button"][aria-label*="reload"]',
            'try_again': 'button[aria-label*="Try again"], button[aria-label*="try again"], [role="button"][aria-label*="Try again"], [role="button"][aria-label*="try again"]'
        };

        if (selectors[target]) {
            let btn = document.querySelector(selectors[target]);
            if (btn && btn.clientWidth > 0) {
                btn.click();
                return "SUCCESS_SELECTOR:" + target;
            }
        }

        // --- 2. Logic tìm theo Text (Dành cho Submit và dự phòng) ---
        // Quét tất cả các thẻ có khả năng là nút bấm
        const elements = document.querySelectorAll('button, [role="button"], div.primary, a.button');
        const searchTerms = {
            'submit': ['submit', 'gửi', 'xác nhận'],
            'start': ['start', 'bắt đầu'],
            'verify': ['verify', 'xác minh'],
            'reload': ['reload challenge', 'reload', 'refresh challenge', 'refresh', 'new challenge', 'try again', 'another'],
            'refresh': ['reload challenge', 'reload', 'refresh challenge', 'refresh', 'new challenge', 'try again', 'another'],
            'try_again': ['try again', 'retry', 'try another', 'reload challenge', 'new challenge']
        };

        let terms = searchTerms[target] || [target];
        
        for (let el of elements) {
            let txt = (el.innerText || el.textContent || "").trim().toLowerCase();
            if (terms.some(term => txt.includes(term)) && el.clientWidth > 0) {
                el.click();
                return "SUCCESS_TEXT:" + target;
            }
        }

        // --- 3. Logic XPath (Dự phòng cuối cùng cho Submit) ---
        if (target === 'submit') {
            const xpath = "//button[contains(translate(text(), 'SUBMIT', 'submit'), 'submit')]";
            const btnX = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            if (btnX && btnX.clientWidth > 0) {
                btnX.click();
                return "SUCCESS_XPATH:submit";
            }
        }

        return "NOT_FOUND";
    };

    return findAndClick(action);
})("%s")
"""

CDP_REQ_ID = 1000
_VERBOSE = False
_SESSION_STATE = threading.local()
_FILE_LOG_LOCK = threading.Lock()


def _get_state_value(name, default=None):
    return getattr(_SESSION_STATE, name, default)


def _set_state_value(name, value):
    setattr(_SESSION_STATE, name, value)
    return value


def _clear_session_state():
    for name in (
        "PORT",
        "CDP_JSON_URL",
        "RUN_DEADLINE",
        "SOLVER_PROVIDER",
        "SOLVER_CLIENT_KEY",
        "SESSION_LABEL",
        "CDP_REQ_ID",
        "VERBOSE",
    ):
        if hasattr(_SESSION_STATE, name):
            delattr(_SESSION_STATE, name)


def _get_port() -> int:
    return int(_get_state_value("PORT", PORT))


def _get_cdp_json_url() -> str:
    return str(_get_state_value("CDP_JSON_URL", CDP_JSON_URL))


def _get_run_deadline():
    return _get_state_value("RUN_DEADLINE", RUN_DEADLINE)


def _get_solver_provider() -> str:
    return str(_get_state_value("SOLVER_PROVIDER", SOLVER_PROVIDER))


def _get_solver_client_key():
    return _get_state_value("SOLVER_CLIENT_KEY", SOLVER_CLIENT_KEY)


def _get_session_label() -> str:
    return str(_get_state_value("SESSION_LABEL", "") or "").strip()


def _get_verbose() -> bool:
    return bool(_get_state_value("VERBOSE", _VERBOSE))


def _get_session_state_snapshot() -> dict:
    return {
        "port": _get_port(),
        "cdp_json_url": _get_cdp_json_url(),
        "solver_provider": _get_solver_provider(),
        "solver_client_key": _get_solver_client_key(),
        "session_label": _get_session_label(),
        "run_deadline": _get_run_deadline(),
        "verbose": _get_verbose(),
        "cdp_req_id": _get_state_value("CDP_REQ_ID", CDP_REQ_ID),
    }


def _retry_attempt_count(
    window_seconds: float,
    interval: float,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    window = max(0.1, float(window_seconds))
    step = max(0.01, float(interval))
    attempts = max(int(minimum), int(window / step))
    if maximum is not None:
        attempts = min(int(maximum), attempts)
    return attempts


def set_verbose(enabled: bool = True):
    global _VERBOSE
    _VERBOSE = bool(enabled)
    _set_state_value("VERBOSE", bool(enabled))


def _format_log_message(message: str) -> str:
    parts = ["in-app"]
    session_label = _get_session_label()
    if session_label:
        parts.append(session_label)
    parts.append(f"port={_get_port()}")
    solver_provider = _get_solver_provider()
    if solver_provider:
        parts.append(f"provider={solver_provider}")
    return f"[{' '.join(parts)}] {message}"


def _should_persist_log_message(message: str) -> bool:
    text = str(message or "").strip()
    return text.startswith("[-]") or text.startswith("[+] Session finished:")


def _append_log_line(formatted_message: str):
    log_path = str(SOLVE_IN_APP_LOG_PATH or "").strip()
    if not log_path:
        return
    try:
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{timestamp} {formatted_message}\n"
        with _FILE_LOG_LOCK:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def _log(message: str, force: bool = False):
    formatted = _format_log_message(message)
    if _should_persist_log_message(message):
        _append_log_line(formatted)
    if _get_verbose() or force:
        print(formatted)


def _normalize_solver_provider(provider: str) -> str:
    name = (provider or "yescaptcha").strip().lower()
    if name in {"omocaptcha", "omo", "omo-captcha"}:
        return "omocaptcha"
    return "yescaptcha"


def _load_solver_key_from_config(provider: str) -> str:
    try:
        with open("config.json", "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            return ""
        if provider == "omocaptcha":
            return str(cfg.get("Omocaptcha Key", "")).strip()
        return str(cfg.get("Yescaptcha Key", "")).strip()
    except Exception:
        return ""


def configure_solver(captcha_provider: str = "yescaptcha", client_key: str = None):
    global SOLVER_PROVIDER, SOLVER_CLIENT_KEY
    provider = _normalize_solver_provider(captcha_provider)
    key = (client_key or "").strip()
    if not key:
        env_key = "OMOCAPTCHA_CLIENT_KEY" if provider == "omocaptcha" else "YESCAPTCHA_CLIENT_KEY"
        key = (os.getenv(env_key) or "").strip()
    if not key:
        key = _load_solver_key_from_config(provider)
    if not key:
        fallback_provider = "yescaptcha" if provider == "omocaptcha" else "omocaptcha"
        fallback_key = _load_solver_key_from_config(fallback_provider)
        if fallback_key:
            provider = fallback_provider
            key = fallback_key
    SOLVER_PROVIDER = provider
    SOLVER_CLIENT_KEY = key
    _set_state_value("SOLVER_PROVIDER", provider)
    _set_state_value("SOLVER_CLIENT_KEY", key)


def _solve_image(image_payload: str, instruction_text: str):
    provider = _get_solver_provider()
    key = (_get_solver_client_key() or "").strip()
    if not key:
        raise ValueError(f"{provider} client key is required")
    time_left = _time_left()
    if time_left <= 0:
        raise TimeoutError("Total timeout reached before solver request")
    solve_timeout = min(SOLVER_TIMEOUT_SECONDS, max(1, int(time_left)))
    if provider == "omocaptcha":
        return asyncio.run(
            OmoCaptcha.solve_image(
                image_payload,
                instruction_text,
                timeout=solve_timeout,
                client_key=key,
                http_retry_attempts=SOLVER_HTTP_RETRY_ATTEMPTS,
                deadline=_get_run_deadline(),
                request_timeout=SOLVER_REQUEST_TIMEOUT_SECONDS,
            )
        )
    return asyncio.run(
        YesCaptcha.solve_image(
            image_payload,
            instruction_text,
            timeout=solve_timeout,
            client_key=key,
            http_retry_attempts=SOLVER_HTTP_RETRY_ATTEMPTS,
            deadline=_get_run_deadline(),
            request_timeout=SOLVER_REQUEST_TIMEOUT_SECONDS,
        )
    )


def _is_retryable_solve_error(exc: Exception) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError, TimeoutError)):
        return True
    text = str(exc or "").lower()
    retry_markers = (
        "timeout",
        "timed out",
        "connection",
        "temporarily",
        "too many requests",
        "[408]",
        "[429]",
        "[500]",
        "[502]",
        "[503]",
        "[504]",
        "http error: 408",
        "http error: 429",
        "http error: 500",
        "http error: 502",
        "http error: 503",
        "http error: 504",
    )
    return any(marker in text for marker in retry_markers)


def _solve_image_with_retries(
    image_payload: str,
    instruction_text: str,
    max_attempts: int = SOLVER_RETRY_ATTEMPTS,
):
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        try:
            return _solve_image(image_payload, instruction_text)
        except Exception as exc:
            _log(f"[-] Solve attempt {attempt}/{attempts} failed: {exc}")
            if attempt >= attempts or not _is_retryable_solve_error(exc):
                raise
            if not _sleep_safe(min(5.0, SOLVER_RETRY_BACKOFF_SECONDS * attempt)):
                raise TimeoutError("Total timeout reached while solving image") from exc
    raise RuntimeError("Solver retry attempts exhausted")


def configure(port: int = 9223) -> bool:
    global PORT, CDP_JSON_URL
    try:
        resolved_port = int(port)
        PORT = resolved_port
        CDP_JSON_URL = f"http://127.0.0.1:{resolved_port}/json"
        _set_state_value("PORT", resolved_port)
        _set_state_value("CDP_JSON_URL", CDP_JSON_URL)
        return True
    except Exception:
        return False


def set_total_timeout(seconds: float):
    global RUN_DEADLINE
    if seconds is None:
        RUN_DEADLINE = None
        _set_state_value("RUN_DEADLINE", None)
        return
    try:
        sec = float(seconds)
    except Exception:
        sec = 420.0
    if sec <= 0:
        RUN_DEADLINE = None
        _set_state_value("RUN_DEADLINE", None)
    else:
        deadline = time.time() + sec
        RUN_DEADLINE = deadline
        _set_state_value("RUN_DEADLINE", deadline)


def _timed_out() -> bool:
    deadline = _get_run_deadline()
    return deadline is not None and time.time() >= deadline


def _time_left() -> float:
    deadline = _get_run_deadline()
    if deadline is None:
        return 999999.0
    return max(0.0, deadline - time.time())


def _sleep_safe(seconds: float) -> bool:
    if seconds <= 0:
        return not _timed_out()
    if _get_run_deadline() is None:
        time.sleep(seconds)
        return True
    left = _time_left()
    if left <= 0:
        return False
    time.sleep(min(seconds, left))
    return not _timed_out()


def _next_cdp_id() -> int:
    global CDP_REQ_ID
    next_id = int(_get_state_value("CDP_REQ_ID", CDP_REQ_ID)) + 1
    CDP_REQ_ID = next_id
    _set_state_value("CDP_REQ_ID", next_id)
    return next_id


def _recv_result(ws, expected_id: int, timeout: float = 1.5) -> dict | None:
    if _timed_out():
        return None
    if _get_run_deadline() is not None:
        left = _time_left()
        if left <= 0:
            return None
        timeout = max(0.1, min(timeout, left))
    ws.settimeout(timeout)
    while not _timed_out():
        try:
            msg = json.loads(ws.recv())
            if msg.get("id") == expected_id:
                return msg
        except Exception:
            break
    return None


def cdp_call(ws, method: str, params: dict = None, timeout: float = 2.5) -> dict | None:
    if _timed_out():
        return None
    req_id = _next_cdp_id()
    payload = {"id": req_id, "method": method}
    if params:
        payload["params"] = params
    ws.send(json.dumps(payload))
    return _recv_result(ws, req_id, timeout=timeout)


def iter_challenge_targets() -> list[dict]:
    if _timed_out():
        return []
    try:
        req_timeout = 5
        if _get_run_deadline() is not None:
            left = _time_left()
            if left <= 0:
                return []
            req_timeout = max(0.5, min(5.0, left))
        targets = requests.get(_get_cdp_json_url(), timeout=req_timeout).json()
        filtered = []
        for target in targets:
            if not isinstance(target, dict):
                continue
            ws_url = target.get("webSocketDebuggerUrl")
            if not ws_url:
                continue
            target_type = str(target.get("type", "") or "").lower()
            url = str(target.get("url", "") or "")
            if (
                "roblox.com" in url
                or "challenge" in url.lower()
                or target_type in {"page", "webview", "iframe"}
            ):
                filtered.append(target)
        filtered.sort(
            key=lambda t: (
                0 if "challenge" in str(t.get("url", "")).lower() else 1,
                str(t.get("id", "")),
            )
        )
        return filtered
    except Exception:
        return []


def get_all_context_ids(ws, wait_seconds: float = 1.2) -> list[dict]:
    if _timed_out():
        return []
    cached_contexts = getattr(ws, "_solve_in_app_contexts", None)
    if isinstance(cached_contexts, list) and cached_contexts:
        return list(cached_contexts)
    if _get_run_deadline() is not None:
        left = _time_left()
        if left <= 0:
            return []
        wait_seconds = max(0.1, min(wait_seconds, left))

    contexts = []
    ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
    end_time = time.time() + wait_seconds
    while time.time() < end_time and not _timed_out():
        try:
            ws.settimeout(max(0.05, end_time - time.time()))
            m = json.loads(ws.recv())
            if m.get("method") == "Runtime.executionContextCreated":
                c = m["params"]["context"]
                ctx_id = c.get("id")
                aux = c.get("auxData") or {}
                if ctx_id is not None:
                    contexts.append(
                        {
                            "id": ctx_id,
                            "origin": c.get("origin", ""),
                            "name": c.get("name", ""),
                            "frameId": aux.get("frameId"),
                            "isDefault": bool(aux.get("isDefault", False)),
                            "type": aux.get("type", ""),
                        }
                    )
        except Exception:
            break

    seen = set()
    uniq = []
    for c in contexts:
        cid = c["id"]
        if cid not in seen:
            seen.add(cid)
            uniq.append(c)

    if not uniq:
        uniq.append(
            {
                "id": 1,
                "origin": "",
                "name": "",
                "frameId": None,
                "isDefault": False,
                "type": "",
            }
        )

    def _ctx_score(c: dict) -> int:
        score = 0
        origin = (c.get("origin") or "").lower()
        if c.get("isDefault"):
            score += 4
        if origin.startswith("http://") or origin.startswith("https://"):
            score += 3
        if "roblox" in origin or "challenge" in origin:
            score += 2
        if (
            origin.startswith("chrome-extension://")
            or origin.startswith("devtools://")
            or origin.startswith("chrome://")
        ):
            score -= 6
        return score

    uniq.sort(key=_ctx_score, reverse=True)
    try:
        setattr(ws, "_solve_in_app_contexts", list(uniq))
    except Exception:
        pass
    return uniq


def _strip_data_url(data_url: str) -> str:
    if not data_url:
        return ""
    if data_url.startswith("data:image") and "," in data_url:
        return data_url.split(",", 1)[1]
    return data_url


def _normalize_clip_value(value) -> float:
    try:
        number = float(value)
        return number if number > 0 else 0.0
    except Exception:
        return 0.0


def _capture_visible_image_screenshot(ws, ctx_id: int, with_reason: bool = False):
    js_bounds = """
(() => {
    try {
        function isVisible(el) {
            if (!el) return false;
            const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
            if (style) {
                if (style.display === "none" || style.visibility === "hidden") return false;
                if (parseFloat(style.opacity || "1") <= 0) return false;
            }
            const rect = el.getBoundingClientRect();
            if (!rect || rect.width < 24 || rect.height < 24) return false;
            return true;
        }

        function makeCandidate(el, weight) {
            if (!isVisible(el)) return null;
            const rect = el.getBoundingClientRect();
            return {
                x: Math.max(0, rect.left),
                y: Math.max(0, rect.top),
                width: rect.width,
                height: rect.height,
                score: (rect.width * rect.height) + weight,
            };
        }

        const selectors = [
            ['[aria-label^="Image"] canvas', 300000],
            ['[aria-label^="Image"] img', 250000],
            ['canvas', 200000],
            ['img', 150000],
            ['[style*="background-image"]', 100000],
        ];

        const candidates = [];
        for (const [selector, weight] of selectors) {
            const nodes = document.querySelectorAll(selector);
            for (const node of nodes) {
                const candidate = makeCandidate(node, weight);
                if (candidate) {
                    candidates.push(candidate);
                }
            }
        }

        if (!candidates.length) return "SCREENSHOT_CLIP_NOT_FOUND";
        candidates.sort((a, b) => b.score - a.score);
        const best = candidates[0];
        return {
            x: best.x,
            y: best.y,
            width: best.width,
            height: best.height,
        };
    } catch (err) {
        return "SCREENSHOT_CLIP_ERROR: " + (err && err.message ? err.message : String(err));
    }
})();
    """
    bounds_msg = cdp_call(
        ws,
        "Runtime.evaluate",
        {
            "expression": js_bounds,
            "contextId": ctx_id,
            "returnByValue": True,
        },
        timeout=1.5,
    )
    bounds_value = (((bounds_msg or {}).get("result") or {}).get("result") or {}).get("value")
    if isinstance(bounds_value, dict):
        x = _normalize_clip_value(bounds_value.get("x"))
        y = _normalize_clip_value(bounds_value.get("y"))
        width = _normalize_clip_value(bounds_value.get("width"))
        height = _normalize_clip_value(bounds_value.get("height"))
        if width > 0 and height > 0:
            screenshot_msg = cdp_call(
                ws,
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "clip": {
                        "x": x,
                        "y": y,
                        "width": width,
                        "height": height,
                        "scale": 1,
                    },
                },
                timeout=2.5,
            )
            screenshot_data = str(((screenshot_msg or {}).get("result") or {}).get("data") or "").strip()
            if screenshot_data:
                data_url = f"data:image/png;base64,{screenshot_data}"
                return (data_url, None) if with_reason else data_url
            return (None, "SCREENSHOT_CAPTURE_EMPTY") if with_reason else None
    bounds_reason = str(bounds_value or "SCREENSHOT_CLIP_NOT_FOUND").strip()
    return (None, bounds_reason) if with_reason else None


def extract_blob_image_base64(ws, ctx_id: int, with_reason: bool = False):
    js_extract = """
(async function() {
    try {
        function readNodeUrl(node) {
            if (!node) return null;
            const src = node.getAttribute ? (node.getAttribute("src") || "") : "";
            if (src && (src.startsWith("blob:") || src.startsWith("data:image"))) {
                return src;
            }
            const bg = (node.style && node.style.backgroundImage)
                ? node.style.backgroundImage
                : ((window.getComputedStyle(node) || {}).backgroundImage || "");
            const m = (bg || "").match(/^url\\((['"]?)(.*?)\\1\\)$/);
            if (m && m[2]) return m[2];
            return null;
        }

        function findImageUrl(doc) {
            const direct = [
                '[aria-label^="Image"][style*="blob:"]',
                '[style*="blob:"]',
                'img[src^="blob:"]',
                'img[src^="data:image"]'
            ];
            for (const sel of direct) {
                const hit = doc.querySelector(sel);
                const found = readNodeUrl(hit);
                if (found) return found;
            }

            const broadNodes = doc.querySelectorAll('[style*="blob:"], img');
            for (const node of broadNodes) {
                const found = readNodeUrl(node);
                if (found && (found.startsWith("blob:") || found.startsWith("data:image"))) {
                    return found;
                }
            }

            const canvases = doc.querySelectorAll("canvas");
            for (const canvas of canvases) {
                try {
                    if ((canvas.width || 0) > 0 && (canvas.height || 0) > 0) {
                        return canvas.toDataURL("image/png");
                    }
                } catch (e) {}
            }

            return null;
        }

        function findImageUrlRecursive(doc) {
            const local = findImageUrl(doc);
            if (local) return local;
            const iframes = doc.querySelectorAll("iframe");
            for (let i = 0; i < iframes.length; i++) {
                try {
                    const child = iframes[i].contentDocument || iframes[i].contentWindow.document;
                    const nested = findImageUrlRecursive(child);
                    if (nested) return nested;
                } catch (e) {}
            }
            return null;
        }

        async function blobToDataUrlViaImage(blobUrl) {
            return await new Promise((resolve, reject) => {
                const img = new Image();
                img.onload = () => {
                    try {
                        const w = img.naturalWidth || img.width;
                        const h = img.naturalHeight || img.height;
                        if (!w || !h) {
                            reject(new Error("IMAGE_DIMENSION_EMPTY"));
                            return;
                        }
                        const canvas = document.createElement("canvas");
                        canvas.width = w;
                        canvas.height = h;
                        const ctx = canvas.getContext("2d");
                        ctx.drawImage(img, 0, 0);
                        resolve(canvas.toDataURL("image/png"));
                    } catch (e) {
                        reject(e);
                    }
                };
                img.onerror = () => reject(new Error("IMAGE_LOAD_ERROR"));
                img.src = blobUrl;
            });
        }

        const imageUrl = findImageUrlRecursive(document);
        if (!imageUrl) {
            return "BLOB_ELEMENT_NOT_FOUND";
        }

        if (imageUrl.startsWith("data:image")) {
            return imageUrl;
        }
        if (!imageUrl.startsWith("blob:")) {
            return "BLOB_URL_NOT_FOUND";
        }

        // Prefer fetch+FileReader; some sites block fetch(blob:) via CSP.
        try {
            const response = await fetch(imageUrl);
            const blob = await response.blob();
            return await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onloadend = () => resolve(reader.result);
                reader.onerror = () => reject(new Error("FILE_READER_ERROR"));
                reader.readAsDataURL(blob);
            });
        } catch (fetchErr) {
            try {
                return await blobToDataUrlViaImage(imageUrl);
            } catch (imgErr) {
                return "BLOB_FETCH_AND_IMAGE_FAIL: "
                    + (fetchErr && fetchErr.message ? fetchErr.message : String(fetchErr))
                    + " | "
                    + (imgErr && imgErr.message ? imgErr.message : String(imgErr));
            }
        }
    } catch (e) {
        return "BLOB_FETCH_ERROR: " + (e && e.message ? e.message : String(e));
    }
})();
    """
    msg = cdp_call(
        ws,
        "Runtime.evaluate",
        {
            "expression": js_extract,
            "contextId": ctx_id,
            "awaitPromise": True,
            "returnByValue": True,
        },
        timeout=2.0,
    )
    value = (((msg or {}).get("result") or {}).get("result") or {}).get("value")
    if isinstance(value, str) and value.startswith("data:image"):
        return (value, None) if with_reason else value
    reason = str(value) if value else "NO_VALUE_OR_EVAL_FAILED"
    screenshot_value, screenshot_reason = _capture_visible_image_screenshot(ws, ctx_id, with_reason=True)
    if screenshot_value:
        return (screenshot_value, None) if with_reason else screenshot_value
    if screenshot_reason and screenshot_reason != "SCREENSHOT_CLIP_NOT_FOUND":
        reason = f"{reason} | {screenshot_reason}"
    return (None, reason) if with_reason else None


def extract_instruction_text(ws, ctx_id: int, with_reason: bool = False):
    js_extract_text = """
(() => {
    try {
        const ACTION_RE = /\\b(select|click|tap|choose|match|drag|drop|rotate|find|identify|pick|arrange|move|complete|verify|follow)\\b/i;
        const DIRECTION_RE = /\\b(left|right|top|bottom|up|down|clockwise|counterclockwise|direction|arrow)\\b/i;
        const OBJECT_RE = /\\b(icon|icons|symbol|symbols|image|images|object|objects|dice|face|faces|tile|tiles|shape|shapes|color|colors)\\b/i;
        const COUNTER_RE = /^image\\s+\\d+\\s+of\\s+\\d+\\.?$/i;
        const UI_LABEL_RE = /^(verify|submit|next|previous|back|continue|audio challenge|refresh|reload|reload challenge|refresh challenge|new challenge|try again)$/i;

        function normalizeText(raw) {
            if (!raw) return "";
            return raw.replace(/\\s*\\(\\d+ of \\d+\\)\\s*$/i, "").replace(/\\s+/g, " ").trim();
        }

        function scoreText(text) {
            if (!text) return -1;
            const t = text.toLowerCase();
            if (text.length < 8 || text.length > 220) return -1;
            if (COUNTER_RE.test(t)) return -1;
            if (UI_LABEL_RE.test(t)) return -1;
            const words = t.split(/\\s+/).filter(Boolean).length;
            if (words < 3) return -1;

            let score = 0;
            if (ACTION_RE.test(t)) score += 5;
            if (DIRECTION_RE.test(t)) score += 3;
            if (OBJECT_RE.test(t)) score += 3;
            if (/[.:]/.test(text)) score += 1;
            score += Math.min(words, 20) / 20;
            return score;
        }

        function collectCandidates(doc) {
            const out = [];
            const nodes = doc.querySelectorAll('span[role="text"], div[role="text"], [aria-label], p, h1, h2, h3, div');
            for (const el of nodes) {
                const rawText = (el.textContent || "").trim() || (el.getAttribute && el.getAttribute("aria-label")) || "";
                const text = normalizeText(rawText);
                const score = scoreText(text);
                if (score > 0) out.push({ text, score });
            }
            return out;
        }

        function walk(doc) {
            let all = collectCandidates(doc);
            const iframes = doc.querySelectorAll("iframe");
            for (let i = 0; i < iframes.length; i++) {
                try {
                    const child = iframes[i].contentDocument || iframes[i].contentWindow.document;
                    all = all.concat(walk(child));
                } catch (e) {}
            }
            return all;
        }

        const allCandidates = walk(document);
        if (!allCandidates.length) return null;
        allCandidates.sort((a, b) => b.score - a.score);
        return allCandidates[0].text;
    } catch (err) {
        return null;
    }
})();
    """
    msg = cdp_call(
        ws,
        "Runtime.evaluate",
        {
            "expression": js_extract_text,
            "contextId": ctx_id,
            "awaitPromise": True,
            "returnByValue": True,
        },
        timeout=1.5,
    )
    value = (((msg or {}).get("result") or {}).get("result") or {}).get("value")
    if isinstance(value, str) and value.strip():
        return (value.strip(), None) if with_reason else value.strip()
    reason = "TEXT_NOT_FOUND"
    return (None, reason) if with_reason else None


def _score_instruction_text(text: str) -> float:
    if not text:
        return -1.0
    t = text.strip().lower()
    if len(t) < 8 or len(t) > 220:
        return -1.0
    if re.match(r"^image\s+\d+\s+of\s+\d+\.?$", t):
        return -1.0
    if t in {
        "submit",
        "continue",
        "previous",
        "next",
        "verify",
        "reload",
        "refresh",
        "reload challenge",
        "refresh challenge",
        "new challenge",
        "try again",
    }:
        return -1.0
    words = t.split()
    if len(words) < 3:
        return -1.0
    score = 0.0
    if re.search(
        r"\b(select|click|tap|choose|match|drag|drop|rotate|find|identify|pick|arrange|move|complete|follow)\b",
        t,
    ):
        score += 5.0
    if re.search(
        r"\b(left|right|top|bottom|up|down|clockwise|counterclockwise|direction|arrow)\b",
        t,
    ):
        score += 3.0
    if re.search(r"\b(icon|symbol|image|object|dice|face|tile|shape|color)\b", t):
        score += 3.0
    score += min(len(words), 20) / 20.0
    return score


def _clean_instruction_text(text: str) -> str:
    if not text:
        return ""
    t = re.sub(r"\s*\(\d+\s+of\s+\d+\)\s*.*$", "", text, flags=re.I)
    if re.fullmatch(
        r"(reload challenge|refresh challenge|new challenge|try again|reload|refresh)",
        t.strip(),
        flags=re.I,
    ):
        return ""
    ui_hit = re.search(
        r"\b(submit|audio|restart|done|verify|next|previous|back|continue|reload|refresh)\b",
        t,
        flags=re.I,
    )
    if ui_hit and ui_hit.start() > 20:
        t = t[: ui_hit.start()].strip()
    t = re.sub(r"\s*[a-f0-9]{10,}.*$", "", t, flags=re.I)
    t = re.sub(r"\s+\d+\s*$", "", t)
    return t.strip(" .,:;-")


def _select_best_instruction(instruction_candidates) -> str:
    cleaned_candidates = []
    for candidate in instruction_candidates or ():
        raw_text = candidate[0] if isinstance(candidate, (tuple, list)) and candidate else candidate
        cleaned = _clean_instruction_text(str(raw_text or ""))
        if cleaned and _score_instruction_text(cleaned) > 0:
            cleaned_candidates.append(cleaned)
    if not cleaned_candidates:
        return ""
    cleaned_candidates.sort(key=_score_instruction_text, reverse=True)
    return cleaned_candidates[0]


def _get_best_instruction_with_retries(
    ws,
    instruction_candidates=None,
    max_attempts: int = INSTRUCTION_RETRY_ATTEMPTS,
) -> str:
    if instruction_candidates is None:
        instruction_candidates = []
    best_instruction = _select_best_instruction(instruction_candidates)
    if best_instruction:
        return best_instruction
    attempts = max(1, int(max_attempts))
    for attempt in range(attempts):
        if _timed_out():
            break
        contexts = get_all_context_ids(ws)
        for ctx in contexts:
            ctx_id = ctx["id"]
            instruction_text, _ = extract_instruction_text(ws, ctx_id, with_reason=True)
            if instruction_text:
                cleaned_instruction = _clean_instruction_text(instruction_text)
                if cleaned_instruction:
                    instruction_candidates.append(cleaned_instruction)
        best_instruction = _select_best_instruction(instruction_candidates)
        if best_instruction:
            return best_instruction
        if attempt + 1 < attempts and not _sleep_safe(INSTRUCTION_RETRY_INTERVAL_SECONDS):
            break
    return ""


def rename_window(ws, new_title: str):
    js_rename = f"document.title = '{new_title}';"
    for cid in range(1, 20):
        payload = {
            "id": 777,
            "method": "Runtime.evaluate",
            "params": {"expression": js_rename, "contextId": cid},
        }
        ws.send(json.dumps(payload))


def _click_by_label(ws, ctx_id: int, label: str) -> bool:
    js_click = (
        """
(function() {
    const targetLabel = """
        + json.dumps(label)
        + """;
    const selectors = {
        'start': '[aria-label="Start Puzzle"], #game_children_button, .match-game-start-button, [aria-label*="start"]',
        'next': '[aria-label="Navigate to next image"], .core-next, [aria-label*="next"]',
        'prev': '[aria-label="Navigate to previous image"], .core-prev, [aria-label*="previous"]',
        'submit': '[aria-label*="submit"], button[type="submit"], button[aria-label*="submit"], .submit, .btn-submit',
        'reload': 'button[aria-label*="Reload"], button[aria-label*="reload"], button[aria-label*="Refresh"], button[aria-label*="refresh"], [role="button"][aria-label*="Reload"], [role="button"][aria-label*="reload"]',
        'refresh': 'button[aria-label*="Reload"], button[aria-label*="reload"], button[aria-label*="Refresh"], button[aria-label*="refresh"], [role="button"][aria-label*="Reload"], [role="button"][aria-label*="reload"]',
        'try_again': 'button[aria-label*="Try again"], button[aria-label*="try again"], [role="button"][aria-label*="Try again"], [role="button"][aria-label*="try again"]'
    };
    const actionSelector = 'button, [role="button"], input[type="button"], input[type="submit"], a, [onclick], [tabindex], .button, .btn, .primary, div.primary';
    const isVisible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
        if (style && (style.display === "none" || style.visibility === "hidden" || parseFloat(style.opacity || "1") <= 0)) return false;
        const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
        return !!rect && rect.width > 0 && rect.height > 0;
    };
    const textFor = (el) => (el.innerText || el.textContent || el.value || el.getAttribute("aria-label") || el.getAttribute("title") || "").replace(/\\s+/g, " ").trim().toLowerCase();
    const areaOf = (el) => {
        const rect = el && el.getBoundingClientRect ? el.getBoundingClientRect() : null;
        return rect ? rect.width * rect.height : Number.MAX_SAFE_INTEGER;
    };
    const bySmallestVisible = (a, b) => areaOf(a) - areaOf(b);
    const isActionable = (el) => {
        if (!el || !isVisible(el)) return false;
        if (el.matches && el.matches(actionSelector)) return true;
        const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
        return !!style && style.cursor === "pointer";
    };
    const clickableAncestor = (el) => {
        let cur = el;
        let depth = 0;
        while (cur && cur !== document.body && depth < 8) {
            if (isActionable(cur)) return cur;
            cur = cur.parentElement;
            depth += 1;
        }
        return isVisible(el) ? el : null;
    };
    const dispatchClick = (el) => {
        if (!el || !isVisible(el)) return false;
        try { el.scrollIntoView({ block: "center", inline: "center" }); } catch (e) {}
        const rect = el.getBoundingClientRect();
        const x = Math.max(1, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
        const y = Math.max(1, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));
        const topEl = document.elementFromPoint ? document.elementFromPoint(x, y) : null;
        const clickTarget = clickableAncestor(topEl) || el;
        try { clickTarget.focus && clickTarget.focus(); } catch (e) {}
        const opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y };
        for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
            try {
                const ev = type.startsWith("pointer")
                    ? new PointerEvent(type, Object.assign({ pointerId: 1, pointerType: "mouse", isPrimary: true }, opts))
                    : new MouseEvent(type, opts);
                clickTarget.dispatchEvent(ev);
            } catch (e) {}
        }
        try { clickTarget.click && clickTarget.click(); } catch (e) {}
        return true;
    };
    const roots = () => {
        const out = [document];
        const all = Array.from(document.querySelectorAll("*"));
        for (const el of all) {
            if (el.shadowRoot) out.push(el.shadowRoot);
        }
        return out;
    };
    const sel = selectors[targetLabel];
    if (sel) {
        for (const root of roots()) {
            const el = root.querySelector(sel);
            if (dispatchClick(clickableAncestor(el) || el)) {
                return true;
            }
        }
    }
    const labelTerms = {
        "submit": ["submit", "verify", "confirm", "done"],
        "reload": ["reload challenge", "reload", "refresh challenge", "refresh", "new challenge", "try again", "another"],
        "refresh": ["reload challenge", "reload", "refresh challenge", "refresh", "new challenge", "try again", "another"],
        "try_again": ["try again", "retry", "try another", "reload challenge", "new challenge"]
    };
    const terms = labelTerms[targetLabel] || [targetLabel];
    if (terms.length) {
        for (const root of roots()) {
            const directCandidates = Array.from(root.querySelectorAll(actionSelector)).filter(isVisible).sort(bySmallestVisible);
            for (const el of directCandidates) {
                const txt = textFor(el);
                if (!txt) continue;
                if (terms.some((term) => txt.includes(term)) && dispatchClick(el)) {
                    return true;
                }
            }
            const textCandidates = Array.from(root.querySelectorAll("button, [role='button'], input, a, div, span, p")).filter(isVisible).sort(bySmallestVisible);
            for (const el of textCandidates) {
                const txt = textFor(el);
                if (!txt) continue;
                if (terms.some((term) => txt.includes(term))) {
                    const clickable = clickableAncestor(el);
                    if (dispatchClick(clickable)) return true;
                }
            }
        }
    }
    return false;
})();
    """
    )
    msg = cdp_call(
        ws,
        "Runtime.evaluate",
        {
            "expression": js_click,
            "contextId": ctx_id,
            "userGesture": True,
            "returnByValue": True,
        },
        timeout=1.5,
    )
    return bool((((msg or {}).get("result") or {}).get("result") or {}).get("value"))


def has_start_button(ws, ctx_id: int) -> bool:
    js_check = """
(() => {
    const sel = '[aria-label="Start Puzzle"], #game_children_button, .match-game-start-button, [aria-label*="start"]';
    const el = document.querySelector(sel);
    if (el && el.clientWidth > 0) return true;
    const candidates = document.querySelectorAll('button, [role="button"], div.primary, a.button');
    for (const btn of candidates) {
        const txt = (btn.innerText || btn.textContent || "").trim().toLowerCase();
        if (!txt) continue;
        if (txt.includes("start") || txt.includes("bắt đầu") || txt.includes("bat dau")) {
            if (btn.clientWidth > 0) return true;
        }
    }
    return false;
})();
    """
    msg = cdp_call(
        ws,
        "Runtime.evaluate",
        {
            "expression": js_check,
            "contextId": ctx_id,
            "returnByValue": True,
        },
        timeout=1.0,
    )
    return bool((((msg or {}).get("result") or {}).get("result") or {}).get("value"))


def detect_refresh_button(ws, ctx_id: int, with_reason: bool = False):
    js_detect = """
(function() {
    const actionSelector = 'button, [role="button"], input[type="button"], input[type="submit"], a, [onclick], [tabindex], .button, .btn, .primary, div.primary';
    const isVisible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
        if (style && (style.display === "none" || style.visibility === "hidden" || parseFloat(style.opacity || "1") <= 0)) return false;
        const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
        return !!rect && rect.width > 0 && rect.height > 0;
    };
    const textFor = (el) => (el.innerText || el.textContent || el.value || el.getAttribute("aria-label") || el.getAttribute("title") || "").replace(/\\s+/g, " ").trim();
    const areaOf = (el) => {
        const rect = el && el.getBoundingClientRect ? el.getBoundingClientRect() : null;
        return rect ? rect.width * rect.height : Number.MAX_SAFE_INTEGER;
    };
    const roots = () => {
        const out = [document];
        const all = Array.from(document.querySelectorAll("*"));
        for (const el of all) {
            if (el.shadowRoot) out.push(el.shadowRoot);
        }
        return out;
    };
    const reloadTerms = ["reload challenge", "reload", "refresh challenge", "refresh", "new challenge", "try again", "retry", "try another", "another"];
    for (const root of roots()) {
        const candidates = Array.from(root.querySelectorAll(actionSelector + ', div, span, p')).filter(isVisible).sort((a, b) => areaOf(a) - areaOf(b));
        for (const el of candidates) {
            const raw = textFor(el);
            const txt = raw.toLowerCase();
            if (!txt) continue;
            if (reloadTerms.some((term) => txt.includes(term))) {
                return raw;
            }
        }
    }
    return null;
})();
    """
    msg = cdp_call(
        ws,
        "Runtime.evaluate",
        {
            "expression": js_detect,
            "contextId": ctx_id,
            "returnByValue": True,
        },
        timeout=1.2,
    )
    value = (((msg or {}).get("result") or {}).get("result") or {}).get("value")
    if isinstance(value, str) and value.strip():
        return (value.strip(), None) if with_reason else value.strip()
    return (None, "REFRESH_NOT_FOUND") if with_reason else None


def has_blob_aria(ws, ctx_id: int) -> bool:
    js_check = """
(function e(doc) {
    try {
        if (doc.querySelector('[aria-label^="Image"][style*="blob:"]')) return true;
        if (doc.querySelector('img[src^="blob:"], img[src^="data:image"], [style*="blob:"]')) return true;
        const canvases = doc.querySelectorAll("canvas");
        for (const canvas of canvases) {
            if ((canvas.width || 0) > 0 && (canvas.height || 0) > 0) return true;
        }
        const frames = doc.querySelectorAll("iframe");
        for (let i = 0; i < frames.length; i++) {
            try {
                const child = frames[i].contentDocument || frames[i].contentWindow.document;
                if (e(child)) return true;
            } catch (e) {}
        }
    } catch (e) {}
    return false;
})(document);
    """
    msg = cdp_call(
        ws,
        "Runtime.evaluate",
        {
            "expression": js_check,
            "contextId": ctx_id,
            "returnByValue": True,
        },
        timeout=1.0,
    )
    return bool((((msg or {}).get("result") or {}).get("result") or {}).get("value"))


def _find_action_context(ws, preferred_origin: str = None, preferred_frame: str = None) -> int | None:
    contexts = get_all_context_ids(ws)
    for ctx in contexts:
        if preferred_origin and ctx.get("origin") == preferred_origin:
            if has_blob_aria(ws, ctx["id"]):
                return ctx["id"]
        if preferred_frame and ctx.get("frameId") == preferred_frame:
            if has_blob_aria(ws, ctx["id"]):
                return ctx["id"]
    for ctx in contexts:
        if has_blob_aria(ws, ctx["id"]):
            return ctx["id"]
    return None


def _click_label_any_context(
    ws,
    label: str,
    preferred_origin: str = None,
    preferred_frame: str = None,
    first_ctx: int = None,
) -> tuple[bool, int | None]:
    ordered_ctx_ids = []
    if first_ctx is not None:
        ordered_ctx_ids.append(first_ctx)
    primary = _find_action_context(ws, preferred_origin=preferred_origin, preferred_frame=preferred_frame)
    if primary is not None and primary not in ordered_ctx_ids:
        ordered_ctx_ids.append(primary)
    for ctx in get_all_context_ids(ws):
        ctx_id = ctx["id"]
        if ctx_id not in ordered_ctx_ids:
            ordered_ctx_ids.append(ctx_id)
    for ctx_id in ordered_ctx_ids:
        if _click_by_label(ws, ctx_id, label):
            return True, ctx_id
    return False, None


def _click_reload_challenge(
    ws,
    preferred_origin: str = None,
    preferred_frame: str = None,
    first_ctx: int = None,
) -> tuple[bool, int | None]:
    for label in ("reload", "refresh", "try_again"):
        clicked, found_ctx = _click_label_any_context(
            ws,
            label,
            preferred_origin=preferred_origin,
            preferred_frame=preferred_frame,
            first_ctx=first_ctx,
        )
        if clicked:
            return True, found_ctx
    return False, None


def _perform_clicks(ws_url: str, steps: int, capture_ctx_info: dict = None) -> bool:
    if _timed_out():
        _log("[-] Total timeout reached before auto click.")
        return False
    ws = None
    try:
        ws = websocket.create_connection(ws_url, suppress_origin=True, timeout=2)
        preferred_origin = (capture_ctx_info or {}).get("origin")
        preferred_frame = (capture_ctx_info or {}).get("frameId")
        action_ctx = _find_action_context(
            ws,
            preferred_origin=preferred_origin,
            preferred_frame=preferred_frame,
        )
        if action_ctx is None:
            _log("[-] Auto click failed: no action context")
            return False

        clicks_done = 0
        attempts = 0
        max_attempts = max(8, steps * 10, 20)
        if steps > 0:
            _log(f"[+] Need {steps} next clicks before submit")
        else:
            _log("[+] Solver requested immediate submit")

        while clicks_done < steps and attempts < max_attempts:
            if _timed_out():
                _log("[-] Total timeout reached during next clicks.")
                return False
            attempts += 1
            clicked = False
            if action_ctx is not None and _click_by_label(ws, action_ctx, "next"):
                clicked = True
            else:
                clicked, found_ctx = _click_label_any_context(
                    ws,
                    "next",
                    preferred_origin=preferred_origin,
                    preferred_frame=preferred_frame,
                    first_ctx=action_ctx,
                )
                if found_ctx is not None:
                    action_ctx = found_ctx
            if clicked:
                clicks_done += 1
                _log(f"[+] Next click {clicks_done}/{steps}")
                _sleep_safe(0.12)
            else:
                if attempts % 3 == 0:
                    action_ctx = _find_action_context(
                        ws,
                        preferred_origin=preferred_origin,
                        preferred_frame=preferred_frame,
                    )
                _sleep_safe(0.08)

        if clicks_done < steps:
            _log(f"[-] Next clicks incomplete: {clicks_done}/{steps}")
            return False

        if steps > 0:
            _sleep_safe(0.2)

        submitted = False
        submit_attempts = _retry_attempt_count(
            window_seconds=4.0,
            interval=0.12,
            minimum=12,
            maximum=48,
        )
        for submit_attempt_index in range(submit_attempts):
            if _timed_out():
                _log("[-] Total timeout reached during submit.")
                return False
            if action_ctx is not None and _click_by_label(ws, action_ctx, "submit"):
                submitted = True
                _log(f"[+] Submit clicked on attempt {submit_attempt_index + 1}/{submit_attempts}")
                break
            clicked, found_ctx = _click_label_any_context(
                ws,
                "submit",
                preferred_origin=preferred_origin,
                preferred_frame=preferred_frame,
                first_ctx=action_ctx,
            )
            if clicked:
                submitted = True
                action_ctx = found_ctx
                _log(f"[+] Submit clicked on attempt {submit_attempt_index + 1}/{submit_attempts}")
                break
            if (submit_attempt_index + 1) % 5 == 0:
                _log(f"[~] Submit not ready yet ({submit_attempt_index + 1}/{submit_attempts})")
            _sleep_safe(0.12)

        if not submitted:
            _log("[-] Submit not found/clickable")
            for ctx in get_all_context_ids(ws):
                refresh_txt, _ = detect_refresh_button(ws, ctx["id"], with_reason=True)
                if refresh_txt:
                    _log(f"[+] Refresh button detected: {refresh_txt}")
                    clicked, refresh_ctx_id = _click_reload_challenge(
                        ws,
                        preferred_origin=preferred_origin,
                        preferred_frame=preferred_frame,
                        first_ctx=ctx["id"],
                    )
                    if clicked:
                        _log(f"[+] Reload Challenge clicked: {refresh_txt} (ctx={refresh_ctx_id})")
                        _sleep_safe(0.5)
                    break
            return False

        _log("[+] Clicked next and submit based on solve result")
        return True
    except Exception as e:
        _log(f"[-] Auto click failed: {e}")
        return False
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def wait_for_challenge(ws, timeout: float = 8.0, interval: float = 0.2) -> bool:
    end_time = time.time() + timeout
    while time.time() < end_time and not _timed_out():
        contexts = get_all_context_ids(ws)
        for ctx in contexts:
            if has_blob_aria(ws, ctx["id"]):
                return True
        if not _sleep_safe(interval):
            break
    return False


def wait_for_challenge_any_target(timeout: float = 8.0, interval: float = 0.2) -> bool:
    end_time = time.time() + timeout
    while time.time() < end_time and not _timed_out():
        for target in iter_challenge_targets():
            ws_url = target.get("webSocketDebuggerUrl")
            if not ws_url:
                continue
            ws = None
            try:
                ws = websocket.create_connection(ws_url, suppress_origin=True, timeout=2)
                ok = wait_for_challenge(
                    ws,
                    timeout=max(0.2, min(0.8, end_time - time.time())),
                    interval=interval,
                )
                if ok:
                    return True
            except Exception:
                pass
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
        if not _sleep_safe(interval):
            break
    return False


def execute_action(action_name: str, silent: bool = False) -> bool:
    if _timed_out():
        return False
    for target in iter_challenge_targets():
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            continue
        ws = None
        try:
            ws = websocket.create_connection(ws_url, suppress_origin=True, timeout=2)
            contexts = get_all_context_ids(ws)
            current_js = JS_CLICKER % action_name
            if action_name == "start":
                contexts = sorted(contexts, key=lambda c: c["id"], reverse=True)
                rename_window(ws, "Roblox - ROOO")
            for ctx in contexts:
                if _timed_out():
                    return False
                ctx_id = ctx["id"]
                msg = cdp_call(
                    ws,
                    "Runtime.evaluate",
                    {
                        "expression": current_js,
                        "contextId": ctx_id,
                        "userGesture": True,
                        "returnByValue": True,
                    },
                    timeout=1.0,
                )
                val = (((msg or {}).get("result") or {}).get("result") or {}).get("value")
                if isinstance(val, str) and "SUCCESS" in val:
                    if not silent:
                        _log(f"[+] Action '{action_name}' success in context {ctx_id}: {val}")
                    return True
                if _click_by_label(ws, ctx_id, action_name):
                    if not silent:
                        _log(f"[+] Action '{action_name}' click fallback in context {ctx_id}")
                    return True
        except Exception:
            pass
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
    if not silent:
        _log(f"[-] Could not find button '{action_name}' in any context.")
    return False


def capture_current_image() -> bool:
    if _timed_out():
        _log("[-] Total timeout reached before capture.")
        return False
    for target in iter_challenge_targets():
        if _timed_out():
            _log("[-] Total timeout reached before capture.")
            return False
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            continue
        ws = None
        captured_b64 = None
        capture_source = None
        capture_ctx_id = None
        capture_ctx_info = None
        instruction_candidates = []
        debug_reasons = []
        try:
            ws = websocket.create_connection(ws_url, suppress_origin=True, timeout=2)
            for _attempt in range(30):
                if _timed_out():
                    _log("[-] Total timeout reached while waiting image render.")
                    return False
                contexts = get_all_context_ids(ws)
                for ctx in contexts:
                    ctx_id = ctx["id"]
                    refresh_txt, _ = detect_refresh_button(ws, ctx_id, with_reason=True)
                    if refresh_txt:
                        _log(f"[+] Refresh button detected before capture: {refresh_txt}")
                        clicked, reload_ctx_id = _click_reload_challenge(ws, first_ctx=ctx_id)
                        if clicked:
                            _log(f"[+] Reload/Try again clicked before capture: {refresh_txt} (ctx={reload_ctx_id})")
                            _sleep_safe(0.5)
                    instruction_text, _ = extract_instruction_text(ws, ctx_id, with_reason=True)
                    if instruction_text:
                        cleaned_instruction = _clean_instruction_text(instruction_text)
                        if cleaned_instruction:
                            instruction_candidates.append(cleaned_instruction)
                    data_url, reason = extract_blob_image_base64(ws, ctx_id, with_reason=True)
                    if data_url:
                        captured_b64 = data_url
                        capture_ctx_id = ctx_id
                        capture_ctx_info = ctx
                        capture_source = (
                            f"ctx={ctx_id}, origin={ctx.get('origin')}, "
                            f"frame={ctx.get('frameId')}, default={ctx.get('isDefault')}"
                        )
                        break
                    else:
                        debug_reasons.append(
                            (
                                ctx_id,
                                ctx.get("isDefault"),
                                ctx.get("frameId"),
                                ctx.get("origin"),
                                reason,
                            )
                        )
                if captured_b64:
                    break
                if not _sleep_safe(0.2):
                    break

            if captured_b64:
                _log(f"[+] Captured from {capture_source}")
                best_instruction = _get_best_instruction_with_retries(ws, instruction_candidates)
                if best_instruction:
                    _log(f"[+] Instruction: {best_instruction}")
                else:
                    _log("[+] Instruction: (not found)")
                image_payload = _strip_data_url(captured_b64)
                try:
                    result = _solve_image_with_retries(image_payload, best_instruction)
                    _log(f"[+] Solve result: {result}")
                    if isinstance(result, (list, tuple)) and result:
                        steps = max(0, int(str(result[0]).strip()))
                    else:
                        steps = max(0, int(str(result).strip()))
                    return _perform_clicks(ws_url, steps, capture_ctx_info=capture_ctx_info)
                except Exception as e:
                    _log(f"[-] Solve failed: {e}")
                    return False
            else:
                for ctx in get_all_context_ids(ws):
                    reload_txt, _ = detect_refresh_button(ws, ctx["id"], with_reason=True)
                    if reload_txt:
                        _log(f"[+] Refresh button detected: {reload_txt}")
                        clicked, reload_ctx_id = _click_reload_challenge(ws, first_ctx=ctx["id"])
                        if clicked:
                            _log(f"[+] Reload Challenge clicked: {reload_txt} (ctx={reload_ctx_id})")
                            _sleep_safe(0.5)
                        break
                _log("[-] Blob aria element not found or cannot convert blob to base64.")
                if debug_reasons:
                    _log("[-] Context debug (ctx_id, default, frame, origin -> reason):")
                    for cid, is_default, frame_id, origin, reason in debug_reasons[:12]:
                        _log(f"    - {cid}, {is_default}, {frame_id}, {origin} -> {reason}")
        except Exception as e:
            _log(f"[-] Capture context error: {e}")
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
    _log("[-] Could not extract image.")
    return False


def run_auto_loop() -> bool:
    if not wait_for_challenge_any_target(timeout=1.2, interval=0.2):
        start_attempts = 0
        start_click_stalls = 0
        while not _timed_out():
            if execute_action("start", silent=True):
                _log("[+] Start clicked")
                if wait_for_challenge_any_target(timeout=3.0, interval=0.2):
                    break
                start_click_stalls += 1
                if start_click_stalls >= START_CLICK_STALL_LIMIT:
                    _log("[-] Start clicked repeatedly but challenge did not become visible.")
                    return False
                _sleep_safe(1.0)
            elif execute_action("reload", silent=True):
                _log("[+] Reload Challenge clicked")
                if wait_for_challenge_any_target(timeout=3.0, interval=0.2):
                    break
                _sleep_safe(1.0)
            elif execute_action("try_again", silent=True):
                _log("[+] Try again clicked")
                if wait_for_challenge_any_target(timeout=3.0, interval=0.2):
                    break
                _sleep_safe(1.0)
            else:
                start_attempts += 1
                if start_attempts >= 20:
                    _log("[-] Start loop stalled: challenge not visible.")
                    return False
                if not _sleep_safe(0.5):
                    break
        if _timed_out():
            _log("[-] Total timeout reached before start challenge.")
            return False

    capture_fail_streak = 0
    while not _timed_out():
        ok = capture_current_image()
        if ok:
            return True
        capture_fail_streak += 1
        _log(f"[-] Capture/solve failed ({capture_fail_streak}/{CAPTURE_FAIL_STREAK_LIMIT}).")
        if capture_fail_streak >= CAPTURE_FAIL_STREAK_LIMIT:
            return False
        if not wait_for_challenge_any_target(timeout=0.8, interval=0.2):
            _sleep_safe(0.4)
            if execute_action("reload", silent=True):
                _log("[+] Reload Challenge clicked")
                _sleep_safe(0.8)
            elif execute_action("try_again", silent=True):
                _log("[+] Try again clicked")
                _sleep_safe(0.8)
            else:
                execute_action("start", silent=True)
                _sleep_safe(0.6)
        else:
            _sleep_safe(0.3)

    _log("[-] Total timeout reached in solve loop.")
    return False


def solve_captcha(
    port: int = 9223,
    total_timeout: float = 420,
    verbose: bool = False,
    client_key: str = None,
    captcha_provider: str = "yescaptcha",
    session_label: str = None,
) -> bool:
    _clear_session_state()
    if session_label:
        _set_state_value("SESSION_LABEL", str(session_label).strip())
    set_verbose(verbose)
    if not configure(port):
        _log(f"[-] Invalid port: {port}", force=True)
        _clear_session_state()
        return False
    configure_solver(captcha_provider=captcha_provider, client_key=client_key)
    solver_provider = _get_solver_provider()
    if not _get_solver_client_key():
        _log(f"[-] Missing client key for provider '{solver_provider}'.", force=True)
        _clear_session_state()
        return False
    set_total_timeout(total_timeout)
    _log("[+] Session started")
    try:
        result = run_auto_loop()
        _log(f"[+] Session finished: {'solved' if result else 'failed'}")
        return result
    except Exception as e:
        _log(f"[-] Unhandled solver error: {e}", force=True)
        return False
    finally:
        set_total_timeout(None)
        _clear_session_state()
