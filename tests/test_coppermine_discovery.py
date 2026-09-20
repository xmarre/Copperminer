import unittest
from unittest.mock import Mock, patch

import requests

import gallery_ripper as gr


class CoppermineDiscoveryTests(unittest.TestCase):
    @staticmethod
    def _http_error(status, url):
        response = requests.Response()
        response.status_code = status
        response.url = url
        return requests.HTTPError(f"{status} for {url}", response=response)

    def test_fetch_cache_records_effective_response_url(self):
        response = Mock()
        response.text = "<html><title>Gallery</title></html>"
        response.headers = {}
        response.url = "http://example.test/gallery/"
        response.raise_for_status = Mock()
        cache = {}

        with patch.object(gr.session, "get", return_value=response):
            html, changed = gr.fetch_html_cached(
                "http://example.test/gallery",
                cache,
                quick_scan=False,
            )

        self.assertTrue(changed)
        self.assertIn("Gallery", html)
        self.assertEqual(cache["http://example.test/gallery"]["final_url"], response.url)

    def test_legacy_cached_page_backfills_effective_url_without_quick_scan(self):
        response = Mock()
        response.url = "http://example.test/gallery/"
        cache = {
            "http://example.test/gallery": {
                "html": "<html><title>Cached</title></html>",
            }
        }

        with patch.object(gr.session, "head", return_value=response):
            html, changed = gr.fetch_html_cached(
                "http://example.test/gallery",
                cache,
                quick_scan=False,
            )

        self.assertFalse(changed)
        self.assertIn("Cached", html)
        self.assertEqual(
            cache["http://example.test/gallery"]["final_url"],
            "http://example.test/gallery/",
        )

    def test_failed_quick_head_does_not_replace_cached_effective_url(self):
        response = Mock()
        response.ok = False
        response.status_code = 503
        response.url = "http://example.test/login"
        response.headers = {}
        cache = {
            "http://example.test/gallery": {
                "html": "<html><title>Cached</title></html>",
                "final_url": "http://example.test/gallery/",
                "etag": "old-etag",
            }
        }

        with patch.object(gr.session, "head", return_value=response):
            html, changed = gr.fetch_html_cached(
                "http://example.test/gallery",
                cache,
                quick_scan=True,
            )

        self.assertFalse(changed)
        self.assertIn("Cached", html)
        self.assertEqual(
            cache["http://example.test/gallery"]["final_url"],
            "http://example.test/gallery/",
        )

    def test_failed_legacy_head_does_not_store_error_url(self):
        response = Mock()
        response.ok = False
        response.status_code = 503
        response.url = "http://example.test/login"
        cache = {
            "http://example.test/gallery": {
                "html": "<html><title>Cached</title></html>",
            }
        }

        with patch.object(gr.session, "head", return_value=response):
            html, changed = gr.fetch_html_cached(
                "http://example.test/gallery",
                cache,
                quick_scan=False,
            )

        self.assertFalse(changed)
        self.assertIn("Cached", html)
        self.assertNotIn("final_url", cache["http://example.test/gallery"])

    def test_discovery_resolves_links_from_effective_page_url(self):
        root = "http://example.test/gallery"
        child = "http://example.test/gallery/index.php?cat=2"
        pages = {
            root: (
                '<html><title>Home</title><a href="index.php?cat=2">Appearances</a></html>',
                "http://example.test/gallery/",
            ),
            child: ("<html><title>Appearances</title></html>", child),
        }

        def fake_fetch(url, page_cache, **_kwargs):
            html, final_url = pages[url]
            page_cache[url] = {"html": html, "final_url": final_url}
            return html, True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch):
            tree = gr.discover_tree(root, page_cache={}, quick_scan=False)

        self.assertEqual([node["url"] for node in tree["children"]], [child])
        self.assertTrue(
            all(
                item["url"].startswith("http://example.test/gallery/thumbnails.php?")
                for item in tree["specials"]
            )
        )

    def test_broken_album_does_not_abort_discovery(self):
        root = "http://example.test/gallery/index.php?cat=11"
        html = """
        <html><title>2006</title>
          <a href="thumbnails.php?album=8">Working</a>
          <a href="thumbnails.php?album=9">Gone</a>
          <a href="thumbnails.php?album=10">Transient</a>
        </html>
        """
        logs = []

        def fake_fetch(url, page_cache, **_kwargs):
            page_cache[url] = {"html": html, "final_url": root}
            return html, True

        def fake_count(url, _page_cache):
            if "album=9" in url:
                raise self._http_error(404, url)
            if "album=10" in url:
                raise self._http_error(500, url)
            return 31

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch), patch.object(
            gr, "get_album_image_count", side_effect=fake_count
        ):
            tree = gr.discover_tree(
                root,
                log=logs.append,
                page_cache={},
                quick_scan=False,
            )

        self.assertEqual([album["name"] for album in tree["albums"]], ["Working", "Transient"])
        self.assertEqual(tree["albums"][0]["image_count"], 31)
        self.assertEqual(tree["albums"][1]["image_count"], "?")
        self.assertTrue(any("Skipping unavailable album: Gone" in line for line in logs))
        self.assertTrue(any("keeping it with unknown count" in line for line in logs))

    def test_broken_subcategory_does_not_abort_siblings(self):
        root = "http://example.test/gallery/index.php"
        bad = "http://example.test/gallery/index.php?cat=2"
        good = "http://example.test/gallery/index.php?cat=3"
        root_html = """
        <html><title>Home</title>
          <a href="index.php?cat=2">Broken</a>
          <a href="index.php?cat=3">Working</a>
        </html>
        """
        logs = []

        def fake_fetch(url, page_cache, **_kwargs):
            if url == bad:
                raise self._http_error(500, url)
            html = root_html if url == root else "<html><title>Working</title></html>"
            page_cache[url] = {"html": html, "final_url": url}
            return html, True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch):
            tree = gr.discover_tree(
                root,
                log=logs.append,
                page_cache={},
                quick_scan=False,
            )

        self.assertEqual([node["url"] for node in tree["children"]], [good])
        self.assertTrue(any("Skipping unavailable subcategory: Broken" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
