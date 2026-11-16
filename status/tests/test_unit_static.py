"""Static files and page shell (SPEC §0, §11 chat). Run: make check"""
from __future__ import annotations

from test_contract import Base


class StaticTest(Base):
    def test_stylesheet_served_and_linked(self) -> None:
        code, headers, raw = self.req("GET", "/static/style.css", token=None)
        self.assertEqual(code, 200)
        self.assertTrue({k.lower(): v for k, v in headers.items()}["content-type"].startswith("text/css"))
        self.assertIn(b"--primary", raw)
        for path in ("/", "/chat"):
            page = self.req("GET", path, token=None)[2].decode()
            self.assertRegex(page, r'<link rel="stylesheet" href="/static/style\.css\?v=[0-9a-f]{10}">')

    def test_only_known_files(self) -> None:
        for path in ("/static/", "/static/nope.css", "/static/../app.py", "/static/%2e%2e/app.py"):
            self.assertEqual(self.req("GET", path, token=None)[0], 404, path)

    def test_chart_gradient_outside_live(self) -> None:
        page = self.req("GET", "/", token=None)[2].decode()
        self.assertLess(page.index('id="bar-fill"'), page.index('<main id="live">'))
