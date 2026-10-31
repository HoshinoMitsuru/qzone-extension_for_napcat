"""Read a user's blog list (日志, long-form posts) and print a summary.

    set ONEBOT_URL=http://127.0.0.1:3000
    set ONEBOT_TOKEN=your-token
    python examples/read_blogs.py               # your own blogs
    python examples/read_blogs.py 123456789     # someone allowed by policy
    python examples/read_blogs.py --detail <blog_id>

Blogs are a different content family from 说说 (taotao): the list comes from
``blognew/get_abs`` and the body from ``blognew/blog_output_data``, which may
answer either JSON or a full HTML page — both are handled. A blog list entry
carries ``blogId``, not ``tid``.
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
    args = [a for a in sys.argv[1:]]
    detail = None
    if "--detail" in args:
        i = args.index("--detail")
        if i + 1 >= len(args):
            print(__doc__)
            return 2
        detail = args[i + 1]
        del args[i:i + 2]

    target = args[0] if args else ""
    # Reading anyone other than yourself must be allowed explicitly.
    policy = {"scope": "any"} if target else None

    call = OneBotHTTP(os.getenv("ONEBOT_URL", "http://127.0.0.1:3000"),
                      os.getenv("ONEBOT_TOKEN", ""))

    if detail:
        post = qzone.blog_detail(call, detail, target=target, policy=policy)
        print(f"blog_id={post['blog_id']}  target={post['target']}")
        print(f"text ({len(post['text'])} chars):")
        print("  " + post["text"][:400].replace("\n", "\n  "))
        print(f"images: {len(post['images'])}")
        for url in post["images"][:5]:
            print("  " + url[:90])
        print(f"comments returned: {len(post['comments'])}")
        for c in post["comments"][:5]:
            body = str(c.get("content") or "").replace("\n", " ")
            print(f"  [{c.get('name')}] {body[:50]}")
        return 0

    page = qzone.list_blogs(call, target=target, num=10, policy=policy)
    print(f"total={page['total']}  returned={len(page['items'])}")
    for blog in page["items"]:
        title = str(blog.get("title") or "(untitled)")
        print(f"  {fmt_time(blog.get('pubtime'))}  blogId={blog.get('blogId')}")
        print(f"    cate={blog.get('cate')}  comments={blog.get('commentNum')}"
              f"  reads={blog.get('readNum')}")
        print(f"    {title[:60]}")
    if page["items"]:
        print("\nNext page:  pos =", page["pos"] + len(page["items"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
