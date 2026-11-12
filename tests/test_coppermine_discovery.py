import unittest
import tempfile
import io
from pathlib import Path
from unittest.mock import ANY, Mock, patch

import requests

import gallery_ripper as gr


class CoppermineDiscoveryTests(unittest.TestCase):
    def setUp(self):
        gr._page_request_state.clear()

    @staticmethod
    def _response(status=200, url="http://example.test/gallery/", text="", headers=None):
        response = Mock()
        response.status_code = status
        response.ok = 200 <= status < 400
        response.url = url
        response.text = text
        response.headers = headers or {}
        response.raise_for_status = Mock()
        if status >= 400:
            response.raise_for_status.side_effect = requests.HTTPError(
                f"{status} for {url}", response=response
            )
        return response

    @staticmethod
    def _http_error(status, url):
        response = requests.Response()
        response.status_code = status
        response.url = url
        return requests.HTTPError(f"{status} for {url}", response=response)

    def test_page_requests_start_unthrottled(self):
        with patch.object(gr.time, "monotonic", return_value=100.0), patch.object(
            gr.time, "sleep"
        ) as sleep:
            gr._wait_for_page_request("http://example.test/one")
            gr._wait_for_page_request("http://example.test/two")

        sleep.assert_not_called()

    def test_transient_404_activates_host_backoff(self):
        first = self._response(404)
        second = self._response(200)
        logs = []

        with patch.object(gr, "_wait_for_page_request"), patch.object(
            gr.time, "sleep"
        ) as sleep, patch.object(
            gr.session, "get", side_effect=[first, second]
        ):
            response = gr._discovery_request(
                "get",
                "http://example.test/gallery/index.php?cat=14",
                log=logs.append,
            )

        self.assertIs(response, second)
        sleep.assert_called_once_with(gr.PAGE_REQUEST_RETRY_DELAY)
        self.assertGreater(gr._page_request_state["example.test"]["delay"], 0)
        self.assertTrue(any("HTTP 404" in line and "retrying" in line for line in logs))

    def test_persistent_404_does_not_throttle_whole_host(self):
        first = self._response(404)
        second = self._response(404)

        with patch.object(gr, "_wait_for_page_request"), patch.object(
            gr.time, "sleep"
        ), patch.object(gr.session, "get", side_effect=[first, second]):
            response = gr._discovery_request(
                "get", "http://example.test/gallery/thumbnails.php?album=9"
            )

        self.assertIs(response, second)
        self.assertEqual(
            gr._page_request_state.get("example.test", {}).get("delay", 0),
            0,
        )

    def test_discovery_request_uses_same_origin_referer(self):
        response = self._response(200)
        with patch.object(gr, "_wait_for_page_request"), patch.object(
            gr.session, "get", return_value=response
        ) as get:
            gr._discovery_request("get", "http://example.test/gallery/index.php")

        self.assertEqual(
            get.call_args.kwargs["headers"]["Referer"],
            "http://example.test/",
        )

    def test_fresh_cached_page_skips_network(self):
        now = 1000.0
        cache = {
            "http://example.test/gallery": {
                "html": "<html><title>Cached</title></html>",
                "timestamp": now - 10,
                "final_url": "http://example.test/gallery/",
            }
        }
        with patch.object(gr.time, "time", return_value=now), patch.object(
            gr.session, "head"
        ) as head, patch.object(gr.session, "get") as get:
            html, changed = gr.fetch_html_cached(
                "http://example.test/gallery", cache, quick_scan=True
            )

        self.assertFalse(changed)
        self.assertIn("Cached", html)
        head.assert_not_called()
        get.assert_not_called()

    def test_fresh_legacy_cache_backfills_effective_url(self):
        url = "http://example.test/gallery"
        cached_html = "<html><title>Cached</title></html>"
        response = self._response(
            200, url="http://example.test/gallery/", text=cached_html
        )
        cache = {url: {"html": cached_html, "timestamp": 995.0}}

        with patch.object(gr.time, "time", return_value=1000.0), patch.object(
            gr.session, "get", return_value=response
        ) as get, patch.object(gr.session, "head") as head:
            html, changed = gr.fetch_html_cached(url, cache, quick_scan=True)

        self.assertFalse(changed)
        self.assertIn("Cached", html)
        get.assert_called_once()
        head.assert_not_called()
        self.assertEqual(cache[url]["final_url"], "http://example.test/gallery/")

    def test_failed_quick_revalidation_does_not_replace_cached_effective_url(self):
        url = "http://example.test/gallery"
        response = self._response(503, url="http://example.test/login")
        cache = {
            url: {
                "html": "<html><title>Cached</title></html>",
                "timestamp": 0,
                "final_url": "http://example.test/gallery/",
                "etag": "old-etag",
            }
        }

        with patch.object(gr.time, "time", return_value=1000.0), patch.object(
            gr.session, "get", return_value=response
        ), patch.object(gr.session, "head") as head, patch.object(gr.time, "sleep"):
            html, changed = gr.fetch_html_cached(url, cache, quick_scan=True)
            head.assert_not_called()

        self.assertFalse(changed)
        self.assertIn("Cached", html)
        self.assertEqual(cache[url]["final_url"], "http://example.test/gallery/")

    def test_failed_legacy_get_does_not_store_error_url(self):
        url = "http://example.test/gallery"
        response = self._response(503, url="http://example.test/login")
        cache = {url: {"html": "<html><title>Cached</title></html>"}}

        with patch.object(gr.session, "get", return_value=response), patch.object(
            gr.session, "head"
        ) as head, patch.object(gr.time, "sleep"):
            html, changed = gr.fetch_html_cached(url, cache, quick_scan=False)
            head.assert_not_called()

        self.assertFalse(changed)
        self.assertIn("Cached", html)
        self.assertNotIn("final_url", cache[url])

    def test_fetch_cache_records_effective_response_url(self):
        response = self._response(
            200,
            url="http://example.test/gallery/",
            text="<html><title>Gallery</title></html>",
        )
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

    def test_album_image_count_is_reused_for_same_html_version(self):
        url = "http://example.test/gallery/thumbnails.php?album=7"
        html = "<html><body>42 files</body></html>"
        html_hash = gr.hashlib.sha1(html.encode("utf-8")).hexdigest()
        cache = {
            url: {
                "html": html,
                "html_hash": html_hash,
                "image_count": 42,
                "image_count_html_hash": html_hash,
            }
        }

        with patch.object(
            gr,
            "fetch_html_cached",
            return_value=(html, False),
        ):
            count = gr.get_album_image_count(url, cache)

        self.assertEqual(count, 42)

    def test_album_image_count_recomputes_after_page_refresh(self):
        url = "http://example.test/gallery/thumbnails.php?album=7"
        old_html = "<html><body>42 files</body></html>"
        new_html = "<html><body>43 files</body></html>"
        old_hash = gr.hashlib.sha1(old_html.encode("utf-8")).hexdigest()
        new_hash = gr.hashlib.sha1(new_html.encode("utf-8")).hexdigest()
        cache = {
            url: {
                "html": old_html,
                "html_hash": old_hash,
                "image_count": 42,
                "image_count_html_hash": old_hash,
            }
        }

        def fake_fetch(_url, page_cache, **_kwargs):
            page_cache[url] = {
                "html": new_html,
                "html_hash": new_hash,
                "final_url": url,
            }
            return new_html, True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch):
            count = gr.get_album_image_count(url, cache)

        self.assertEqual(count, 43)
        self.assertEqual(cache[url]["image_count_html_hash"], new_hash)

    def test_legacy_album_count_without_html_version_is_recomputed(self):
        url = "http://example.test/gallery/thumbnails.php?album=7"
        html = "<html><body>9 files</body></html>"
        cache = {url: {"html": html, "image_count": 42}}

        with patch.object(
            gr,
            "fetch_html_cached",
            return_value=(html, False),
        ):
            count = gr.get_album_image_count(url, cache)

        self.assertEqual(count, 9)
        self.assertEqual(cache[url]["image_count"], 9)
        self.assertIn("image_count_html_hash", cache[url])

    def test_stale_cached_page_refreshes_with_one_conditional_get(self):
        url = "http://example.test/gallery/index.php?cat=2"
        old_html = "<html><title>Old</title></html>"
        new_html = "<html><title>New</title></html>"
        cache = {
            url: {
                "html": old_html,
                "html_hash": gr.hashlib.sha1(old_html.encode("utf-8")).hexdigest(),
                "timestamp": 0,
                "etag": "old",
                "final_url": url,
            }
        }
        response = self._response(200, url=url, text=new_html, headers={"ETag": "new"})

        with patch.object(gr.time, "time", return_value=1000.0), patch.object(
            gr.session, "head"
        ) as head, patch.object(gr.session, "get", return_value=response) as get:
            html, changed = gr.fetch_html_cached(url, cache, quick_scan=True)
            head.assert_not_called()
            get.assert_called_once()
            self.assertEqual(get.call_args.kwargs["headers"]["If-None-Match"], "old")

        self.assertTrue(changed)
        self.assertEqual(html, new_html)
        self.assertEqual(cache[url]["etag"], "new")
        self.assertNotIn("image_count", cache[url])

    def test_old_tree_cache_is_invalidated_but_pages_are_preserved(self):
        url = "http://example.test/gallery/index.php"
        pages = {url: {"html": "<html></html>"}}
        old_tree = {"type": "category", "name": "Old topology", "url": url}

        with patch.object(gr, "site_cache_path", return_value="cache.json"), patch.object(
            gr.os.path, "exists", return_value=True
        ), patch("builtins.open"), patch.object(
            gr.json,
            "load",
            return_value={
                "tree_v": gr.TREE_CACHE_VERSION - 1,
                "tree": old_tree,
                "pages": pages,
            },
        ):
            loaded_pages, loaded_tree = gr.load_page_cache(url)

        self.assertEqual(loaded_pages, pages)
        self.assertIsNone(loaded_tree)

    def test_current_tree_cache_version_is_reused(self):
        url = "http://example.test/gallery/index.php"
        pages = {url: {"html": "<html></html>"}}
        tree = {"type": "category", "name": "Current topology", "url": url}

        with patch.object(gr, "site_cache_path", return_value="cache.json"), patch.object(
            gr.os.path, "exists", return_value=True
        ), patch("builtins.open"), patch.object(
            gr.json,
            "load",
            return_value={
                "tree_v": gr.TREE_CACHE_VERSION,
                "tree": tree,
                "pages": pages,
            },
        ):
            loaded_pages, loaded_tree = gr.load_page_cache(url)

        self.assertEqual(loaded_pages, pages)
        self.assertEqual(loaded_tree, tree)

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

    def test_album_listing_count_avoids_album_page_fetch(self):
        root = "http://example.test/gallery/index.php?cat=14"
        html = """
            <html><title>2008</title><table><tr>
              <td><a class="albums" href="thumbnails.php?album=101">First</a></td>
              <td></td>
              <td><p class="album_stat">31 files, last one added today</p></td>
            </tr></table></html>
        """

        def fake_fetch(url, page_cache, **_kwargs):
            page_cache[url] = {"html": html, "final_url": url}
            return html, True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch), patch.object(
            gr, "get_album_image_count"
        ) as network_count:
            tree = gr.discover_tree(
                root,
                parent_title="2008",
                page_cache={},
                quick_scan=False,
            )

        self.assertEqual(tree["albums"][0]["image_count"], 31)
        network_count.assert_not_called()

    def test_lazy_display_resolution_is_not_used_when_direct_original_downloads(self):
        album = "http://example.test/gallery/thumbnails.php?album=5"
        display = "http://example.test/gallery/displayimage.php?album=5&pid=7"
        html = f"""
            <html>
              <a href="{display}">
                <img src="albums/test/thumb_001.jpg">
              </a>
            </html>
        """
        cache = {
            album: {
                "html": html,
                "timestamp": 100.0,
                "final_url": album,
            }
        }

        with patch.object(gr.time, "time", return_value=100.0), patch.object(
            gr, "extract_all_displayimage_candidates"
        ) as resolve:
            entries = gr.get_all_candidate_images_from_album(
                album,
                page_cache=cache,
                quick_scan=True,
            )

        resolve.assert_not_called()
        candidates = entries[0][1]
        self.assertIn("__display__=", candidates[0])
        self.assertTrue(candidates[0].split("#", 1)[0].endswith("/albums/test/001.jpg"))

    def test_lazy_display_page_resolves_only_after_direct_original_fails(self):
        original = "http://example.test/gallery/albums/test/001.jpg"
        display = "http://example.test/gallery/displayimage.php?album=5&pid=7"
        resolved = "http://example.test/gallery/albums/test/full_001.jpg"
        candidate = gr._encode_candidate_metadata(
            original,
            referer="http://example.test/gallery/thumbnails.php?album=5",
            display_url=display,
        )

        failed = self._response(404, url=original)
        failed.raise_for_status.side_effect = requests.HTTPError(
            "404", response=failed
        )
        success = self._response(200, url=resolved, headers={"Content-Type": "image/jpeg"})
        success.iter_content = Mock(return_value=[b"abc"])
        with tempfile.TemporaryDirectory() as output_dir, patch.object(
            gr.session, "get", side_effect=[failed, success]
        ), patch.object(
            gr,
            "extract_all_displayimage_candidates",
            return_value=[resolved],
        ) as resolve_detail:
            downloaded = gr.download_image_candidates(
                [candidate],
                output_dir,
                log=lambda _msg: None,
                max_attempts=1,
            )
            self.assertEqual(Path(output_dir, "full_001.jpg").read_bytes(), b"abc")

        self.assertTrue(downloaded)
        resolve_detail.assert_called_once_with(
            display,
            ANY,
            referer="http://example.test/gallery/thumbnails.php?album=5",
        )

    def test_album_pagination_does_not_refetch_page_one_alias(self):
        album = "http://example.test/gallery/thumbnails.php?album=383"
        page1 = album + "&page=1"
        page2 = album + "&page=2"
        pages = {
            album: f"""
                <html>
                  <a href="displayimage.php?album=383&pid=1">
                    <img src="albums/test/thumb_001.jpg">
                  </a>
                  <a href="{page1}">1</a>
                  <a href="{page2}">2</a>
                </html>
            """,
            page2: f"""
                <html>
                  <a href="displayimage.php?album=383&pid=2">
                    <img src="albums/test/thumb_002.jpg">
                  </a>
                  <a href="{page1}">1</a>
                  <a href="{page2}">2</a>
                </html>
            """,
        }
        fetched = []

        def fake_fetch(url, page_cache, **_kwargs):
            fetched.append(url)
            if url == page1:
                raise self._http_error(404, url)
            html = pages[url]
            page_cache[url] = {"html": html, "final_url": url}
            return html, True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch):
            entries = gr.get_all_candidate_images_from_album(
                album,
                page_cache={},
                quick_scan=False,
            )

        self.assertEqual(fetched, [album, page2])
        self.assertNotIn(page1, fetched)
        urls = [
            candidate.split("#", 1)[0]
            for _name, candidates, _ref in entries
            for candidate in candidates
        ]
        self.assertTrue(any(url.endswith("/albums/test/001.jpg") for url in urls))
        self.assertTrue(any(url.endswith("/albums/test/002.jpg") for url in urls))

    def test_album_pagination_failure_does_not_abort_album(self):
        album = "http://example.test/gallery/thumbnails.php?album=383"
        page2 = album + "&page=2"
        root_html = f"""
            <html>
              <a href="displayimage.php?album=383&pid=1">
                <img src="albums/test/thumb_001.jpg">
              </a>
              <a href="{page2}">2</a>
            </html>
        """
        logs = []

        def fake_fetch(url, page_cache, **_kwargs):
            if url == page2:
                raise self._http_error(404, url)
            page_cache[url] = {"html": root_html, "final_url": url}
            return root_html, True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch):
            entries = gr.get_all_candidate_images_from_album(
                album,
                log=logs.append,
                page_cache={},
                quick_scan=False,
            )

        self.assertEqual(len(entries), 1)
        self.assertTrue(
            any("Skipping unavailable pagination page" in line for line in logs)
        )

    def test_batch_skips_unavailable_album_and_continues(self):
        root = "http://example.test/gallery/index.php"
        bad = "http://example.test/gallery/thumbnails.php?album=1"
        good = "http://example.test/gallery/thumbnails.php?album=2"
        logs = []
        selected = [
            ("Bad", bad, ["Bad"]),
            ("Good", good, ["Good"]),
        ]

        def scrape(url, **_kwargs):
            if url == bad:
                raise self._http_error(404, url)
            return []

        with patch.object(gr, "select_adapter_for_url", return_value="coppermine"), patch.object(
            gr, "load_page_cache", return_value=({}, None)
        ), patch.object(
            gr, "get_all_candidate_images_from_album", side_effect=scrape
        ) as scraper:
            gr.rip_galleries(
                selected,
                "out",
                logs.append,
                root,
                quick_scan=False,
            )

        self.assertEqual(scraper.call_count, 2)
        self.assertTrue(any("Skipping unavailable album: Bad" in line for line in logs))
        self.assertTrue(any("No images to download." in line for line in logs))

    def test_category_pagination_is_aggregated_not_nested(self):
        root = "http://example.test/gallery/index.php?cat=14"
        page2 = "http://example.test/gallery/index.php?cat=14&page=2"
        album1 = "http://example.test/gallery/thumbnails.php?album=101"
        album2 = "http://example.test/gallery/thumbnails.php?album=102&cat=14"
        pages = {
            root: f"""
                <html><title>2008</title>
                  <a href="index.php?cat=2">Appearances</a>
                  <a href="index.php?page=2&cat=14">2</a>
                  <a href="thumbnails.php?album=101">First</a>
                </html>
            """,
            page2: f"""
                <html><title>2</title>
                  <a href="index.php?page=1&cat=14">1</a>
                  <a href="thumbnails.php?cat=14&album=101">First</a>
                  <a href="thumbnails.php?album=102&cat=14">Second</a>
                </html>
            """,
        }

        def fake_fetch(url, page_cache, **_kwargs):
            html = pages[url]
            page_cache[url] = {"html": html, "final_url": url}
            return html, True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch), patch.object(
            gr, "get_album_image_count", side_effect=lambda url, _cache: {album1: 3, album2: 4}[url]
        ) as count:
            tree = gr.discover_tree(
                root,
                parent_cat="2",
                parent_title="2008",
                page_cache={},
                quick_scan=False,
                probe_album_counts=True,
            )

        self.assertEqual(tree["children"], [])
        self.assertEqual([a["name"] for a in tree["albums"]], ["First", "Second"])
        self.assertEqual(count.call_count, 2)

    def test_displayimage_fetch_uses_album_referer(self):
        album = "http://example.test/gallery/thumbnails.php?album=5"
        display = "http://example.test/gallery/displayimage.php?album=5&pid=7"
        page = self._response(
            200,
            url=display,
            text='<html><img class="image" src="albums/test/001.jpg"></html>',
        )

        with patch.object(gr, "_wait_for_page_request"), patch.object(
            gr.session, "get", return_value=page
        ) as get:
            candidates = gr.extract_all_displayimage_candidates(
                display,
                referer=album,
            )

        self.assertTrue(any(url.endswith("/albums/test/001.jpg") for url in candidates))
        self.assertEqual(get.call_args.kwargs["headers"]["Referer"], album)

    def test_category_pagination_preserves_page_one_when_base_is_page_zero(self):
        root = "http://example.test/gallery/index.php?cat=14"
        page1 = "http://example.test/gallery/index.php?cat=14&page=1"
        pages = {
            root: """
                <html><title>Category</title>
                  <a href="index.php?cat=14&page=0">0</a>
                  <a href="index.php?cat=14&page=1">1</a>
                  <a href="thumbnails.php?album=101">Zero page album</a>
                </html>
            """,
            page1: """
                <html><title>1</title>
                  <a href="index.php?cat=14&page=0">0</a>
                  <a href="thumbnails.php?album=102">Page one album</a>
                </html>
            """,
        }

        def fake_fetch(url, page_cache, **_kwargs):
            html = pages[url]
            page_cache[url] = {"html": html, "final_url": url}
            return html, True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch), patch.object(
            gr, "get_album_image_count", return_value=1
        ):
            tree = gr.discover_tree(
                root,
                parent_title="Category",
                page_cache={},
                quick_scan=False,
            )

        self.assertEqual(
            [album["name"] for album in tree["albums"]],
            ["Zero page album", "Page one album"],
        )

    def test_theme_asset_inside_gallery_path_is_ui(self):
        url = "http://example.test/gallery/themes/theme/images/header.jpg"
        self.assertTrue(gr.is_ui_image(url, "header.jpg"))

    def test_album_extraction_drops_theme_header(self):
        album = "http://example.test/gallery/thumbnails.php?album=199"
        html = """
            <html>
              <img src="themes/theme/images/header.jpg" width="900" height="300">
              <img src="albums/test/thumb_001.jpg">
            </html>
        """
        cache = {album: {"html": html, "timestamp": 100.0, "final_url": album}}

        with patch.object(gr.time, "time", return_value=100.0), patch.object(
            gr.session, "head"
        ) as head_probe:
            entries = gr.get_all_candidate_images_from_album(
                album,
                page_cache=cache,
                quick_scan=True,
            )

        head_probe.assert_not_called()
        urls = [u for _name, candidates, _ref in entries for u in candidates]
        self.assertFalse(any("/themes/" in u for u in urls))
        self.assertTrue(any("/albums/test/001.jpg" in u for u in urls))

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
                probe_album_counts=True,
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

        self.assertEqual([node["url"] for node in tree["children"]], [bad, good])
        self.assertTrue(tree["incomplete"])
        self.assertTrue(tree["children"][0]["incomplete"])
        self.assertFalse(tree["children"][1]["incomplete"])
        self.assertTrue(any("Unavailable subcategory: Broken" in line for line in logs))


