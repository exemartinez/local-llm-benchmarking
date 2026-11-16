from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    """Small stdlib client that only permits a loopback Ollama endpoint."""

    def __init__(self, base_url: str, timeout: float = 900, retries: int = 0):
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Ollama URL must use local loopback HTTP (127.0.0.1)")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[dict, int]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = Request(self.base_url + path, data=data, method=method, headers={"Content-Type": "application/json"})
        attempts = 0
        while True:
            attempts += 1
            try:
                with urlopen(req, timeout=self.timeout) as response:
                    body = response.read()
                return json.loads(body), attempts - 1
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:500]
                if exc.code >= 500 and attempts <= self.retries:
                    time.sleep(min(2 ** (attempts - 1), 4))
                    continue
                raise OllamaError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempts <= self.retries:
                    time.sleep(min(2 ** (attempts - 1), 4))
                    continue
                raise OllamaError(f"Ollama request failed: {exc}") from exc

    def list_models(self) -> tuple[dict, int]:
        return self.request("GET", "/api/tags")

    def version(self) -> tuple[dict, int]:
        return self.request("GET", "/api/version")

    def show_model(self, tag: str) -> tuple[dict, int]:
        return self.request("POST", "/api/show", {"model": tag})

    def pull_model(self, tag: str) -> tuple[dict, int]:
        return self.request("POST", "/api/pull", {"name": tag, "stream": False})

    def chat(self, tag: str, prompt: str, settings: dict) -> tuple[dict, int]:
        body = {
            "model": tag,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "keep_alive": settings["keep_alive"],
            "think": settings["think"],
            "options": {
                "temperature": settings["temperature"],
                "seed": settings["seed"],
                "num_ctx": settings["num_ctx"],
                "num_predict": settings["num_predict"],
            },
        }
        return self.request("POST", "/api/chat", body)
