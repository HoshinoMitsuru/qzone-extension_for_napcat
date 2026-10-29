"""Publish one post, verify it, then delete it — the safest way to prove the write path.

    # 1) dry run (default): shows exactly what would be posted
    python examples/post_and_delete.py

    # 2) actually do it, private to yourself
    python examples/post_and_delete.py --yes

    # 3) with images (file:// http(s):// base64:// are all accepted)
    python examples/post_and_delete.py --yes --image ./demo.png --image https://example.com/a.jpg

Safety choices baked in:

* ``ugc_right=64`` (visible only to yourself) unless you pass ``--public``.
* ``--public`` still posts as "visible to friends" (``ugc_right=4``), never to
  everyone: a test post should not reach strangers.
* The post is deleted at the end. Note that uploaded images are NOT removed —
  they stay in the auto-created album "说说和日志相册". That is QQ behaviour, not
  something this tool can undo (a separate album API would be required).
* Without ``--yes`` nothing leaves the machine.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import qzone                                    # noqa: E402
from onebot import OneBotHTTP                   # noqa: E402

UGC_SELF = 64        # only me
UGC_FRIENDS = 4      # friends only — the narrowest setting others can see


def main() -> int:
    parser = argparse.ArgumentParser(description="post a test entry, verify, delete")
    parser.add_argument("--yes", action="store_true",
                        help="actually publish (otherwise only print the plan)")
    parser.add_argument("--public", action="store_true",
                        help="visible to friends instead of only yourself")
    parser.add_argument("--image", action="append", default=[],
                        help="image reference, repeatable")
    parser.add_argument("--keep", action="store_true",
                        help="do not delete the post afterwards")
    args = parser.parse_args()

    call = OneBotHTTP(os.getenv("ONEBOT_URL", "http://127.0.0.1:3000"),
                      os.getenv("ONEBOT_TOKEN", ""))

    ugc_right = UGC_FRIENDS if args.public else UGC_SELF
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    content = (f"[api self-test] {stamp} posted by napcat-qzone-toolkit, "
               f"safe to delete")

    print("plan:")
    print(f"  content   : {content}")
    print(f"  images    : {len(args.image)}")
    print(f"  ugc_right : {ugc_right} "
          f"({'friends only' if args.public else 'only me'})")
    print(f"  delete    : {'no (--keep)' if args.keep else 'yes'}")

    if not args.yes:
        print("\ndry run — re-run with --yes to publish")
        return 0

    before = qzone.list_feeds(call, num=1)
    print(f"\nbaseline total={before['total']}")

    params: dict = {"content": content, "ugc_right": ugc_right}
    if args.image:
        params["images"] = args.image

    ok, data, err = call("send_qzone_msg", params)
    if not ok:
        print(f"publish FAILED: {err}")
        return 1
    tid = (data or {}).get("tid")
    print(f"published tid={tid}")
    if not tid:
        print("no tid returned — cannot verify or delete, stopping")
        return 1

    time.sleep(3)
    after = qzone.list_feeds(call, num=3)
    seen = tid in [x.get("tid") for x in after["items"]]
    print(f"visible in timeline: {seen}  (total {before['total']} -> {after['total']})")

    if args.keep:
        print("kept as requested; delete manually with action delete_qzone_msg")
        return 0 if seen else 1

    ok, _data, err = call("delete_qzone_msg", {"tid": tid})
    print(f"delete: ok={ok} {err}".rstrip())
    time.sleep(3)
    final = qzone.list_feeds(call, num=3)
    gone = tid not in [x.get("tid") for x in final["items"]]
    print(f"removed: {gone}  (total back to {final['total']})")
    return 0 if (seen and ok and gone) else 1


if __name__ == "__main__":
    raise SystemExit(main())
