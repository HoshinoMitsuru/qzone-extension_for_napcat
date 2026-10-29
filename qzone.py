"""Read and write QQ Zone (Qzone) through NapCat's own credentials.

Why this exists
---------------
NapCat exposes Qzone "public feed" actions only (get_qzone_public_feeds & friends),
which cover a recommendation stream, not a user's own timeline. But NapCat already
holds the QQ session, and its `get_cookies` action will hand out the `qzone.qq.com`
cookies plus a correctly derived `bkn` (it derives bkn from `p_skey`, which differs
from the generic skey algorithm). With those two values any Qzone cgi endpoint can
be called directly — no CDP, no UI automation, no browser, no extra login.

Design notes
------------
* Zero third-party dependencies (stdlib only).
* The caller injects a `Caller` — any callable shaped
  ``call(action: str, params: dict) -> (ok: bool, data: Any, error: str)``.
  See ``onebot.py`` for a ready-made OneBot 11 HTTP caller.
* Credentials are never written to disk and never logged.
* Read helpers are thin wrappers over Qzone cgi endpoints. Write helpers are
  deliberately NOT provided here; publishing to Qzone is an externally visible
  action and belongs in a layer that can ask for confirmation first.

Compatibility
-------------
Requires an OneBot 11 implementation that provides the ``get_cookies`` action.
As of this writing NapCat provides it; a plain go-cqhttp style implementation does
not, and this module will fail with a clear error.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable

__all__ = [
    "QzoneError",
    "Caller",
    "DEFAULT_POLICY",
    "auth",
    "check_target",
    "list_feeds",
    "feed_detail",
    "probe",
]

Caller = Callable[[str, dict], tuple[bool, Any, str]]

HOST = "https://user.qzone.qq.com"
TAOTAO = f"{HOST}/proxy/domain/taotao.qq.com/cgi-bin"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

#: Safe defaults: read your own timeline only. Widen ``scope`` deliberately.
DEFAULT_POLICY: dict = {
    "enabled": True,
    "scope": "self",        # self | whitelist | any
    "whitelist": [],
    "max_items": 20,
    "max_pages": 1,
}

#: Fields kept from an upstream feed object. Everything else is dropped so a
#: single unexpected upstream change cannot blow up the caller.
_FEED_FIELDS = ("tid", "content", "created_time", "cmtnum", "fwdnum", "secret",
                "lbs", "pics", "rt_con", "uin", "name")


class QzoneError(RuntimeError):
    """Raised for credential, transport, policy or upstream errors."""


def _loads_lenient(text: str) -> Any:
    """Parse Qzone's "almost JSON".

    Some legacy endpoints reply with JS object literals where keys are unquoted.
    Try strict JSON first, then quote bare keys once. Do not attempt more: a
    half-parsed payload is worse than a clear error.
    """
    try:
        return json.loads(text)
    except ValueError:
        pass
    patched = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)', r'\1"\2"\3', text)
    patched = re.sub(r"'([^'\\]*)'", r'"\1"', patched)
    patched = re.sub(r',\s*([}\]])', r'\1', patched)
    try:
        return json.loads(patched)
    except ValueError as exc:
        raise QzoneError(f"unparseable response (first 80 chars): {text[:80]}") from exc


def _strip_jsonp(text: str) -> str:
    text = text.strip()
    if not text.startswith("{"):
        start, end = text.find("("), text.rfind(")")
        if 0 <= start < end:
            text = text[start + 1:end]
    return text


def _request_text(url: str, cookie: str, *, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={
        "Cookie": cookie,
        "User-Agent": UA,
        "Referer": f"{HOST}/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise QzoneError(f"request failed: {exc}") from exc


def _request_json(url: str, cookie: str, *, timeout: float = 20.0) -> dict:
    data = _loads_lenient(_strip_jsonp(_request_text(url, cookie, timeout=timeout)))
    if not isinstance(data, dict):
        raise QzoneError("unexpected response shape (not an object)")
    if data.get("code") not in (0, None, "0"):
        raise QzoneError(f"upstream code={data.get('code')} "
                         f"message={str(data.get('message'))[:80]}")
    return data


def auth(call: Caller) -> tuple[str, str, str]:
    """Return ``(uin, bkn, cookie)`` for ``qzone.qq.com``.

    NapCat derives ``bkn`` from ``p_skey`` for this domain; use its value as-is
    instead of computing one from ``skey`` yourself.
    """
    ok, data, err = call("get_cookies", {"domain": "qzone.qq.com"})
    if not ok or not isinstance(data, dict):
        raise QzoneError(f"get_cookies failed: {err or 'no response'}")
    cookie = str(data.get("cookies") or "")
    bkn = str(data.get("bkn") or "")
    if not cookie or not bkn:
        raise QzoneError("incomplete credentials (missing cookies or bkn)")
    match = re.search(r"(?:^|;\s*)uin=o?(\d+)", cookie)
    if not match:
        raise QzoneError("no uin in credentials")
    return match.group(1), bkn, cookie


def check_target(policy: dict, target: str, self_uin: str) -> None:
    """Enforce the read scope. Empty ``target`` means "myself"."""
    merged = {**DEFAULT_POLICY, **(policy or {})}
    if not merged.get("enabled", True):
        raise QzoneError("qzone access disabled by policy")
    target = str(target or self_uin)
    scope = str(merged.get("scope") or "self")
    if scope == "any" or target == self_uin:
        return
    if scope == "whitelist" and target in {str(x) for x in (merged.get("whitelist") or [])}:
        return
    raise QzoneError(f"policy forbids reading {target} (scope={scope})")


def list_feeds(call: Caller, *, target: str = "", pos: int = 0, num: int = 20,
               policy: dict | None = None) -> dict:
    """Read a user's timeline.

    Returns ``{"uin", "target", "pos", "total", "items"}``. ``items`` keeps only
    the fields in ``_FEED_FIELDS``; image entries are flattened to URLs.

    Pagination is plain offset/limit: call again with ``pos=pos+num``.
    An empty ``content`` does **not** mean an empty post — image-only posts carry
    their media in ``pics``.
    """
    uin, bkn, cookie = auth(call)
    merged = {**DEFAULT_POLICY, **(policy or {})}
    check_target(merged, target, uin)

    cap = max(1, int(merged.get("max_items") or 20))
    num = max(1, min(int(num or cap), cap))
    pos = max(0, int(pos or 0))
    who = str(target or uin)

    url = (f"{TAOTAO}/emotion_cgi_msglist_v6?uin={who}&ftype=0&sort=0"
           f"&pos={pos}&num={num}&replynum=0&g_tk={bkn}&code_version=1&format=json")
    data = _request_json(url, cookie)

    raw = data.get("msglist") or []
    if not isinstance(raw, list):
        raw = []

    items = []
    for entry in raw[:num]:
        if not isinstance(entry, dict):
            continue
        row = {k: entry[k] for k in _FEED_FIELDS if k in entry}
        pics = row.get("pics")
        if isinstance(pics, list):
            row["pics"] = [p.get("url1") or p.get("url2") or p.get("url3")
                           for p in pics if isinstance(p, dict)]
        items.append(row)

    return {"uin": uin, "target": who, "pos": pos,
            "total": data.get("total"), "items": items}


def _epoch(value: Any) -> int | None:
    """Return a unix timestamp if ``value`` plausibly is one.

    Qzone returns two time fields with the same meaning: ``created_time`` (unix
    seconds) and ``createTime`` (a preformatted "HH:MM" string). Prefer the
    numeric one — the string loses the date.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "")
    return int(text) if text.isdigit() and len(text) >= 9 else None


