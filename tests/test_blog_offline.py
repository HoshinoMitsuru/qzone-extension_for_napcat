"""Offline tests for the blog (日志) additions in qzone.py.

Run:  python tests/test_blog_offline.py
No network, no NapCat — a fake Caller hands out fixed credentials and the
cgi endpoints are patched to serve canned responses. Covers response-shape
handling, which is exactly the part that breaks when upstream changes.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import qzone                                            # noqa: E402


COOKIE = ("uin=o123456; p_skey=abc; skey=def")
CRED = {"cookies": COOKIE, "bkn": "1234567"}


class FakeCaller:
    """OneBot-shaped caller: only get_cookies is needed by qzone.auth."""

    def __call__(self, action, params):
        if action == "get_cookies":
            return True, dict(CRED), ""
        return False, None, f"unexpected action {action}"


def patched(monkey: dict):
    """Return a decorator that swaps module-level request helpers."""
    def wrap(fn):
        def run(*args, **kwargs):
            saved = {k: getattr(qzone, k) for k in monkey}
            for k, v in monkey.items():
                setattr(qzone, k, v)
            try:
                return fn(*args, **kwargs)
            finally:
                for k, v in saved.items():
                    setattr(qzone, k, v)
        return run
    return wrap


def fetch_text(url, cookie, **kw):                    # pragma: no cover
    raise AssertionError(f"unexpected text request: {url}")


class TestBlogList(unittest.TestCase):

    @patched({"_request_json_mirrored": lambda q, c, **kw: {
        "code": 0,
        "data": {"totalNum": 2, "list": [
            {"blogId": "1698000001", "title": "第一篇", "pubtime": 1698000000,
             "cate": "个人日志", "commentNum": 3, "readNum": 12, "extra": "drop"},
            {"blogId": "1698000002", "title": "第二篇", "pubtime": 1698000100,
             "cate": "个人日志", "commentNum": 0, "readNum": 1, "extra": "drop"},
        ]}}})
    def test_list_blogs_whitelists_fields(self):
        res = qzone.list_blogs(FakeCaller())
        self.assertEqual(res["total"], 2)
        self.assertEqual(len(res["items"]), 2)
        first = res["items"][0]
        self.assertEqual(first["blogId"], "1698000001")
        self.assertEqual(first["title"], "第一篇")
        self.assertNotIn("extra", first)          # whitelist works
        self.assertEqual(res["target"], "123456")  # self by default

    @patched({"_request_json_mirrored": lambda q, c, **kw: {"code": 0, "data": {}}})
    def test_list_blogs_empty(self):
        res = qzone.list_blogs(FakeCaller())
        self.assertEqual(res["items"], [])
        self.assertIsNone(res["total"])

    def test_policy_still_applies(self):
        with self.assertRaises(qzone.QzoneError):
            qzone.list_blogs(FakeCaller(), target="999")   # scope=self


class TestBlogDetail(unittest.TestCase):

    DETAIL_JSON = ('_Callback({"code":0,"data":{"html":"<p>正文第一段</p>'
                   '<br><img src=\'https://img.example/1.png\'><p>第二段</p>"}})')

    @patched({
        "_request_text": lambda url, c, **kw: TestBlogDetail.DETAIL_JSON
            if "blog_output_data" in url else fetch_text(url, c, **kw),
        "_request_json_mirrored": lambda q, c, **kw: {
            "code": 0, "data": {"totalNum": 1, "comments": [
                {"U": "张三", "uin": 777, "content": "写得不错", "pubtime": "10-22"}]}},
    })
    def test_detail_json_shape(self):
        res = qzone.blog_detail(FakeCaller(), "1698000001")
        self.assertEqual(res["blog_id"], "1698000001")
        self.assertIn("正文第一段", res["text"])
        self.assertIn("第二段", res["text"])
        self.assertEqual(res["images"], ["https://img.example/1.png"])
        self.assertEqual(res["comments"][0]["name"], "张三")
        self.assertEqual(res["comments"][0]["content"], "写得不错")
        self.assertEqual(res["comment_count"], 1)

    DETAIL_HTML = """<html><body>
        <div id="sideBar">noise</div>
        <div id="blogDetailDiv"><p>HTML 形态正文</p>
          <img src="https://img.example/2.png">
          <div><span>嵌套段落</span></div></div>
        <div id="tail">after</div></body></html>"""

    @patched({
        "_request_text": lambda url, c, **kw: TestBlogDetail.DETAIL_HTML
            if "blog_output_data" in url else fetch_text(url, c, **kw),
        "_request_json_mirrored": lambda q, c, **kw: {"code": 0, "data": {"comments": []}},
    })
    def test_detail_html_shape(self):
        res = qzone.blog_detail(FakeCaller(), "1698000002")
        self.assertIn("HTML 形态正文", res["text"])
        self.assertIn("嵌套段落", res["text"])
        self.assertNotIn("noise", res["text"])     # stays inside the div
        self.assertNotIn("after", res["text"])
        self.assertEqual(res["images"], ["https://img.example/2.png"])

    @patched({"_request_text": lambda url, c, **kw: (_ for _ in ()).throw(
        qzone.QzoneError("request failed"))})
    def test_detail_upstream_down_raises(self):
        with self.assertRaises(qzone.QzoneError):
            qzone.blog_detail(FakeCaller(), "1")

    def test_blog_id_required(self):
        with self.assertRaises(qzone.QzoneError):
            qzone.blog_detail(FakeCaller(), "  ")

    def test_policy_still_applies(self):
        with self.assertRaises(qzone.QzoneError):
            qzone.blog_detail(FakeCaller(), "1", target="999")


class TestUrlParser(unittest.TestCase):
    """The mirrored endpoints must carry the documented parameter split."""

    @patched({"_request_json_mirrored": lambda q, c, **kw: {"code": 0, "data": {"list": []}}})
    def test_list_query_params(self):
        seen = {}
        def spy(q, c, **kw):
            seen["q"] = q
            return {"code": 0, "data": {"list": []}}
        saved = qzone._request_json_mirrored
        qzone._request_json_mirrored = spy
        try:
            # target == self (empty) keeps DEFAULT_POLICY happy; the uin/hostUin
            # split is what this test actually pins down.
            qzone.list_blogs(FakeCaller(), pos=5, num=3)
        finally:
            qzone._request_json_mirrored = saved
        q = seen["q"]
        self.assertIn("/get_abs?", q)
        self.assertIn("uin=123456", q)             # visitor = self
        self.assertIn("hostUin=123456", q)         # owner = self (empty target)
        self.assertIn("pos=5", q)
        self.assertIn("num=3", q)
        self.assertIn("g_tk=1234567", q)


if __name__ == "__main__":
    unittest.main(verbosity=2)