class CoppermineTrafficReductionTests(unittest.TestCase):
    _response = staticmethod(CoppermineDiscoveryTests._response)

    def setUp(self):
        gr._page_request_state.clear()

    def test_coppermine_discovery_does_not_fetch_album_pages_for_counts(self):
        root = "https://gallery.test/photos/index.php?cat=1"
        html = (
            "<html><title>Movies</title>"
            + "".join(
                f'<a href="thumbnails.php?album={index}">Album {index}</a>'
                for index in range(1, 41)
            )
            + "</html>"
        )
        fetched = []

        def fake_fetch(url, pages, **_kwargs):
            fetched.append(url)
            pages[url] = {"final_url": url, "html": html}
            return html, True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch), patch.object(
            gr, "get_album_image_count"
        ) as get_count:
            tree = gr.discover_tree(root, page_cache={}, quick_scan=False)

        self.assertEqual(fetched, [root])
        get_count.assert_not_called()
        self.assertEqual(len(tree["albums"]), 40)
        self.assertTrue(all(a["image_count"] == "?" for a in tree["albums"]))
        self.assertFalse(tree["incomplete"])

    def test_cached_album_count_is_used_without_network(self):
        root = "https://gallery.test/photos/index.php?cat=1"
        album = "https://gallery.test/photos/thumbnails.php?album=8"
        html = '<html><title>Movies</title><a href="thumbnails.php?album=8">Stills</a></html>'
        previous = {
            "type": "category", "url": root, "name": "Movies",
            "children": [], "albums": [{
                "type": "album", "url": album, "name": "Stills", "image_count": 440,
            }],
            "specials": [], "incomplete": False,
        }

        def fake_fetch(url, pages, **_kwargs):
            pages[url] = {"final_url": url, "html": html}
            return html, True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch), patch.object(
            gr, "get_album_image_count"
        ) as count:
            tree = gr.discover_tree(
                root, page_cache={}, cached_nodes={root: previous}, quick_scan=False
            )

        count.assert_not_called()
        self.assertEqual(tree["albums"][0]["image_count"], 440)

    def test_conditional_304_does_not_discard_cached_album_metadata(self):
        url = "https://gallery.test/photos/thumbnails.php?album=8"
        html = "<html><title>Stills</title></html>"
        digest = gr.hashlib.sha1(html.encode("utf-8")).hexdigest()
        pages = {url: {
            "html": html, "html_hash": digest, "timestamp": 1,
            "final_url": url, "etag": "abc", "image_count": 123,
            "images": [("one", ["x"], url)], "images_v": gr.IMAGE_EXTRACTOR_VERSION,
        }}
        response = self._response(304, url=url)
        with patch.object(gr.time, "time", return_value=1000), patch.object(
            gr.session, "get", return_value=response
        ) as get, patch.object(gr.session, "head") as head:
            refreshed, changed = gr.fetch_html_cached(url, pages)
        self.assertEqual(refreshed, html)
        self.assertFalse(changed)
        self.assertEqual(pages[url]["image_count"], 123)
        self.assertEqual(len(pages[url]["images"]), 1)
        get.assert_called_once()
        head.assert_not_called()

    def test_identical_200_preserves_cached_image_list(self):
        url = "https://gallery.test/photos/thumbnails.php?album=8"
        html = "<html><title>Stills</title></html>"
        pages = {url: {
            "html": html, "timestamp": 1, "final_url": url,
            "images": [("one", ["x"], url)],
        }}
        response = self._response(200, url=url, text=html)
        with patch.object(gr.time, "time", return_value=1000), patch.object(
            gr.session, "get", return_value=response
        ) as get:
            refreshed, changed = gr.fetch_html_cached(url, pages)
        self.assertEqual(refreshed, html)
        self.assertFalse(changed)
        self.assertEqual(len(pages[url]["images"]), 1)
        get.assert_called_once()

    def test_blocked_stale_cache_is_not_mistaken_for_complete_scan(self):
        url = "https://gallery.test/photos/index.php?cat=1"
        pages = {url: {
            "html": "<html><title>Old</title></html>",
            "timestamp": 1, "final_url": url,
        }}
        denied = self._response(403, url=url)
        with patch.object(gr.time, "time", return_value=1000), patch.object(
            gr.session, "get", return_value=denied
        ) as get, patch.object(gr.time, "sleep") as sleep:
            with self.assertRaises(requests.HTTPError):
                gr.fetch_html_cached(url, pages)
        self.assertEqual(pages[url]["html"], "<html><title>Old</title></html>")
        get.assert_called_once()
        sleep.assert_not_called()

    def test_stop_prevents_additional_category_requests(self):
        root = "https://gallery.test/photos/"
        child = "https://gallery.test/photos/index.php?cat=2"
        stop = gr.threading.Event()
        fetched = []
        html = (
            '<html><title>Home</title>'
            '<a href="index.php?cat=2">Movies</a></html>'
        )

        def fake_fetch(url, pages, **_kwargs):
            fetched.append(url)
            self.assertEqual(url, root)
            pages[url] = {"html": html, "final_url": url}
            stop.set()
            return html, True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch):
            with self.assertRaises(gr.PacingCancelled):
                gr.discover_tree(
                    root, page_cache={}, quick_scan=False, stop_flag=stop
                )
        self.assertEqual(fetched, [root])
        self.assertNotIn(child, fetched)

    def test_cancelled_cached_page_does_not_hide_stop_request(self):
        url = "https://gallery.test/photos/"
        stop = gr.threading.Event()
        stop.set()
        cache = {url: {
            "html": "<html><title>Old</title></html>",
            "timestamp": 1000, "final_url": url,
        }}
        with patch.object(gr.session, "get") as get:
            with self.assertRaises(gr.PacingCancelled):
                gr.fetch_html_cached(url, cache, stop_flag=stop)
        get.assert_not_called()

    def test_coppermine_request_passes_stop_event_to_scheduler(self):
        url = "https://gallery.test/photos/"
        stop = gr.threading.Event()
        response = self._response(200, url=url)
        with patch.object(gr.session, "get", return_value=response) as get, patch.object(
            gr, "_wait_for_page_request"
        ):
            gr._discovery_request("get", url, stop_flag=stop)
        self.assertIs(get.call_args.kwargs["_pacing_stop_event"], stop)

    def test_cancellation_is_not_relabelled_as_incomplete_cache(self):
        root = "https://gallery.test/photos/"
        cached_tree = {"type": "category", "name": "Old", "url": root}
        stop = gr.threading.Event()
        stop.set()
        with patch.object(
            gr, "load_page_cache", return_value=({}, cached_tree)
        ), patch.object(gr, "save_page_cache") as saved, patch.object(
            gr, "discover_tree", side_effect=gr.PacingCancelled("stopped")
        ):
            with self.assertRaises(gr.PacingCancelled):
                gr.discover_or_load_gallery_tree(
                    root, lambda _msg: None, stop_flag=stop
                )
        saved.assert_not_called()

    def test_blocked_coppermine_image_aborts_without_retrying_candidates(self):
        urls = [
            "https://gallery.test/photos/albums/original.jpg",
            "https://gallery.test/photos/albums/normal_original.jpg",
        ]
        for status in (403, 429):
            with self.subTest(status=status):
                response = self._response(status, url=urls[0])
                stop = gr.threading.Event()
                logs = []
                with tempfile.TemporaryDirectory() as folder, patch.object(
                    gr.session, "get", return_value=response
                ) as get, patch.object(gr.time, "sleep") as sleep:
                    result = gr.download_image_candidates(
                        urls, folder, logs.append, max_attempts=3,
                        stop_flag=stop, abort_on_block=True,
                    )
                    self.assertFalse(result)
                    self.assertTrue(stop.is_set())
                    get.assert_called_once()
                    sleep.assert_not_called()
                    self.assertEqual(list(Path(folder).iterdir()), [])
                self.assertTrue(any(f"HTTP {status}" in msg for msg in logs))

    def test_blocked_album_stops_following_album_scans(self):
        root = "https://gallery.test/photos/"
        selected = [
            ("First", root + "thumbnails.php?album=1", ["First"]),
            ("Second", root + "thumbnails.php?album=2", ["Second"]),
        ]
        rejected = self._response(403, url=selected[0][1])
        stop = gr.threading.Event()
        logs = []
        with tempfile.TemporaryDirectory() as folder, patch.object(
            gr, "load_page_cache", return_value=({}, None)
        ), patch.object(gr, "save_page_cache") as saved, patch.object(
            gr, "get_all_candidate_images_from_album",
            side_effect=requests.HTTPError("Forbidden", response=rejected)
        ) as extract:
            gr._rip_galleries_impl(
                selected, folder, logs.append, root,
                stop_flag=stop, workers=8,
            )
        self.assertTrue(stop.is_set())
        extract.assert_called_once()
        saved.assert_called_once()
        self.assertTrue(any("1 download worker" in msg for msg in logs))
        self.assertTrue(any("stopping remaining" in msg for msg in logs))

    def test_blocked_album_pagination_is_not_silently_partial(self):
        root = "https://gallery.test/photos/thumbnails.php?album=1"
        next_page = root + "&page=2"
        html = (
            '<html><a href="displayimage.php?album=1&pid=7">'
            '<img src="albums/test/thumb_001.jpg"></a>'
            '<a href="thumbnails.php?album=1&page=2">2</a></html>'
        )
        blocked = self._response(403, url=next_page)

        def fake_fetch(url, pages, **_kwargs):
            if url == next_page:
                raise requests.HTTPError("Forbidden", response=blocked)
            self.assertEqual(url, root)
            pages[url] = {"final_url": url, "html": html}
            return html, True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch):
            with self.assertRaises(requests.HTTPError):
                gr.get_all_candidate_images_from_album(
                    root, page_cache={}, quick_scan=False,
                )


    def test_discovery_installs_host_policy_before_fetching_pages(self):
        root = "https://gallery.test/photos/"
        tree = {
            "type": "category", "name": "Home", "url": root,
            "children": [], "albums": [],
        }
        with patch.object(gr, "load_page_cache", return_value=({}, None)), patch.object(
            gr, "save_page_cache"
        ), patch.object(
            gr.session.pacer, "set_host_min_interval"
        ) as configure, patch.object(gr, "discover_tree", return_value=tree) as discover:
            result = gr.discover_or_load_gallery_tree(root, lambda _msg: None)
        self.assertIs(result, tree)
        configure.assert_called_once_with(root, gr.COPPERMINE_REQUEST_INTERVAL_SECONDS)
        discover.assert_called_once()


