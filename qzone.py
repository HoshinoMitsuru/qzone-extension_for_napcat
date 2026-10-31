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
from html.parser import HTMLParser
from typing import Any, Callable

__all__ = [
    "QzoneError",
    "Caller",
    "DEFAULT_POLICY",
    "auth",
    "check_target",
    "list_feeds",
    "feed_detail",
    "list_blogs",
    "blog_detail",
    "probe",
]

Caller = Callable[[str, dict], tuple[bool, Any, str]]

HOST = "https://user.qzone.qq.com"
TAOTAO = f"{HOST}/proxy/domain/taotao.qq.com/cgi-bin"

#: Blog (日志) cgi lives on b.qzone.qq.com behind the /proxy/domain/ reverse
#: proxy. Community tools call it through both proxy entrances; we try the
#: same one as the rest of this module first, then fall back to the h5 one.
BLOG = f"{HOST}/proxy/domain/b.qzone.qq.com/cgi-bin/blognew"
BLOG_ALT = "https://h5.qzone.qq.com/proxy/domain/b.qzone.qq.com/cgi-bin/blognew"
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

#: Same idea for blog list entries. Verified live 2026-10-31 against a real
#: account: the publish time field is ``pubTime`` (capital T, a
#: ``"2026-10-31 18:44"`` formatted string, **not** unix seconds), and the
#: permission posture lives in ``ar`` / ``arUins`` / ``artype``.
_BLOG_FIELDS = ("blogId", "title", "pubTime", "pubtime", "createTime", "cate",
                "cateHex", "commentNum", "readNum", "secret", "isPub", "blogType",
                "ar", "arUins", "artype")


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


def _request_json_mirrored(paths: str, cookie: str, *, timeout: float = 20.0) -> dict:
    """Call a blognew endpoint through each proxy entrance in turn.

    ``paths`` is the query string already (``/get_abs?uin=...`` style is built
    by the callers as ``f"/get_abs?..."``). ``user.qzone.qq.com`` first, then
    ``h5.qzone.qq.com`` — whichever answers ``code == 0`` wins.
    """
    last: QzoneError | None = None
    for base in (BLOG, BLOG_ALT):
        try:
            return _request_json(f"{base}{paths}", cookie, timeout=timeout)
        except QzoneError as exc:
            last = exc
    raise last or QzoneError("all blog endpoints failed")


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


