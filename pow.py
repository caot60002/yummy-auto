import argparse
import ctypes
import json
import os
import random
import re
import time
from pathlib import Path
from urllib.parse import quote, urlparse
import requests

DEFAULT_BASE_URL = "https://apis.roblox.com/proof-of-work-service/v1/pow-puzzle"
PROXY_PATH = "proxies.txt"
DEFAULT_ORIGIN = "https://www.roblox.com"
DEFAULT_REFERER = "https://www.roblox.com/"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 15.0
CSRF_URL = "https://auth.roblox.com/v2/login"
CHALLENGE_CONTINUE_URL = "https://apis.roblox.com/challenge/v1/continue"
MAX_DETAIL_LEN = 200


def normalize_proxy_value(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if "://" in value:
        value = value.split("://", 1)[1].strip()
    return value


def is_valid_proxy_value(value: str) -> bool:
    if not value:
        return False
    candidate = value if "://" in value else f"http://{value}"
    try:
        parsed = urlparse(candidate)
        return bool(parsed.hostname and parsed.port and 1 <= parsed.port <= 65535)
    except Exception:
        return False


def load_proxies() -> list[str]:
    base_dir = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
    direct_path = Path(PROXY_PATH)
    path = direct_path if direct_path.is_file() else (base_dir / PROXY_PATH)
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    proxies = []
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        low = raw.lower()
        if "example" in low or "only ip:port" in low:
            continue
        normalized = normalize_proxy_value(raw)
        if is_valid_proxy_value(normalized):
            proxies.append(normalized)
    return proxies


def choose_proxy(proxy_value: str = "") -> str:
    value = normalize_proxy_value(proxy_value)
    if is_valid_proxy_value(value):
        return value
    proxies = load_proxies()
    if not proxies:
        return ""
    return random.choice(proxies)


def build_requests_proxies(proxy_value: str) -> dict | None:
    normalized = normalize_proxy_value(proxy_value)
    if not is_valid_proxy_value(normalized):
        return None
    proxy_url = f"http://{normalized}"
    return {"http": proxy_url, "https": proxy_url}


def _sanitize_detail(text: str) -> str:
    text = (text or "").replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"https?://\S+", "<redacted-url>", text)
    text = re.sub(r"[A-Za-z0-9_-]{24,}", "<redacted>", text)
    if len(text) > MAX_DETAIL_LEN:
        return text[:MAX_DETAIL_LEN] + "..."
    return text


def _extract_error_detail(body_bytes: bytes) -> str:
    if not body_bytes:
        return ""
    try:
        text = body_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return "response not utf-8"
    text = text.strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _sanitize_detail(text)

    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            parts = []
            for err in errors[:2]:
                if isinstance(err, dict):
                    msg = err.get("message") or err.get("Message")
                    code = err.get("code") or err.get("Code")
                    field = err.get("field") or err.get("Field")
                    piece = ""
                    if code is not None:
                        piece += f"code={code}"
                    if msg:
                        piece += f" message={msg}" if piece else f"message={msg}"
                    if field:
                        piece += f" field={field}" if piece else f"field={field}"
                    if piece:
                        parts.append(piece)
            if parts:
                return _sanitize_detail("; ".join(parts))

        for key in ("message", "error", "errorMessage", "error_description", "errorDescription", "reason"):
            if key in payload and payload[key]:
                return _sanitize_detail(f"{key}={payload[key]}")
        return "json response without error details"

    if isinstance(payload, list):
        return f"response_list_len={len(payload)}"
    return _sanitize_detail(str(payload))


def _format_error(exc: Exception) -> str:
    reason = getattr(exc, "reason", None)
    cause = getattr(exc, "__cause__", None)
    if reason:
        return _sanitize_detail(str(reason))
    if cause:
        return _sanitize_detail(f"{type(exc).__name__}: {cause}")
    return _sanitize_detail(str(exc))


def build_url(base_url: str, session_id: str) -> str:
    return f"{base_url}?sessionID={quote(session_id, safe='')}"


