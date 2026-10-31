import asyncio
import time
import requests

HTTP_RETRY_ATTEMPTS = 20
HTTP_RETRY_BACKOFF_SECONDS = 0.5
HTTP_RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}


def _sleep_backoff(attempt_index: int):
    delay = HTTP_RETRY_BACKOFF_SECONDS * max(1, int(attempt_index))
    time.sleep(delay)


def _is_retryable_request_error(exc):
    return isinstance(exc, (requests.Timeout, requests.ConnectionError, requests.RequestException))


def _remaining_deadline_seconds(deadline):
    if deadline is None:
        return None
    try:
        return float(deadline) - time.time()
    except Exception:
        return None


def _post_with_retry(url, payload, timeout, *, attempts=None, deadline=None, request_timeout=None):
    last_response = None
    max_attempts = HTTP_RETRY_ATTEMPTS if attempts is None else max(1, int(attempts))
    base_timeout = timeout if request_timeout is None else request_timeout

    for attempt_index in range(1, max_attempts + 1):
        effective_timeout = base_timeout
        remaining = _remaining_deadline_seconds(deadline)
        if remaining is not None:
            if remaining <= 0:
                raise requests.Timeout("deadline exceeded")
            effective_timeout = min(float(base_timeout), max(0.1, remaining))

        try:
            response = requests.post(url, json=payload, timeout=effective_timeout)
            last_response = response
        except requests.RequestException as exc:
            if attempt_index >= max_attempts or not _is_retryable_request_error(exc):
                raise
            delay = HTTP_RETRY_BACKOFF_SECONDS * max(1, int(attempt_index))
            remaining = _remaining_deadline_seconds(deadline)
            if remaining is not None:
                if remaining <= 0:
                    raise requests.Timeout("deadline exceeded")
                delay = min(delay, remaining)
            time.sleep(delay)
            continue

        if response.status_code not in HTTP_RETRYABLE_STATUSES or attempt_index >= max_attempts:
            return response

        delay = HTTP_RETRY_BACKOFF_SECONDS * max(1, int(attempt_index))
        remaining = _remaining_deadline_seconds(deadline)
        if remaining is not None:
            if remaining <= 0:
                raise requests.Timeout("deadline exceeded")
            delay = min(delay, remaining)
        time.sleep(delay)

    return last_response


class YesCaptcha:
    @staticmethod
    async def solve_image(
        image: str,
        game_question: str,
        timeout: int = 120,
        client_key: str | None = None,
        *,
        http_retry_attempts: int | None = None,
        deadline: float | None = None,
        request_timeout: float | None = None,
    ):
        key = (client_key or "").strip()
        if not key:
            raise Exception("YESCAPTCHA client key is required")

        def _request():
            return _post_with_retry(
                "https://api.yescaptcha.com/createTask",
                {
                    "clientKey": key,
                    "task": {
                        "type": "FunCaptchaClassification",
                        "image": image,
                        "question": game_question,
                    },
                },
                timeout,
                attempts=http_retry_attempts,
                deadline=deadline,
                request_timeout=request_timeout,
            )

        res = await asyncio.to_thread(_request)
        if res.status_code != 200:
            raise Exception(
                f"YesCaptcha API error: {res.json().get('errorCode', 'Unknown error')}[{res.status_code}]"
            )
        return list(res.json()["solution"]["objects"])


class OmoCaptcha:
    BASE_URL = "https://api.omocaptcha.com/v2"

    @staticmethod
    async def solve_image(
        image: str,
        game_question: str,
        timeout: int = 120,
        client_key: str | None = None,
        poll_interval: float = 2,
        *,
        http_retry_attempts: int | None = None,
        deadline: float | None = None,
        request_timeout: float | None = None,
    ):
        key = (client_key or "").strip()
        if not key:
            raise Exception("OMOcaptcha client key is required")

        def _post(url, payload):
            return _post_with_retry(
                url,
                payload,
                timeout,
                attempts=http_retry_attempts,
                deadline=deadline,
                request_timeout=request_timeout,
            )

        def _create_task():
            return _post(
                f"{OmoCaptcha.BASE_URL}/createTask",
                {
                    "clientKey": key,
                    "task": {
                        "type": "FuncaptchaImageTask",
                        "imageBase64": image,
                        "other": game_question,
                    },
                },
            )

        def _get_result(task_id: str):
            return _post(
                f"{OmoCaptcha.BASE_URL}/getTaskResult",
                {
                    "clientKey": key,
                    "taskId": task_id,
                },
            )

        create_res = await asyncio.to_thread(_create_task)
        if create_res.status_code != 200:
            raise Exception(f"OMOcaptcha createTask HTTP error: {create_res.status_code}")

        create_data = create_res.json()
        if create_data.get("errorId", 0) != 0:
            raise Exception(
                f"OMOcaptcha createTask error: {create_data.get('errorCode', 'Unknown error')} - {create_data.get('errorDescription', '')}"
            )

        task_id = str(create_data.get("taskId", "")).strip()
        if not task_id:
            raise Exception("OMOcaptcha createTask did not return taskId")

        solve_deadline = time.time() + timeout
        if deadline is not None:
            try:
                solve_deadline = min(solve_deadline, float(deadline))
            except Exception:
                pass

        while time.time() < solve_deadline:
            result_res = await asyncio.to_thread(_get_result, task_id)
            if result_res.status_code != 200:
                raise Exception(f"OMOcaptcha getTaskResult HTTP error: {result_res.status_code}")

            result_data = result_res.json()
            if result_data.get("errorId", 0) != 0:
                raise Exception(
                    f"OMOcaptcha getTaskResult error: {result_data.get('errorCode', 'Unknown error')} - {result_data.get('errorDescription', '')}"
                )

            status = str(result_data.get("status", "")).lower()
            if status == "ready":
                raw_index = result_data.get("solution", {}).get("index")
                if raw_index is None:
                    raise Exception("OMOcaptcha result missing solution index")
                try:
                    idx = int(raw_index)
                except Exception as exc:
                    raise Exception(f"OMOcaptcha invalid index: {raw_index}") from exc
                return [idx]

            if status not in ("processing", "pending"):
                raise Exception(f"OMOcaptcha unexpected status: {status}")

            await asyncio.sleep(min(0.3, max(0.05, solve_deadline - time.time())))

        raise Exception("OMOcaptcha task timeout")
