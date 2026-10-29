"""Minimal OneBot 11 HTTP caller — enough to drive ``qzone.py``.

NapCat can expose an HTTP action endpoint (``network.httpServers``). Enable one in
the NapCat WebUI, then:

    from onebot import OneBotHTTP
    call = OneBotHTTP("http://127.0.0.1:3000", token="your-token")
    ok, data, err = call("get_login_info", {})

A successful response must satisfy ``status == "ok" and retcode == 0``.
Checking only the HTTP status is a common mistake: an unknown action answers
HTTP 200 with ``status: "failed"`` and ``retcode: 200``.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

__all__ = ["OneBotHTTP", "OneBotError"]


class OneBotError(RuntimeError):
    """Raised when the endpoint is unreachable or the reply is not JSON."""


class OneBotHTTP:
    """Callable wrapper: ``call(action, params) -> (ok, data, error)``."""

    def __init__(self, base_url: str, token: str = "", *, timeout: float = 30.0):
        self.base_url = str(base_url or "").rstrip("/")
        self.token = str(token or "")
        self.timeout = float(timeout)

    def __call__(self, action: str, params: dict | None = None
                 ) -> tuple[bool, Any, str]:
        if not self.base_url:
            raise OneBotError("base_url is empty")
        body = json.dumps(params or {}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/{action}", data=body, method="POST",
            headers={"Content-Type": "application/json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError) as exc:
            raise OneBotError(f"cannot reach {self.base_url}: {exc}") from exc
        except ValueError as exc:
            raise OneBotError("reply is not JSON") from exc

        if not isinstance(payload, dict):
            raise OneBotError("reply is not an object")
        ok = (payload.get("status") == "ok"
              and int(payload.get("retcode") or 0) == 0)
        err = "" if ok else str(payload.get("message")
                                or payload.get("wording")
                                or f"retcode={payload.get('retcode')}")
        return ok, payload.get("data"), err