class CoppermineRecoveryTests(unittest.TestCase):
    _response = staticmethod(CoppermineDiscoveryTests._response)
    _http_error = staticmethod(CoppermineDiscoveryTests._http_error)

    def setUp(self):
        gr._page_request_state.clear()

    def test_http_403_is_not_retried(self):
        response = self._response(403)
        with patch.object(gr.session, "get", return_value=response) as get, patch.object(
            gr.time, "sleep"
        ) as sleep:
            result = gr._discovery_request("get", "https://blocked.test/photos/index.php")
        self.assertIs(result, response)
        get.assert_called_once()
        sleep.assert_not_called()

    def test_first_block_keeps_sibling_categories_without_requesting_them(self):
        root = "https://blocked.test/photos/"
        first = "https://blocked.test/photos/index.php?cat=2"
        second = "https://blocked.test/photos/index.php?cat=3"
        html = (
            '<html><title>Home</title>'
            '<a href="index.php?cat=2">Movies</a>'
            '<a href="index.php?cat=3">Photoshoots</a></html>'
        )
        requests_seen = []

        def fake_fetch(url, pages, **_kwargs):
            requests_seen.append(url)
            if url != root:
                raise self._http_error(403, url)
            pages[url] = {"final_url": url}
            return html, True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch):
            tree = gr.discover_tree(root, quick_scan=False, page_cache={})
        self.assertEqual(requests_seen, [root, first])
        self.assertEqual([n["name"] for n in tree["children"]], ["Movies", "Photoshoots"])
        self.assertTrue(all(n["incomplete"] for n in tree["children"]))
        self.assertTrue(tree["incomplete"])

    def test_blocked_child_preserves_previous_albums(self):
        root = "https://blocked.test/photos/"
        first = "https://blocked.test/photos/index.php?cat=2"
        html = '<html><title>Home</title><a href="index.php?cat=2">Movies</a></html>'
        old_child = {
            "type": "category", "url": first, "name": "Movies",
            "children": [], "specials": [], "albums": [
                {"type": "album", "name": "Screen Captures",
                 "url": "https://blocked.test/photos/thumbnails.php?album=8"}
            ],
        }

        def fake_fetch(url, pages, **_kwargs):
            if url == first:
                raise self._http_error(403, url)
            pages[url] = {"final_url": url}
            return html, True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch):
            tree = gr.discover_tree(
                root, page_cache={}, quick_scan=False, cached_nodes={first: old_child}
            )
        self.assertEqual(tree["children"][0]["albums"], old_child["albums"])
        self.assertTrue(tree["children"][0]["incomplete"])
        self.assertNotIn("incomplete", old_child)

    def test_incomplete_cache_cannot_skip_descendant_refresh(self):
        root = "https://gallery.test/photos/"
        child = "https://gallery.test/photos/index.php?cat=2"
        html = '<html><title>Home</title><a href="index.php?cat=2">Movies</a></html>'
        old_root = {
            "name": "Home", "url": root, "type": "category",
            "child_hash": gr.compute_child_hash([("Movies", child)], []),
            "incomplete": True, "children": [], "specials": [], "albums": [],
        }
        calls = []

        def fake_fetch(url, pages, **_kwargs):
            calls.append(url)
            pages[url] = {"final_url": url}
            return (html if url == root else "<html><title>Movies</title></html>"), True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch):
            tree = gr.discover_tree(
                root, page_cache={}, quick_scan=True, cached_nodes={root: old_root}
            )
        self.assertIn(child, calls)
        self.assertFalse(tree["incomplete"])
        self.assertEqual([n["name"] for n in tree["children"]], ["Movies"])




