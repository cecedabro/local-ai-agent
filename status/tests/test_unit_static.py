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

    def test_favicon(self) -> None:
        page = self.req("GET", "/", token=None)[2].decode()
        self.assertRegex(page, r'<link rel="icon" type="image/svg\+xml" href="/static/icon\.svg\?v=[0-9a-f]{10}">')
        code, headers, raw = self.req("GET", "/static/icon.svg", token=None)
        self.assertEqual(code, 200)
        self.assertEqual({k.lower(): v for k, v in headers.items()}["content-type"], "image/svg+xml")
        self.assertIn(b"<svg", raw)

    def test_runs_actions_column(self) -> None:
        page = self.req("GET", "/", token=None)[2].decode()
        self.assertNotIn("<th>Pause</th>", page)

    def test_start_loop_form_submits(self) -> None:
        # The Start loop button silently did nothing: max_hours had min 0.1 / step 0.5, so its default
        # 24 was off-step and the browser blocked the submit (the field sits in a collapsed <details>).
        import re
        page = self.req("GET", "/", token=None)[2].decode()
        self.assertRegex(page, r'<form id="add"[^>]* novalidate')
        tag = re.search(r'<input name="max_hours"[^>]*>', page).group(0)
        lo, step, val = (float(re.search(f'{k}="([0-9.]+)"', tag).group(1)) for k in ("min", "step", "value"))
        self.assertAlmostEqual((val - lo) / step, round((val - lo) / step))

    def test_chart_gradient_outside_live(self) -> None:
        page = self.req("GET", "/", token=None)[2].decode()
        self.assertLess(page.index('id="bar-fill"'), page.index('<main id="live">'))


class ThemeTest(Base):
    def test_switch_on_both_pages(self) -> None:
        for path in ("/", "/chat"):
            page = self.req("GET", path, token=None)[2].decode()
            self.assertIn('class="seg three icons theme"', page)
            self.assertLess(page.index("agent-status-theme"), page.index('rel="stylesheet"'))  # before paint
