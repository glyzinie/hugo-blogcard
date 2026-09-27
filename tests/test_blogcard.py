"""End-to-end checks for the blogcard shortcode.

Run with: uv run --no-cache --no-project python tests/test_blogcard.py
Set HUGO_BIN to use a different Hugo executable.
"""

from collections import Counter
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
from urllib.parse import urlparse
import zlib


REPO = Path(__file__).resolve().parents[1]
MODULE = "github.com/glyzinie/hugo-blogcard"


def png(width=400, height=200):
    """A small, valid image that Hugo can resize without another dependency."""

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    pixels = (b"\0" + b"\x60\x90\xc0" * width) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def document(head, opener="<head>"):
    return f"<!doctype html><html>{opener}{head}</head><body>Fixture</body></html>".encode()


class FixtureServer:
    def __init__(self):
        self.routes = {}
        self.requests = Counter()
        self.lock = threading.Lock()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                with fixture.lock:
                    fixture.requests[self.path] += 1
                status, mime, body = fixture.routes.get(
                    self.path, (404, "text/plain", b"Not found")
                )
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def add(self, path, body, mime="text/html", status=200):
        if isinstance(body, str):
            body = body.encode()
        self.routes[path] = status, mime, body

    def snapshot(self):
        with self.lock:
            return self.requests.copy()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class CardParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self.current = None
        self.in_title = False
        self.fallback_tag = None
        self.fallback_depth = 0
        self.stylesheets = []
        self.scripts = []
        self.inline_styles = []
        self.in_paragraph = False
        self.blocks_in_paragraph = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if tag == "p":
            self.in_paragraph = True
        elif self.in_paragraph and tag in {
            "div", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "ul", "ol", "table"
        }:
            self.blocks_in_paragraph.append(tag)
        if "style" in attrs:
            self.inline_styles.append((tag, attrs["style"]))
        if tag == "link" and "stylesheet" in attrs.get("rel", "").split():
            self.stylesheets.append(attrs.get("href", ""))
        if tag == "script":
            self.scripts.append(attrs)
        if tag == "a" and "blogcard" in classes:
            self.current = {"href": attrs.get("href", ""), "text": "", "title": "", "images": []}
            self.cards.append(self.current)
        elif tag in ("div", "span") and "blogcard" in classes and self.current is None:
            self.current = {"href": "", "text": "", "title": "", "images": []}
            self.cards.append(self.current)
            self.fallback_tag = tag
            self.fallback_depth = 1
        elif self.current is not None:
            if tag == self.fallback_tag:
                self.fallback_depth += 1
            if tag == "img":
                self.current["images"].append(attrs)
            if "blogcard__title" in classes:
                self.in_title = True

    def handle_endtag(self, tag):
        if tag == "p":
            self.in_paragraph = False
        if tag == "a":
            self.current = None
            self.in_title = False
        elif tag == self.fallback_tag and self.fallback_depth:
            self.fallback_depth -= 1
            if self.fallback_depth == 0:
                self.current = None
                self.in_title = False
                self.fallback_tag = None
        elif tag in ("h1", "h2", "h3", "strong"):
            self.in_title = False

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data
            if self.in_title:
                self.current["title"] += data


class BlogcardIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hugo = os.environ.get("HUGO_BIN") or shutil.which("hugo")
        if not cls.hugo:
            raise unittest.SkipTest("Hugo is not installed; set HUGO_BIN")
        cls.temp = tempfile.TemporaryDirectory(prefix="hugo-blogcard-test-")
        cls.root = Path(cls.temp.name)
        cls.fixture = FixtureServer()
        cls.addClassCleanup(cls.fixture.close)
        cls.addClassCleanup(cls.temp.cleanup)
        cls._populate_fixtures()

    @classmethod
    def _populate_fixtures(cls):
        f = cls.fixture
        image = png()
        f.add("/media/cover.png", image, "image/png")
        f.add("/media/repeated.png", image, "image/png")
        f.add("/media/vector.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>', "image/svg+xml")
        f.add("/media/corrupt.png", b"this is not a PNG", "image/png")
        f.add("/assets/favicon.png", image, "image/png")

        f.add(
            "/posts/meta",
            document(
                "<META PROPERTY='OG:TITLE' CONTENT='A &amp; B'>"
                "<meta NAME='description' CONTENT='Safe &lt;script&gt;alert(1)&lt;/script&gt; &amp; sound'>"
                "<meta property='og:image' content='../media/cover.png'>"
                "<link REL='icon' HREF='../assets/favicon.png'>"
                "<link rel='manifest' href='../manifests/ignored.webmanifest'>",
                '<head prefix="og: https://ogp.me/ns#">',
            ),
        )
        f.add("/posts/title-only", document("<title>Fallback &amp; title</title>"))
        f.add(
            "/posts/root-image",
            document("<meta property='og:title' content='Root image'>"
                     "<meta property='og:image' content='/media/cover.png'>"),
        )
        f.add(
            "/posts/repeated",
            document(
                "<meta property='og:title' content='Repeated card'>"
                "<meta property='og:image' content='/media/repeated.png'>"
                "<link rel='icon' href='/assets/favicon.png'>"
            ),
        )

        f.add(
            "/pages/manifest",
            document(
                "<meta property='og:title' content='Manifest'>"
                "<link rel='manifest' href='../manifests/site.webmanifest'>"
            ),
        )
        f.add(
            "/manifests/site.webmanifest",
            json.dumps({"icons": [
                {"src": "icons/small.png", "sizes": "32x32"},
                {"src": "icons/large.png", "sizes": "any 192x192"},
            ]}),
            "application/manifest+json; charset=utf-8",
        )
        f.add(
            "/pages/bad-json",
            document("<meta property='og:title' content='Bad JSON'>"
                     "<link rel='manifest' href='../manifests/bad.webmanifest'>"),
        )
        f.add("/manifests/bad.webmanifest", "{broken", "application/manifest+json")
        f.add(
            "/pages/weird-manifest",
            document("<meta property='og:title' content='Odd manifest'>"
                     "<link rel='manifest' href='../manifests/odd.webmanifest'>"),
        )
        f.add("/manifests/odd.webmanifest", '{"icons":{"src":"icon.png"}}', "application/manifest+json")

        f.add("/forbidden", "Forbidden", "text/plain", 403)
        f.add("/posts/no-head", "<html><body>No head element</body></html>")
        f.add("/posts/no-image", document("<meta property='og:title' content='No image'>"))
        f.add(
            "/posts/svg-image",
            document("<meta property='og:title' content='SVG image'>"
                     "<meta property='og:image' content='/media/vector.svg'>"),
        )
        f.add(
            "/posts/corrupt-image",
            document("<meta property='og:title' content='Corrupt image'>"
                     "<meta property='og:image' content='/media/corrupt.png'>"),
        )
        f.add(
            "/posts/missing-image",
            document("<meta property='og:title' content='Missing image'>"
                     "<meta property='og:image' content='/media/missing.png'>"),
        )

    def _site(self, name, urls, fallback=None, allow_manifest=False):
        """Create a fresh site, import this checkout, then return its generated HTML."""
        site = self.root / name
        (site / "content").mkdir(parents=True)
        (site / "layouts/_default").mkdir(parents=True)
        (site / "assets").mkdir(parents=True)
        (site / "layouts/_default/single.html").write_text(
            "<!doctype html><html><head></head><body>{{ .Content }}</body></html>"
        )
        (site / "assets/fallback.png").write_bytes(png())
        (site / "go.mod").write_text(
            f"module example.org/blogcard-test\n\ngo 1.22.2\n\n"
            f"require {MODULE} v0.0.0\n\nreplace {MODULE} => {REPO}\n"
        )
        config = (
            "baseURL = 'https://cards.example.test/'\n"
            "disableKinds = ['home', 'section', 'taxonomy', 'term', 'RSS', 'sitemap']\n"
            "[module]\n[[module.imports]]\n"
            f"path = '{MODULE}'\n"
            "[security.http]\nurls = ['^http://127[.]0[.]0[.]1:']\n"
        )
        if allow_manifest:
            config += r"mediaTypes = ['(?i)^application/manifest\+json(?:\s*;|$)']" + "\n"
        config += "[params]\nimageQuality = 67\n"
        if fallback is not None:
            config += f"defaultNoimage = '{fallback}'\n"
        (site / "hugo.toml").write_text(config)
        content = "+++\ntitle = 'Card test'\n+++\n" + "\n".join(
            '{{< blogcard "' + url + '" >}}' for url in urls
        ) + "\n"
        (site / "content/card.md").write_text(content)
        before = self.fixture.snapshot()
        result = self._run_hugo(site)
        self.assertEqual(
            result.returncode,
            0,
            f"Hugo failed for {name}:\n{result.stdout}\n{result.stderr}",
        )
        page = site / "public/card/index.html"
        self.assertTrue(page.is_file(), f"No generated card page for {name}")
        markup = page.read_text()
        parsed = CardParser()
        parsed.feed(markup)
        return site, markup, parsed, self.fixture.snapshot() - before

    def _run_hugo(self, site):
        env = os.environ.copy()
        env.update({
            "GOWORK": "off",
            "GOPATH": str(self.root / "go"),
            "GOCACHE": str(self.root / "go-cache"),
            "HUGO_RESOURCEDIR": str(site / "resources"),
        })
        return subprocess.run(
            [self.hugo, "--source", str(site), "--cacheDir", str(site / "cache"),
             "--destination", str(site / "public"), "--noBuildLock"],
            cwd=site,
            env=env,
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )

    def _card(self, parsed, url):
        cards = [card for card in parsed.cards if card["href"] == url]
        self.assertTrue(cards, f"No card links to {url}; found {parsed.cards!r}")
        return cards[0]

    def _image(self, card, class_name):
        return next((img for img in card["images"] if class_name in img.get("class", "").split()), None)

    def _valid_thumbnail_or_omitted(self, card):
        thumbnail = self._image(card, "blogcard__thumbnail")
        if thumbnail:
            self.assertTrue(thumbnail.get("src"))
            self.assertEqual(thumbnail.get("width"), "100")
            self.assertEqual(thumbnail.get("height"), "100")
            self.assertEqual(thumbnail.get("loading"), "lazy")
            self.assertEqual(thumbnail.get("decoding"), "async")
        return thumbnail

    def _generated_thumbnail(self, site, card):
        thumbnail = self._valid_thumbnail_or_omitted(card)
        self.assertIsNotNone(thumbnail, f"No thumbnail for {card['href']}")
        path = urlparse(thumbnail["src"]).path.lstrip("/")
        self.assertEqual(Path(path).suffix, ".webp")
        self.assertTrue((site / "public" / path).is_file(), f"Missing generated image: {path}")
        return thumbnail

    def test_metadata_escaping_and_relative_urls(self):
        f = self.fixture
        site, markup, parsed, requests = self._site(
            "metadata", [f.base + "/posts/meta", f.base + "/posts/title-only", f.base + "/posts/root-image"],
            fallback="fallback.png",
        )
        card = self._card(parsed, f.base + "/posts/meta")
        self.assertEqual(card["title"], "A & B")
        self.assertIn("Safe", card["text"])
        self.assertIn("& sound", card["text"])
        self.assertIn("A &amp; B", markup)
        self.assertNotIn("A &amp;amp; B", markup)
        self.assertNotIn("<script>alert(1)</script>", markup)
        self.assertEqual(parsed.scripts, [])
        icon = self._image(card, "blogcard__favicon")
        self.assertIsNotNone(icon)
        self.assertEqual(icon["src"], f.base + "/assets/favicon.png")
        self.assertEqual((icon.get("width"), icon.get("height")), ("16", "16"))
        self.assertEqual(self._card(parsed, f.base + "/posts/title-only")["title"], "Fallback & title")
        self._generated_thumbnail(site, card)
        self._generated_thumbnail(site, self._card(parsed, f.base + "/posts/root-image"))
        self.assertEqual(requests["/media/cover.png"], 1)
        self.assertEqual(requests["/manifests/ignored.webmanifest"], 0)

    def test_manifest_variants(self):
        f = self.fixture
        _site, _markup, parsed, requests = self._site(
            "manifests", [f.base + "/pages/manifest", f.base + "/pages/bad-json", f.base + "/pages/weird-manifest"],
            allow_manifest=True,
        )
        card = self._card(parsed, f.base + "/pages/manifest")
        icon = self._image(card, "blogcard__favicon")
        self.assertIsNotNone(icon)
        self.assertIn(icon["src"], {
            f.base + "/manifests/icons/small.png",
            f.base + "/manifests/icons/large.png",
        })
        self.assertEqual(requests["/manifests/site.webmanifest"], 1)
        for endpoint in ("bad-json", "weird-manifest"):
            card = self._card(parsed, f.base + "/pages/" + endpoint)
            icon = self._image(card, "blogcard__favicon")
            if icon:
                self.assertTrue(icon.get("src"))
            self.assertEqual(requests[f"/manifests/{'bad' if endpoint == 'bad-json' else 'odd'}.webmanifest"], 1)

        _site, _markup, parsed, requests = self._site(
            "manifest-default-policy", [f.base + "/pages/manifest"]
        )
        card = self._card(parsed, f.base + "/pages/manifest")
        icon = self._image(card, "blogcard__favicon")
        self.assertIsNotNone(icon)
        self.assertEqual(icon["src"], f.base + "/favicon.ico")
        self.assertEqual(requests["/manifests/site.webmanifest"], 1)

    def test_remote_failures_and_image_fallback(self):
        f = self.fixture
        paths = ["/missing-page", "/forbidden", "/posts/no-head", "/posts/no-image", "/posts/svg-image",
                 "/posts/corrupt-image", "/posts/missing-image"]
        site, _markup, parsed, requests = self._site(
            "failures", [f.base + path for path in paths] + ["https://", "javascript:alert(1)", "file:///etc/passwd"],
            fallback="fallback.png",
        )
        for path in paths:
            card = self._card(parsed, f.base + path)
            self.assertTrue(card["text"].strip(), path)
            self._generated_thumbnail(site, card)
        self.assertEqual(requests["/missing-page"], 1)
        self.assertEqual(requests["/forbidden"], 1)
        self.assertEqual(requests["/media/missing.png"], 1)
        invalid_cards = [card for card in parsed.cards if not card["href"]]
        self.assertEqual(len(invalid_cards), 3)
        self.assertTrue(all(card["text"].strip() for card in invalid_cards))

    def test_missing_fallback_config_or_asset(self):
        f = self.fixture
        for fallback, name in ((None, "unset-fallback"), ("not-present.png", "unknown-fallback")):
            with self.subTest(fallback=fallback):
                _site, _markup, parsed, _requests = self._site(
                    name, [f.base + "/posts/no-image"], fallback=fallback
                )
                card = self._card(parsed, f.base + "/posts/no-image")
                self.assertIsNone(self._image(card, "blogcard__thumbnail"))

    def test_repeated_card_uses_remote_cache_and_shared_stylesheet(self):
        f = self.fixture
        url = f.base + "/posts/repeated"
        site, _markup, parsed, requests = self._site("repeated", [url] * 3)
        self.assertEqual(len(parsed.cards), 3)
        self.assertEqual(requests["/posts/repeated"], 1)
        self.assertEqual(requests["/media/repeated.png"], 1)
        self.assertTrue(parsed.stylesheets)
        self.assertEqual(len(set(parsed.stylesheets)), 1)
        self.assertEqual(parsed.inline_styles, [])
        self.assertEqual(parsed.blocks_in_paragraph, [])
        css_path = urlparse(parsed.stylesheets[0]).path.lstrip("/")
        css_file = site / "public" / css_path
        self.assertTrue(css_file.is_file(), f"Missing stylesheet {css_file}")
        css = css_file.read_text()
        for selector in (".blogcard", ".blogcard__title", ".blogcard__thumbnail", ".blogcard__favicon"):
            self.assertIn(selector, css)
        for card in parsed.cards:
            self._generated_thumbnail(site, card)
        before = f.snapshot()
        rebuilt = self._run_hugo(site)
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        self.assertEqual(f.snapshot() - before, Counter())


if __name__ == "__main__":
    unittest.main(verbosity=2)