class CoppermineImagePrefilterTests(unittest.TestCase):
    @staticmethod
    def image_response(data, content_type="image/png"):
        response = Mock(status_code=200)
        response.headers = {"Content-Type": content_type}
        response.raise_for_status = Mock()
        response.iter_content.return_value = iter([data])
        return response

    @staticmethod
    def image_bytes(width, height, fmt="PNG"):
        output = io.BytesIO()
        gr.Image.new("RGB", (width, height)).save(output, format=fmt)
        return output.getvalue()

    def test_tooltip_dimensions_filter_without_any_image_request(self):
        album = "https://gallery.test/photos/thumbnails.php?album=352"
        display = "displayimage.php?album=352&pid=24561"
        html = (
            '<html><title>Premiere</title><a href="' + display + '">'
            '<img src="albums/PublicAppearances/thumb_023.jpg" '
            'onmouseover="return overlib(\'Filename=023.jpg &lt;br&gt; '
            'Dimensions=395x594\');"></a></html>'
        )
        cache = {}
        def fake_fetch(url, pages, **_):
            pages[url] = {"html": html, "final_url": url}
            return html, True
        skipped = {}
        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch), patch.object(
            gr, "extract_all_displayimage_candidates"
        ) as detail, patch.object(gr.session, "get") as get:
            entries = gr.get_all_candidate_images_from_album(
                album, page_cache=cache, quick_scan=False,
                min_width=1024, min_height=1024, skipped_stats=skipped,
            )
        self.assertEqual(entries, [])
        self.assertEqual(skipped["skipped_small"], 1)
        detail.assert_not_called()
        get.assert_not_called()
        self.assertEqual(cache[album]["images_v"], gr.IMAGE_EXTRACTOR_VERSION)
        self.assertEqual(len(cache[album]["images"]), 1)

    def test_cached_entries_reapply_changed_threshold_without_network(self):
        album = "https://gallery.test/photos/thumbnails.php?album=352"
        html = (
            '<html><title>Premiere</title><a href="displayimage.php?album=352&pid=1">'
            '<img src="albums/test/thumb_001.jpg" title="Dimensions=395x594">'
            '</a></html>'
        )
        cache = {album: {
            "html": html, "timestamp": 100, "final_url": album,
        }}
        with patch.object(gr.time, "time", return_value=100), patch.object(
            gr.session, "get"
        ) as get:
            too_small = gr.get_all_candidate_images_from_album(
                album, page_cache=cache, min_width=1024, min_height=1024,
            )
            eligible = gr.get_all_candidate_images_from_album(
                album, page_cache=cache, min_width=300, min_height=500,
            )
            disabled = gr.get_all_candidate_images_from_album(album, page_cache=cache)
        self.assertEqual(too_small, [])
        self.assertEqual(len(eligible), 1)
        self.assertEqual(len(disabled), 1)
        get.assert_not_called()

    def test_html_thumbnail_dimensions_do_not_reject_original(self):
        album = "https://gallery.test/photos/thumbnails.php?album=352"
        html = (
            '<html><title>Premiere</title><a href="displayimage.php?album=352&pid=1">'
            '<img src="albums/test/thumb_001.jpg" width="120" height="155"></a></html>'
        )
        cache = {album: {"html": html, "timestamp": 100, "final_url": album}}
        with patch.object(gr.time, "time", return_value=100):
            entries = gr.get_all_candidate_images_from_album(
                album, page_cache=cache, min_width=1024, min_height=1024
            )
        self.assertEqual(len(entries), 1)
        self.assertEqual(cache[album]["image_dimensions"], {})

    def test_dimensions_apply_to_correct_thumbnail_only(self):
        album = "https://gallery.test/photos/thumbnails.php?album=9"
        html = (
            '<table><tr><td><a href="displayimage.php?album=9&pid=1">'
            '<img src="albums/test/thumb_001.jpg" title="Dimensions=395x594"></a></td>'
            '<td><a href="displayimage.php?album=9&pid=2">'
            '<img src="albums/test/thumb_002.jpg" title="Dimensions=2560x1440"></a>'
            '</td></tr></table>'
        )
        cache = {album: {"html": html, "timestamp": 100, "final_url": album}}
        with patch.object(gr.time, "time", return_value=100):
            entries = gr.get_all_candidate_images_from_album(
                album, page_cache=cache, min_width=1024, min_height=1024,
            )
        self.assertEqual(len(entries), 1)
        self.assertTrue(gr._decode_candidate_metadata(entries[0][1][0])[0].endswith("002.jpg"))

    def test_header_rejects_small_png_before_second_body_chunk(self):
        data = self.image_bytes(395, 594)
        response = Mock(status_code=200)
        response.headers = {"Content-Type": "image/png"}
        response.raise_for_status = Mock()
        def body(_chunk_size):
            yield data[:128]
            raise AssertionError("Full image transfer should be aborted")
        response.iter_content.side_effect = body
        stats = {"downloaded": 0, "errors": 0, "total_bytes": 0, "total_time": 0.0}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            gr.session, "get", return_value=response
        ) as get:
            result = gr.download_image_candidates(
                ["https://gallery.test/albums/023.png"], folder,
                lambda _msg: None, max_attempts=2, album_stats=stats,
                min_width=1024, min_height=1024,
            )
            self.assertFalse(result)
            self.assertEqual(list(Path(folder).iterdir()), [])
        get.assert_called_once()
        response.close.assert_called_once()
        self.assertEqual(stats["skipped_small"], 1)
        self.assertEqual(stats["errors"], 0)

    def test_encoded_space_alias_and_thumbnail_are_not_downloaded(self):
        small = self.image_response(self.image_bytes(395, 594))
        display = "https://gallery.test/displayimage.php?album=352&pid=1"
        primary = gr._encode_candidate_metadata(
            "https://gallery.test/albums/April%2010/023.png",
            display_url=display,
        )
        aliases = [
            primary,
            "https://gallery.test/albums/April 10/023.png",
            "https://gallery.test/albums/April%2010/thumb_023.png",
        ]
        with tempfile.TemporaryDirectory() as folder, patch.object(
            gr.session, "get", return_value=small
        ) as get, patch.object(
            gr, "extract_all_displayimage_candidates"
        ) as detail:
            result = gr.download_image_candidates(
                aliases, folder, lambda _msg: None, max_attempts=3,
                min_width=1024, min_height=1024,
            )
        self.assertFalse(result)
        get.assert_called_once()
        detail.assert_not_called()

    def test_valid_large_image_remains_complete_and_downloadable(self):
        data = self.image_bytes(1600, 1200, "JPEG")
        response = Mock(status_code=200)
        response.headers = {"Content-Type": "image/jpeg"}
        response.raise_for_status = Mock()
        response.iter_content.return_value = iter(
            [data[:1024], data[1024:]]
        )
        with tempfile.TemporaryDirectory() as folder, patch.object(
            gr.session, "get", return_value=response
        ):
            ok = gr.download_image_candidates(
                ["https://gallery.test/001.jpg"], folder,
                lambda _msg: None, max_attempts=1,
                min_width=1024, min_height=1024,
            )
            self.assertTrue(ok)
            self.assertEqual(Path(folder, "001.jpg").read_bytes(), data)

    def test_missing_original_still_tries_genuinely_distinct_hires_fallback(self):
        original = "https://gallery.test/albums/test/001.jpg"
        display = "https://gallery.test/displayimage.php?album=7&pid=9"
        alternate = "https://gallery.test/albums/hires/001.jpg"
        missing = Mock(status_code=404)
        missing.raise_for_status.side_effect = requests.HTTPError(
            "404 Not Found", response=missing
        )
        large = self.image_response(self.image_bytes(1400, 1400))
        candidate = gr._encode_candidate_metadata(original, display_url=display)
        with tempfile.TemporaryDirectory() as folder, patch.object(
            gr.session, "get", side_effect=[missing, large]
        ) as get, patch.object(
            gr, "extract_all_displayimage_candidates", return_value=[alternate]
        ) as detail:
            success = gr.download_image_candidates(
                [candidate], folder, lambda _msg: None,
                max_attempts=1, min_width=1024, min_height=1024,
            )
            self.assertTrue(success)
            self.assertTrue(Path(folder, "001.jpg").exists())
            self.assertEqual(get.call_count, 2)
        detail.assert_called_once()



