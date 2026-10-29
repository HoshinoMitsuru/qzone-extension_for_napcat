"""Read one post together with its comments.

    set ONEBOT_URL=http://127.0.0.1:3000
    set ONEBOT_TOKEN=your-token
    python examples/feed_detail.py <tid> [target_uin]

Get a ``tid`` from ``examples/read_feeds.py``. One request returns content,
image URLs and the comment list — the standalone comment endpoint answers
HTTP 500, so do not split it.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import qzone                                    # noqa: E402
from onebot import OneBotHTTP                   # noqa: E402


def fmt_time(value) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(value)))
    except (TypeError, ValueError, OSError):
        return str(value or "?")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    tid = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else ""

    call = OneBotHTTP(os.getenv("ONEBOT_URL", "http://127.0.0.1:3000"),
                      os.getenv("ONEBOT_TOKEN", ""))
    policy = {"scope": "any"} if target else None

    post = qzone.feed_detail(call, tid, target=target, policy=policy)

    print(f"tid={post['tid']}  uin={post['target']}  secret={post['secret']}")
    print(f"created={fmt_time(post['created_time'])}  comments={post['comment_count']}")
    print(f"content ({len(post['content'])} chars):")
    print("  " + (post["content"][:400] or "(empty — image-only post)"))
    print(f"images: {len(post['pics'])}")
    for url in post["pics"][:5]:
        # Wikimedia-style hotlinking rules do not apply here, but a bare GET of a
        # Qzone image needs a Referer header or it is refused.
        print("  " + (url or "")[:90])
    print(f"comments returned: {len(post['comments'])}")
    for c in post["comments"][:5]:
        body = str(c.get("content") or "").replace("\n", " ")
        print(f"  [{c.get('name')}] {body[:50]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