class _BlogHTML(HTMLParser):
    """Pull plain text and image URLs out of a blog body HTML.

    Two input shapes exist:
    * a full detail page, where the body lives inside
      ``<div id="blogDetailDiv">`` (use ``root_id="blogDetailDiv"``);
    * a bare rich-text fragment from the JSON detail field (use
      ``root_id=None`` and everything is captured).

    Regex cannot count nested ``<div>``s, so depth tracking it is.
    """

    _BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"}
    _VOID = {"br", "hr"}

    def __init__(self, root_id: str | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.images: list[str] = []
        self.root_id = root_id
        self._depth = 0          # nesting depth inside the root div
        self._capturing = root_id is None
        self._skip = 0           # inside <script>/<style>

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if (self.root_id and tag == "div" and not self._capturing
                and a.get("id") == self.root_id):
            self._capturing = True
            self._depth = 1
            return
        if not self._capturing:
            return
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "div":
            self._depth += 1
        elif tag in self._VOID:
            self.text.append("\n")
        elif tag in self._BLOCK:
            self.text.append("\n")
        elif tag == "img":
            src = a.get("src") or ""
            if src and not src.startswith("data:"):
                self.images.append(src)

    def handle_endtag(self, tag: str) -> None:
        if not self._capturing:
            return
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag == "div":
            self._depth -= 1
            if self._depth <= 0:
                self._capturing = False

    def handle_data(self, data: str) -> None:
        if self._capturing and not self._skip and data.strip():
            self.text.append(data)

    def result(self) -> tuple[str, list[str]]:
        raw = "".join(self.text)
        lines = [ln.strip() for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln), self.images


def _slim_blog_comment(c: Any) -> dict | None:
    """Map a blog comment to a slim dict. Field names verified live 2026-10-31:
    ``content`` / ``postTime`` (unix seconds) / ``poster.{id,name}``. Older
    community-documented aliases (U/uin/nick/pubtime) kept as fallbacks."""
    if not isinstance(c, dict):
        return None
    poster = c.get("poster") if isinstance(c.get("poster"), dict) else {}
    return {
        "name": poster.get("name") or c.get("U") or c.get("name") or c.get("nick"),
        "uin": poster.get("id") or c.get("uin") or c.get("ownerUin"),
        "content": c.get("content") or c.get("Content"),
        "created_time": c.get("postTime") or c.get("pubtime") or c.get("createTime"),
    }


def list_blogs(call: Caller, *, target: str = "", pos: int = 0, num: int = 10,
               policy: dict | None = None) -> dict:
    """Read a user's blog (日志) list — the long-form posts, not 说说.

    Endpoint: ``blognew/get_abs``. Note the parameter split that differs from
    the taotao family: ``uin`` is the *visitor* (the logged-in account),
    ``hostUin`` is the space owner whose blogs are listed.

    Returns ``{"uin", "target", "pos", "total", "items"}``; items keep the
    ``_BLOG_FIELDS`` whitelist (``blogId`` is what ``blog_detail`` wants).
    Pagination mirrors ``list_feeds``: call again with ``pos = pos + num``.
    """
    uin, bkn, cookie = auth(call)
    merged = {**DEFAULT_POLICY, **(policy or {})}
    check_target(merged, target, uin)

    cap = max(1, int(merged.get("max_items") or 20))
    num = max(1, min(int(num or cap), cap))
    pos = max(0, int(pos or 0))
    host = str(target or uin)

    query = (f"/get_abs?uin={uin}&hostUin={host}&blogType=0&reqInfo=1"
             f"&pos={pos}&num={num}&sortType=0&ref=qzone"
             f"&inCharset=utf-8&outCharset=utf-8&format=jsonp&g_tk={bkn}")
    data = _request_json_mirrored(query, cookie)

    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    raw = payload.get("list") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raw = []

    items = []
    for entry in raw[:num]:
        if not isinstance(entry, dict):
            continue
        items.append({k: entry[k] for k in _BLOG_FIELDS if k in entry})

    total = None
    if isinstance(payload, dict):
        for key in ("totalNum", "total", "cnt"):
            if payload.get(key) is not None:
                total = payload.get(key)
                break

    return {"uin": uin, "target": host, "pos": pos, "total": total, "items": items}


def blog_detail(call: Caller, blog_id: str, *, target: str = "",
                policy: dict | None = None, max_comments: int = 20) -> dict:
    """Read one blog post: body (text + images) and its comments.

    Two cgi calls are made:
    * ``blognew/blog_output_data`` for the body. It may answer either JSON
      (body inside ``data.html`` / ``data.content``) or a full HTML page with
      the body in ``<div id="blogDetailDiv">`` — both shapes are handled.
    * ``blognew/get_comment_list`` for comments (``topicId`` is
      ``"<hostUin>_<blogId>"``); the comment field names are community-
      documented and deliberately parsed tolerantly.

    The response body is returned as ``text`` (plain text, tags stripped,
    block elements joined with newlines) plus ``images`` (URLs; a bare GET
    of them still needs a ``Referer`` like the 说说 pictures do). ``fragment``
    carries the raw rich-text HTML when it was recoverable.
    """
    blog_id = str(blog_id or "").strip()
    if not blog_id:
        raise QzoneError("blog_id is required")
    uin, bkn, cookie = auth(call)
    merged = {**DEFAULT_POLICY, **(policy or {})}
    check_target(merged, target, uin)

    host = str(target or uin)

    body_query = (f"/blog_output_data?uin={host}&blogid={blog_id}&numperpage=20"
                  f"&inCharset=utf-8&outCharset=utf-8&ref=qzone&format=jsonp"
                  f"&g_tk={bkn}")
    fragment, text, images = "", "", []
    last: QzoneError | None = None
    for base in (BLOG, BLOG_ALT):
        try:
            page = _request_text(f"{base}{body_query}", cookie)
        except QzoneError as exc:
            last = exc
            continue
        # Shape 1: JSON(P) with the rich-text body in a string field.
        try:
            data = _loads_lenient(_strip_jsonp(page))
        except QzoneError:
            data = None
        if isinstance(data, dict) and data.get("code") in (0, None, "0"):
            d = data.get("data") if isinstance(data.get("data"), dict) else data
            candidate = ""
            if isinstance(d, dict):
                candidate = (str(d.get("html") or "") or str(d.get("content") or "")
                             or str(d.get("blogContent") or "")
                             or str(d.get("htmlContent") or ""))
            if candidate:
                fragment = candidate
                sub = _BlogHTML(root_id=None)      # bare rich-text fragment
                try:
                    sub.feed(candidate)
                except Exception:
                    pass
                text, images = sub.result()
            break
        # Shape 2: full HTML page. A *successful* detail page carries the body
        # inside <div id="blogDetailDiv">; an error page (bad id, no permission,
        # deleted post) has neither that div nor a code field — detect it by the
        # errorTitle marker and treat as upstream failure instead of empty text.
        if "errorTitle" in page[:2000] and "blogDetailDiv" not in page:
            last = QzoneError("upstream returned error page (bad blogid or no access)")
            continue
        sub = _BlogHTML(root_id="blogDetailDiv")
        try:
            sub.feed(page)
        except Exception:
            pass
        text, images = sub.result()
        break

    if not (text or images or fragment):
        raise last or QzoneError(
            "blog body is empty (bad blogid, no access, or upstream change)")

    comments = []
    comment_count = None
    try:
        cmt_query = (f"/get_comment_list?uin={host}&topicId={host}_{blog_id}"
                     f"&start=0&num={max(1, int(max_comments))}"
                     f"&inCharset=utf-8&outCharset=utf-8&format=jsonp&g_tk={bkn}")
        cdata = _request_json_mirrored(cmt_query, cookie)
        cpayload = cdata.get("data") if isinstance(cdata.get("data"), dict) else {}
        craw = cpayload.get("comments") if isinstance(cpayload, dict) else None
        if isinstance(craw, list):
            comments = [c for c in (_slim_blog_comment(x) for x in craw[:max(1, int(max_comments))])
                        if c]
        # 实测（2026-10-31）：字段名是 "total"，不是 get_abs 的 "totalNum"
        if isinstance(cpayload, dict):
            for key in ("total", "totalNum"):
                if cpayload.get(key) is not None:
                    comment_count = cpayload.get(key)
                    break
    except QzoneError:
        pass                                  # comments are best-effort

    return {
        "blog_id": blog_id,
        "target": host,
        "text": text,
        "fragment": fragment,
        "images": images,
        "comment_count": comment_count,
        "comments": comments,
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