class CoppermineNavigationLinkTests(unittest.TestCase):
    """Category navigation/breadcrumbs must not become incomplete children."""

    def test_ancestor_breadcrumb_does_not_mark_complete_gallery_incomplete(self):
        root = "https://gallery.test/photos/index.php"
        television = "https://gallery.test/photos/index.php?cat=3"
        production = "https://gallery.test/photos/index.php?cat=11"
        stills = "https://gallery.test/photos/index.php?cat=128"
        pages = {
            root: '<html><title>Home</title><a href="index.php?cat=3">Television &amp; Web Series</a></html>',
            television: '<html><title>TV</title><a href="index.php?cat=11">Show</a></html>',
            production: '<html><title>Show</title><a href="index.php?cat=128">Episode Stills</a></html>',
            stills: (
                '<html><title>Episode Stills</title>'
                '<a href="index.php?cat=3">Television &amp; Web Series</a>'
                '<a href="thumbnails.php?album=47">Promotional Stills</a></html>'
            ),
        }
        fetched = []
        logs = []

        def fake_fetch(url, cache, **_kwargs):
            fetched.append(url)
            cache[url] = {"html": pages[url], "final_url": url}
            return pages[url], True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch):
            tree = gr.discover_tree(root, page_cache={}, quick_scan=False, log=logs.append)

        self.assertEqual(fetched, [root, television, production, stills])
        self.assertFalse(tree["incomplete"])
        tv_node = tree["children"][0]
        show_node = tv_node["children"][0]
        stills_node = show_node["children"][0]
        self.assertFalse(any(n["incomplete"] for n in (tv_node, show_node, stills_node)))
        self.assertEqual(stills_node["children"], [])
        self.assertEqual([a["name"] for a in stills_node["albums"]], ["Promotional Stills"])
        self.assertTrue(any("Navigation link to visited category" in entry for entry in logs))
        self.assertFalse(any("Unavailable subcategory" in entry for entry in logs))

    def test_backlink_to_previous_sibling_is_not_a_new_child(self):
        root = "https://gallery.test/photos/index.php"
        movies = "https://gallery.test/photos/index.php?cat=2"
        tv = "https://gallery.test/photos/index.php?cat=3"
        earlier = "https://gallery.test/photos/index.php?cat=4"
        pages = {
            root: (
                '<html><title>Home</title>'
                '<a href="index.php?cat=2">Movies</a>'
                '<a href="index.php?cat=3">Television</a></html>'
            ),
            movies: '<html><title>Movies</title><a href="index.php?cat=4">Stills</a></html>',
            earlier: "<html><title>Stills</title></html>",
            tv: (
                '<html><title>TV</title>'
                '<a href="index.php?cat=4">Stills navigation</a></html>'
            ),
        }
        calls = []

        def fake_fetch(url, cache, **_kwargs):
            calls.append(url)
            cache[url] = {"html": pages[url], "final_url": url}
            return pages[url], True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch):
            tree = gr.discover_tree(root, page_cache={}, quick_scan=False)
        self.assertFalse(tree["incomplete"])
        self.assertEqual([n["name"] for n in tree["children"]], ["Movies", "Television"])
        self.assertEqual([n["name"] for n in tree["children"][0]["children"]], ["Stills"])
        self.assertEqual(tree["children"][1]["children"], [])
        self.assertEqual(calls.count(earlier), 1)

    def test_quick_scan_cached_subtree_registers_descendant_as_visited(self):
        root = "https://gallery.test/photos/index.php"
        movies = "https://gallery.test/photos/index.php?cat=2"
        tv = "https://gallery.test/photos/index.php?cat=3"
        stills = "https://gallery.test/photos/index.php?cat=4"
        pages = {
            root: (
                '<html><title>Home</title>'
                '<a href="index.php?cat=2">Movies</a>'
                '<a href="index.php?cat=3">TV</a></html>'
            ),
            movies: '<html><title>Movies</title><a href="index.php?cat=4">Stills</a></html>',
            tv: '<html><title>TV</title><a href="index.php?cat=4">Stills</a></html>',
        }
        cached_stills = {
            "type": "category", "name": "Stills", "url": stills,
            "children": [], "albums": [], "specials": [], "incomplete": False,
        }
        cached_movies = {
            "type": "category", "name": "Movies", "url": movies,
            "child_hash": gr.compute_child_hash([("Stills", stills)], []),
            "children": [cached_stills], "albums": [], "specials": [],
            "incomplete": False,
        }
        requests = []

        def fake_fetch(url, cache, **_kwargs):
            requests.append(url)
            cache[url] = {"html": pages[url], "final_url": url}
            return pages[url], True

        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch):
            tree = gr.discover_tree(
                root, page_cache={}, quick_scan=True,
                cached_nodes={movies: cached_movies},
            )

        self.assertFalse(tree["incomplete"])
        self.assertEqual([c["name"] for c in tree["children"]], ["Movies", "TV"])
        self.assertEqual([c["name"] for c in tree["children"][0]["children"]], ["Stills"])
        self.assertEqual(tree["children"][1]["children"], [])
        self.assertNotIn(stills, requests)

    def test_old_poisoned_cache_tree_rejected_but_page_cache_retained(self):
        root = "https://gallery.test/photos/index.php"
        cached_pages = {root: {"html": "<html></html>"}}
        stale_tree = {
            "type": "category", "name": "Home", "url": root,
            "incomplete": True,
            "children": [
                {"type": "category", "name": "Breadcrumb", "url": root,
                 "incomplete": True, "children": [], "albums": []}
            ],
        }
        with patch.object(gr, "site_cache_path", return_value="cache.json"), patch.object(
            gr.os.path, "exists", return_value=True
        ), patch("builtins.open"), patch.object(
            gr.json, "load", return_value={
                "tree_v": 3, "tree": stale_tree, "pages": cached_pages,
            }
        ):
            loaded_pages, loaded_tree = gr.load_page_cache(root)
        self.assertEqual(loaded_pages, cached_pages)
        self.assertIsNone(loaded_tree)

    def test_revisited_link_never_hides_real_fetch_failure(self):
        root = "https://gallery.test/photos/index.php"
        child = "https://gallery.test/photos/index.php?cat=2"
        missing = "https://gallery.test/photos/index.php?cat=3"
        html = (
            '<html><title>Home</title>'
            '<a href="index.php?cat=2">Movies</a>'
            '<a href="index.php?cat=3">Unavailable</a></html>'
        )
        def fake_fetch(url, cache, **_kwargs):
            if url == missing:
                response = requests.Response()
                response.status_code = 503
                response.url = url
                raise requests.HTTPError("503", response=response)
            contents = html if url == root else (
                '<html><title>Movies</title>'
                '<a href="index.php?cat=2">Movies</a></html>'
            )
            cache[url] = {"html": contents, "final_url": url}
            return contents, True
        with patch.object(gr, "fetch_html_cached", side_effect=fake_fetch):
            tree = gr.discover_tree(root, page_cache={}, quick_scan=False)
        self.assertTrue(tree["incomplete"])
        self.assertEqual(len(tree["children"]), 2)
        self.assertFalse(tree["children"][0]["incomplete"])
        self.assertTrue(tree["children"][1]["incomplete"])
        self.assertEqual(tree["children"][0]["children"], [])