def feed_detail(call: Caller, tid: str, *, target: str = "",
                policy: dict | None = None, max_comments: int = 20) -> dict:
    """Read one post with its comment list.

    One request is enough: the cgi returns content, images and comments together
    (the standalone comment endpoint answers HTTP 500).
    """
    tid = str(tid or "").strip()
    if not tid:
        raise QzoneError("tid is required")
    uin, bkn, cookie = auth(call)
    merged = {**DEFAULT_POLICY, **(policy or {})}
    check_target(merged, target, uin)

    who = str(target or uin)
    url = f"{TAOTAO}/emotion_cgi_msgdetail_v6?uin={who}&tid={tid}&g_tk={bkn}&format=json"
    data = _request_json(url, cookie)

    comments = data.get("commentlist") or []
    if not isinstance(comments, list):
        comments = []
    slim = [{
        "content": c.get("content"),
        "created_time": c.get("createTime") or c.get("created_time"),
        "name": c.get("name"),
        "uin": c.get("uin"),
    } for c in comments[:max(1, int(max_comments))] if isinstance(c, dict)]

    pics = data.get("pic") or data.get("pics") or []
    if isinstance(pics, dict):
        pics = [pics]
    urls = [p.get("url1") or p.get("url2") or p.get("url3")
            for p in pics if isinstance(p, dict)] if isinstance(pics, list) else []

    return {
        "tid": tid,
        "target": who,
        "content": data.get("content") or "",
        "created_time": (_epoch(data.get("created_time"))
                         or _epoch(data.get("createTime"))
                         or data.get("createTime")),
        "secret": data.get("secret"),
        "pics": urls,
        "comment_count": data.get("cmtnum"),
        "comments": slim,
    }


def probe(call: Caller, policy: dict | None = None) -> dict:
    """Cheap health check: fetch credentials and read exactly one post.

    Never returns post content — only whether the channel works.
    """
    uin, _bkn, _cookie = auth(call)
    res = list_feeds(call, num=1, policy={**DEFAULT_POLICY, **(policy or {})})
    first = res["items"][0] if res["items"] else {}
    return {
        "ok": True,
        "uin": uin,
        "total": res.get("total"),
        "fields": sorted(first.keys()),
        "has_content": bool(first.get("content")),
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