def build_headers(method: str, cookie: str = None, csrf: str = None) -> dict:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "user-agent": (
            "Mozilla/5.0 (5955MB; 960x540; 160x160; 960x540; Samsung SM-N976N; 9) "
            "AppleWebKit/537.36 (KHTML, like Gecko)  ROBLOX Android App 2.668.660 "
            "Tablet Hybrid()  GooglePlayStore RobloxApp/2.668.660 (GlobalDist; GooglePlayStore)"
        ),
        "Origin": DEFAULT_ORIGIN,
        "Referer": DEFAULT_REFERER,
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
        "Priority": "u=1, i",
        "Sec-CH-UA": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }
    if cookie:
        headers["Cookie"] = cookie
    if csrf:
        headers["X-CSRF-Token"] = csrf
    if method.upper() == "POST":
        headers["Content-Type"] = "application/json;charset=UTF-8"
    return headers


def fetch(
    url: str,
    headers: dict,
    timeout: float,
    method: str = "GET",
    body: bytes | str | None = None,
    proxy_value: str = "",
) -> tuple[int, bytes]:
    if isinstance(body, str):
        body = body.encode("utf-8")
    proxies = build_requests_proxies(proxy_value)
    resp = requests.request(
        method,
        url,
        headers=headers,
        data=body,
        timeout=timeout,
        proxies=proxies,
    )
    content = resp.content
    resp.raise_for_status()
    return resp.status_code, content


def get_csrf_token(cookie: str, proxy_value: str = "") -> str:
    headers = build_headers("POST", cookie=cookie)
    try:
        fetch(CSRF_URL, headers, REQUEST_TIMEOUT, "POST", body=b"", proxy_value=proxy_value)
    except requests.HTTPError as exc:
        resp = exc.response
        if resp is not None:
            token = resp.headers.get("x-csrf-token")
            if token:
                return token
        raise
    return ""


def fetch_puzzle(session_id: str, cookie: str = None, proxy_value: str = "") -> tuple[int, bytes]:
    headers = build_headers("GET", cookie=cookie)
    url = build_url(DEFAULT_BASE_URL, session_id)
    status, response_body = fetch(url, headers, REQUEST_TIMEOUT, "GET", proxy_value=proxy_value)
    return status, response_body