class ImageQualityFilterTests(unittest.TestCase):
    @staticmethod
    def png(width, height):
        assert gr.Image is not None
        output = io.BytesIO()
        gr.Image.new("RGB", (width, height)).save(output, format="PNG")
        return output.getvalue()

    @staticmethod
    def image_response(payload):
        response = Mock()
        response.headers = {"Content-Type": "image/png"}
        response.status_code = 200
        response.iter_content.return_value = [payload]
        return response

    def test_undersized_candidate_uses_larger_fallback(self):
        small = self.image_response(self.png(419, 594))
        large = self.image_response(self.png(1024, 768))
        stats = {"downloaded": 0, "errors": 0, "total_bytes": 0, "total_time": 0.0}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            gr.session, "get", side_effect=[small, large]
        ) as get:
            result = gr.download_image_candidates(
                ["https://gallery.test/014.png", "https://gallery.test/original.png"],
                folder, lambda _line: None, album_stats=stats,
                min_width=512, min_height=512,
            )
            self.assertTrue(result)
            self.assertFalse(Path(folder, "014.png").exists())
            self.assertTrue(Path(folder, "original.png").exists())
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["original.png"])
            self.assertEqual(get.call_count, 2)
        self.assertEqual(stats["downloaded"], 1)
        self.assertEqual(stats.get("skipped_small", 0), 0)

    def test_only_small_image_skips_without_retry_or_partial_file(self):
        small = self.image_response(self.png(419, 594))
        stats = {"downloaded": 0, "errors": 0, "total_bytes": 0, "total_time": 0.0}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            gr.session, "get", return_value=small
        ) as get:
            result = gr.download_image_candidates(
                ["https://gallery.test/014.png"], folder, lambda _line: None,
                album_stats=stats, min_width=512, min_height=512,
            )
            self.assertFalse(result)
            self.assertEqual(list(Path(folder).iterdir()), [])
            get.assert_called_once()
        self.assertEqual(stats["downloaded"], 0)
        self.assertEqual(stats["skipped_small"], 1)
        self.assertEqual(stats["errors"], 0)

    def test_filter_off_preserves_existing_behavior(self):
        small = self.image_response(self.png(419, 594))
        with tempfile.TemporaryDirectory() as folder, patch.object(
            gr.session, "get", return_value=small
        ):
            result = gr.download_image_candidates(
                ["https://gallery.test/014.png"], folder, lambda _line: None
            )
            self.assertTrue(result)
            self.assertTrue(Path(folder, "014.png").exists())

    def test_4chan_undersized_image_does_not_publish_file(self):
        payload = self.png(419, 594)
        response = Mock(status=200, headers={"Content-Type": "image/png"})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = [payload, b""]
        stats = {"errors": 0, "downloaded": 0}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            gr.session.pacer, "wait"
        ), patch.object(
            gr.session.pacer, "record_response"
        ), patch.object(
            gr.urllib.request, "urlopen", return_value=response
        ):
            self.assertFalse(gr.download_4chan_image_oldschool(
                "https://i.4cdn.org/b/14.png", folder, lambda _line: None,
                album_stats=stats, min_width=512, min_height=512,
            ))
            self.assertEqual(list(Path(folder).iterdir()), [])
        self.assertEqual(stats.get("skipped_small"), 1)
        self.assertEqual(stats["errors"], 0)


class CoppermineUITests(unittest.TestCase):
    def test_incomplete_label_does_not_change_download_folder(self):
        app = Mock()
        app.item_to_category = {}
        app.item_to_album = {}
        app.album_url_to_item = {}
        app.show_specials_var.get.return_value = False
        app.tree.insert.side_effect = ["cat-id", "album-id"]
        node = {
            "type": "category", "name": "Movies", "url": "https://site.test/index.php?cat=2",
            "incomplete": True, "specials": [], "children": [],
            "albums": [{
                "type": "album", "name": "Stills", "image_count": "?",
                "url": "https://site.test/thumbnails.php?album=9",
            }],
        }

        gr.GalleryRipperApp.insert_tree_node(app, "", node, [])

        self.assertIn("[incomplete]", app.tree.insert.call_args_list[0].kwargs["text"])
        self.assertEqual(app.item_to_category["cat-id"][2], ["Movies"])
        self.assertEqual(app.item_to_album["album-id"][2], ["Movies", "Stills"])


if __name__ == "__main__":
    unittest.main()
