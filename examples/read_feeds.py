"""Read your own timeline and print a summary.

Setup
-----
    pip install --upgrade pip          # nothing else needed, stdlib only
    set ONEBOT_URL=http://127.0.0.1:3000
    set ONEBOT_TOKEN=your-token        # leave empty if the endpoint has no token
    python examples/read_feeds.py

    # read someone allowed by policy instead of yourself
    python examples/read_feeds.py 123456789

Enable an HTTP server in NapCat first: WebUI -> 网络配置 -> HTTP 服务器 -> 启用.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import qzone                                    # noqa: E402
from onebot import OneBotHTTP                   # noqa: E402


def fmt_time(value) -> str:
    """Qzone returns unix seconds; fall back to the raw value if it ever changes."""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(value)))
    except (TypeError, ValueError, OSError):
        return str(value or "?")


def main() -> int:
    call = OneBotHTTP(os.getenv("ONEBOT_URL", "http://127.0.0.1:3000"),
                      os.getenv("ONEBOT_TOKEN", ""))

    target = sys.argv[1] if len(sys.argv) > 1 else ""
    # Reading anyone other than yourself must be allowed explicitly.
    policy = {"scope": "any"} if target else None

    print("[probe]", qzone.probe(call))
    page = qzone.list_feeds(call, target=target, num=5, policy=policy)
    print(f"\ntotal={page['total']}  returned={len(page['items'])}")

    for post in page["items"]:
        text = (post.get("content") or "").replace("\n", " ")
        pics = len(post.get("pics") or [])
        print(f"  {fmt_time(post.get('created_time'))}  tid={post.get('tid')}")
        print(f"    secret={post.get('secret')}  pics={pics}  comments={post.get('cmtnum')}")
        print(f"    {text[:60]}{'...' if len(text) > 60 else ''}")

    if page["items"]:
        print("\nNext page:  pos =", page["pos"] + len(page["items"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
