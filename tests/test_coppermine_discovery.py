import unittest
from unittest.mock import ANY, Mock, mock_open, patch

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

    def test_fresh_cached_page_skips_head_probe(self):
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
        ) as head:
            html, changed = gr.fetch_html_cached(
                "http://example.test/gallery", cache, quick_scan=True
            )

        self.assertFalse(changed)
        self.assertIn("Cached", html)
        head.assert_not_called()

    def test_fresh_legacy_cache_backfills_effective_url(self):
        url = "http://example.test/gallery"
        response = self._response(200, url="http://example.test/gallery/")
        cache = {
            url: {
                "html": "<html><title>Cached</title></html>",
                "timestamp": 995.0,
            }
        }

        with patch.object(gr.time, "time", return_value=1000.0), patch.object(
            gr.session, "head", return_value=response
        ) as head:
            html, changed = gr.fetch_html_cached(url, cache, quick_scan=True)

        self.assertFalse(changed)
        self.assertIn("Cached", html)
        head.assert_called_once()
        self.assertEqual(cache[url]["final_url"], "http://example.test/gallery/")

    def test_failed_quick_head_does_not_replace_cached_effective_url(self):
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
            gr.session, "head", return_value=response
        ), patch.object(gr.time, "sleep"):
            html, changed = gr.fetch_html_cached(url, cache, quick_scan=True)

        self.assertFalse(changed)
        self.assertIn("Cached", html)
        self.assertEqual(cache[url]["final_url"], "http://example.test/gallery/")

    def test_failed_legacy_head_does_not_store_error_url(self):
        url = "http://example.test/gallery"
        response = self._response(503, url="http://example.test/login")
        cache = {url: {"html": "<html><title>Cached</title></html>"}}

        with patch.object(gr.session, "head", return_value=response), patch.object(
            gr.time, "sleep"
        ):
            html, changed = gr.fetch_html_cached(url, cache, quick_scan=False)

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

    def test_stale_cached_page_refreshes_when_head_validator_changes(self):
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
        head = self._response(200, url=url, headers={"ETag": "new"})
        get = self._response(200, url=url, text=new_html, headers={"ETag": "new"})

        with patch.object(gr.time, "time", return_value=1000.0), patch.object(
            gr.session, "head", return_value=head
        ), patch.object(gr.session, "get", return_value=get):
            html, changed = gr.fetch_html_cached(url, cache, quick_scan=True)

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
        output_dir = "out"
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
        limiter = Mock()

        with patch.object(gr.os.path, "exists", return_value=False), patch(
            "builtins.open", mock_open()
        ), patch.object(
            gr, "rate_limiter_for_url", return_value=limiter
        ), patch.object(
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

        self.assertTrue(downloaded)
        resolve_detail.assert_called_once_with(
            display,
            ANY,
            referer="http://example.test/gallery/thumbnails.php?album=5",
        )

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
