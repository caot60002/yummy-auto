import asyncio
import time
import requests


class YesCaptcha:
    @staticmethod
    async def solve_image(
        image: str,
        game_question: str,
        timeout: int = 120,
        client_key: str = None,
    ) -> str:
        key = (client_key or "").strip()
        if not key:
            raise ValueError("YESCAPTCHA client key is required")

        def _request():
            return requests.post(
                "https://api.yescaptcha.com/createTask",
                json={
                    "clientKey": key,
                    "task": {
                        "type": "FunCaptchaClassification",
                        "image": image,
                        "question": game_question,
                    },
                },
                timeout=timeout,
            )

        response = await asyncio.to_thread(_request)
        if response.status_code != 200:
            raise RuntimeError(
                f"YesCaptcha API error: {response.json().get('errorCode', 'Unknown error')} [{response.status_code}]"
            )
        return response.json()["solution"]["objects"][0]


class OmoCaptcha:
    BASE_URL = "https://api.omocaptcha.com/v2"

    @staticmethod
    async def solve_image(
        image: str,
        game_question: str,
        timeout: int = 120,
        client_key: str = None,
        poll_seconds: float = 2,
    ) -> str:
        key = (client_key or "").strip()
        if not key:
            raise ValueError("OMOcaptcha client key is required")

        def _post(url, payload, timeout=timeout):
            return requests.post(url, json=payload, timeout=timeout)

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

        create_response = await asyncio.to_thread(_create_task)
        if create_response.status_code != 200:
            raise RuntimeError(f"OMOcaptcha createTask HTTP error: {create_response.status_code}")

        create_data = create_response.json()
        if create_data.get("errorId"):
            raise RuntimeError(
                f"OMOcaptcha createTask error: {create_data.get('errorCode', 'Unknown error')} - {create_data.get('errorDescription', '')}"
            )

        task_id = str(create_data.get("taskId", "")).strip()
        if not task_id:
            raise RuntimeError("OMOcaptcha createTask did not return taskId")

        deadline = time.time() + timeout
        while time.time() < deadline:
            result_response = await asyncio.to_thread(_get_result, task_id)
            if result_response.status_code != 200:
                raise RuntimeError(
                    f"OMOcaptcha getTaskResult HTTP error: {result_response.status_code}"
                )

            result_data = result_response.json()
            if result_data.get("errorId"):
                raise RuntimeError(
                    f"OMOcaptcha getTaskResult error: {result_data.get('errorCode', 'Unknown error')} - {result_data.get('errorDescription', '')}"
                )

            status = str(result_data.get("status", "")).lower()
            if status == "ready":
                raw_index = (result_data.get("solution") or {}).get("index")
                if raw_index is None:
                    raise RuntimeError("OMOcaptcha result missing solution index")
                try:
                    index = int(raw_index)
                except Exception as exc:
                    raise ValueError(f"OMOcaptcha invalid index: {raw_index}") from exc
                return str(max(0, index))

            if status in {"processing", "pending"}:
                await asyncio.sleep(max(0.3, poll_seconds))
                continue

            raise RuntimeError(f"OMOcaptcha unexpected status: {status}")

        raise TimeoutError("OMOcaptcha task timeout")