def submit_solution(
    session_id: str,
    solution: str,
    cookie: str = None,
    proxy_value: str = "",
) -> tuple[int, bytes]:
    csrf = get_csrf_token(cookie, proxy_value=proxy_value) if cookie else None
    headers = build_headers("POST", cookie=cookie, csrf=csrf)
    url = DEFAULT_BASE_URL
    payload = {
        "sessionID": session_id,
        "solution": str(solution),
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    status, response_body = fetch(url, headers, REQUEST_TIMEOUT, "POST", body=body, proxy_value=proxy_value)
    return status, response_body


def submit_challenge_continue(
    challenge_id: str,
    redemption_token: str,
    session_id: str,
    cookie: str = None,
    proxy_value: str = "",
) -> tuple[int, bytes]:
    csrf = get_csrf_token(cookie, proxy_value=proxy_value) if cookie else None
    headers = build_headers("POST", cookie=cookie, csrf=csrf)
    payload = {
        "challengeId": challenge_id,
        "challengeType": "proofofwork",
        "challengeMetadata": json.dumps(
            {
                "redemptionToken": redemption_token,
                "sessionId": session_id,
            }
        ),
    }
    body = json.dumps(payload)
    status, response_body = fetch(
        CHALLENGE_CONTINUE_URL,
        headers,
        REQUEST_TIMEOUT,
        "POST",
        body=body,
        proxy_value=proxy_value,
    )
    return status, response_body


def load_solver() -> ctypes.CDLL:
    solver = ctypes.CDLL("./pow.dll")
    solver.solve_pow.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    solver.solve_pow.restype = ctypes.c_void_p
    solver.free_string.argtypes = [ctypes.c_void_p]
    solver.free_string.restype = None
    return solver


def solve_artifacts(solver: ctypes.CDLL, artifacts_json: str) -> str:
    art = json.loads(artifacts_json)
    n_str = str(art["N"]).encode("ascii")
    a_str = str(art["A"]).encode("ascii")
    t_val = int(art["T"])
    res_ptr = solver.solve_pow(n_str, a_str, t_val)
    if not res_ptr:
        return ""
    try:
        solution = ctypes.string_at(res_ptr).decode("ascii")
    finally:
        solver.free_string(res_ptr)
    return solution


def normalize_cookie(cookie: str) -> str:
    if not cookie:
        return ""
    if ".ROBLOSECURITY=" in cookie or "ROBLOSECURITY=" in cookie:
        return cookie
    return f".ROBLOSECURITY={cookie}"


def main(
    cookie: str,
    session_id: str,
    challenge_id: str,
    debug: bool = False,
    proxy_value: str = "",
):
    proxy_value = choose_proxy(proxy_value)
    cookie = normalize_cookie(cookie)

    def fail(step: str, status: int = None, detail: str = None, error: str = None):
        if not debug:
            return False
        info = {"step": step}
        if status is not None:
            info["status"] = status
        if detail:
            info["detail"] = _sanitize_detail(detail)
        if error:
            info["error"] = _sanitize_detail(error)
        return False, info

    def ok(status: int = None):
        if not debug:
            return True
        return True, {"step": "done", "status": status}

    try:
        status, body = fetch_puzzle(session_id=session_id, cookie=cookie, proxy_value=proxy_value)
    except requests.HTTPError as exc:
        resp = exc.response
        return fail(
            "fetch_puzzle",
            status=resp.status_code if resp is not None else None,
            detail=_extract_error_detail(resp.content) if resp is not None else None,
            error=_format_error(exc),
        )
    except requests.RequestException as exc:
        return fail("fetch_puzzle", error=_format_error(exc))

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return fail("parse_puzzle", status=status, detail=_extract_error_detail(body))

    artifacts = payload.get("artifacts")
    if not artifacts:
        return fail("parse_puzzle", status=status, detail="missing artifacts")

    try:
        solver = load_solver()
    except Exception as exc:
        return fail("load_solver", error=_format_error(exc))

    try:
        solution = solve_artifacts(solver, artifacts)
    except Exception as exc:
        return fail("solve_artifacts", error=_format_error(exc))

    submit_id = payload.get("sessionID") or session_id
    try:
        submit_status, submit_body = submit_solution(
            session_id=submit_id,
            solution=solution,
            cookie=cookie,
            proxy_value=proxy_value,
        )
    except requests.HTTPError as exc:
        resp = exc.response
        return fail(
            "submit_solution",
            status=resp.status_code if resp is not None else None,
            detail=_extract_error_detail(resp.content) if resp is not None else None,
            error=_format_error(exc),
        )
    except requests.RequestException as exc:
        return fail("submit_solution", error=_format_error(exc))

    try:
        submit_payload = json.loads(submit_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return fail("parse_submit", status=submit_status, detail=_extract_error_detail(submit_body))

    redemption_token = submit_payload.get("redemptionToken")
    if not redemption_token:
        return fail("parse_submit", status=submit_status, detail="missing redemptionToken")

    time.sleep(2)
    try:
        cont_status, cont_body = submit_challenge_continue(
            challenge_id=challenge_id,
            redemption_token=redemption_token,
            session_id=submit_id,
            cookie=cookie,
            proxy_value=proxy_value,
        )
    except requests.HTTPError as exc:
        resp = exc.response
        return fail(
            "challenge_continue",
            status=resp.status_code if resp is not None else None,
            detail=_extract_error_detail(resp.content) if resp is not None else None,
            error=_format_error(exc),
        )
    except requests.RequestException as exc:
        return fail("challenge_continue", error=_format_error(exc))

    if cont_status not in (200, 204):
        return fail("challenge_continue", status=cont_status, detail=_extract_error_detail(cont_body))

    return ok(status=cont_status)
