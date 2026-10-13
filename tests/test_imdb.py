import copy
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import gallery_ripper as gr
import imdb_gallery as imdb


TITLE_URL = "https://www.imdb.com/title/tt0107290/"
NAME_URL = "https://www.imdb.com/name/nm0000554/mediaindex/"
VIEWER_URL = TITLE_URL + "mediaviewer/rm1102560512/"
CDN = "https://m.media-amazon.com/images/M/"


def image(image_id="rm1", asset="asset", **kwargs):
    return dict(id=image_id, url=CDN + asset + "._V1_UX300_CR0,0,300,200_.jpg",
                width=3000, height=2000, caption={"plainText": "Photo"}, **kwargs)


def page(nodes, total, next_page=False, cursor=None, kind="title", **kwargs):
    return {"data": {kind: {
        "id": "tt0107290" if kind == "title" else "nm0000554",
        "titleText" if kind == "title" else "nameText": {"text": "Jurassic Park" if kind == "title" else "Sam Neill"},
        "images": dict(total=total, edges=[{"node": n} for n in nodes],
                       pageInfo={"hasNextPage": next_page, "endCursor": cursor},
                       isPaginationDirty=None, restriction=None, **kwargs),
    }}}


def response(payload=None, status=200, headers=None):
    result = Mock()
    result.status_code = status
    result.headers = headers or {}
    result.json.return_value = payload
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(str(status), response=result)
    return result


class IMDbURLTests(unittest.TestCase):
    def test_adapter_routes_title_name_series_and_viewer(self):
        for url in [TITLE_URL, NAME_URL, VIEWER_URL, "https://m.imdb.com/title/tt0944947/mediaindex/", "http://imdb.com/name/nm0000554/"]:
            with self.subTest(url=url):
                self.assertEqual(gr.select_adapter_for_url(url), "imdb")
        self.assertFalse(imdb.is_imdb_url("https://imdb.com.evil.test/title/tt1/"))
        self.assertFalse(imdb.is_imdb_url("https://notimdb.com/title/tt1/"))
        self.assertEqual(gr.select_adapter_for_url("http://example.test/gallery/"), "coppermine")

    def test_combined_any_and_all_filters(self):
        spec = imdb.parse_gallery_url(TITLE_URL + "mediaindex/?names=nm2,nm1&names=nm2&allNames=nm3&titles=tt2&allTitles=tt3&contentTypes=still_frame&types=poster&galleries=rg1&allGalleries=rg2")
        self.assertEqual(spec["filter"], {
            "nameConstraints": {"anyNameIds": ["nm1", "nm2"], "allNameIds": ["nm3"]},
            "titleConstraints": {"anyTitleIds": ["tt2"], "allTitleIds": ["tt3"]},
            "galleryConstraints": {"anyGalleryIds": ["rg1"], "allGalleryIds": ["rg2"]},
            "types": ["poster", "still_frame"],
        })
        self.assertEqual(imdb.parse_gallery_url(spec["url"])["filter"], spec["filter"])

    def test_legacy_refine_preserves_selection_and_removes_tracking_page(self):
        a = imdb.parse_gallery_url(TITLE_URL + "mediaindex/?refine=nm1,still_frame&ref_=tt_mi&ref=tracking&page=3")
        b = imdb.parse_gallery_url(TITLE_URL + "mediaindex/?names=nm1&types=still_frame")
        self.assertEqual(a["url"], b["url"])
        self.assertEqual(a["filter"], b["filter"])

    def test_invalid_paths_ids_and_unknown_filters_fail_explicitly(self):
        for url in ["https://evil.test/title/tt1/", TITLE_URL + "reviews/",
                    "https://imdb.com/title/nm1/", "https://imdb.com/name/tt1/",
                    TITLE_URL + "mediaviewer/", TITLE_URL + "mediaindex/rm1/",
                    TITLE_URL + "mediaindex/?names=tt1", TITLE_URL + "mediaindex/?titles=nm1",
                    TITLE_URL + "mediaindex/?types=../../evil", TITLE_URL + "mediaindex/?unknown=1"]:
            with self.subTest(url=url), self.assertRaises(imdb.IMDbError):
                imdb.parse_gallery_url(url)

    def test_original_url_keeps_asset_id_and_format(self):
        for extension in ["jpg", "jpeg", "png", "webp", "gif"]:
            url = CDN + "MV5Bwith@@._V1_QL75_UX300_CR0,0,300,200_." + extension + "?foo=1#test"
            self.assertEqual(imdb.original_image_url(url), CDN + "MV5Bwith@@._V1_." + extension)
        original = CDN + "MV5Basset._V1_.jpg"
        self.assertEqual(imdb.original_image_url(original), original)
        self.assertEqual(imdb.original_image_url(CDN + "unmodified.jpg"), CDN + "unmodified.jpg")

    def test_non_cdn_assets_are_rejected(self):
        for url in [None, "https://m.media-amazon.com.evil.test/images/M/asset.jpg",
                    "https://m.media-amazon.com/images/G/logo.jpg", CDN + "asset.mp4"]:
            with self.subTest(url=url), self.assertRaises(imdb.IMDbError):
                imdb.original_image_url(url)


class IMDbPaginationTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.cache = {}

    def load(self, url=TITLE_URL, **kwargs):
        return imdb.load_gallery(url, self.session, self.cache, **kwargs)

    def test_all_pages_and_filters_are_sent_with_required_headers(self):
        self.session.post.side_effect = [response(page([image()], 3, True, "one")),
                                         response(page([image("rm2", "two"), image("rm3", "three")], 3))]
        url = TITLE_URL + "mediaindex/?names=nm0000554&types=still_frame"
        result = self.load(url)
        self.assertEqual([n["id"] for n in result["images"]], ["rm1", "rm2", "rm3"])
        first, second = self.session.post.call_args_list
        self.assertEqual(first.args, (imdb.API_URL,))
        self.assertEqual(first.kwargs["headers"]["Origin"], "https://www.imdb.com")
        self.assertEqual(first.kwargs["headers"]["Referer"], "https://www.imdb.com/")
        self.assertIsNone(first.kwargs["json"]["variables"]["after"])
        self.assertEqual(second.kwargs["json"]["variables"]["after"], "one")
        self.assertEqual(first.kwargs["json"]["variables"]["filter"], second.kwargs["json"]["variables"]["filter"])
        self.assertEqual(len(self.cache), 1)

    def test_name_uses_correct_filter_type_and_label(self):
        self.session.post.return_value = response(page([image()], 1, kind="name"))
        result = self.load(NAME_URL)
        query = self.session.post.call_args.kwargs["json"]["query"]
        self.assertIn("$filter:NameImagesFilter", query)
        self.assertIn("name(id:$id)", query)
        self.assertIn("nameText", query)
        self.assertEqual(result["label"], "Sam Neill")

    def test_empty_gallery_is_valid_only_with_zero_total_and_final_page(self):
        self.session.post.return_value = response(page([], 0))
        self.assertEqual(self.load()["images"], [])

    def test_overlap_across_pages_is_deduplicated(self):
        self.session.post.side_effect = [response(page([image()], 2, True, "one")),
                                         response(page([image(), image("rm2", "two")], 2))]
        self.assertEqual(len(self.load()["images"]), 2)

    def test_repeating_cursor_fails_without_partial_cache(self):
        self.session.post.side_effect = [response(page([image()], 3, True, "one")),
                                         response(page([image("rm2", "two")], 3, True, "one"))]
        with self.assertRaisesRegex(imdb.IMDbError, "did not advance"):
            self.load()
        self.assertEqual(self.cache, {})
        self.assertEqual(self.session.post.call_count, 2)

    def test_changing_cursor_without_new_images_does_not_loop(self):
        self.session.post.side_effect = [response(page([image()], 3, True, "one")),
                                         response(page([image()], 3, True, "two"))]
        with self.assertRaisesRegex(imdb.IMDbError, "did not advance"):
            self.load()
        self.assertEqual(self.session.post.call_count, 2)
        self.assertEqual(self.cache, {})

    def test_more_images_than_reported_total_fails(self):
        self.session.post.return_value = response(page([image(), image("rm2", "two")], 1))
        with self.assertRaisesRegex(imdb.IMDbError, "more images"):
            self.load()
        self.assertEqual(self.cache, {})

    def test_missing_cursor_and_empty_continuation_fail(self):
        for payload in [page([image()], 2, True), page([], 2, True, "one")]:
            with self.subTest(payload=payload):
                self.session.post.side_effect = None
                self.session.post.return_value = response(payload)
                with self.assertRaisesRegex(imdb.IMDbError, "did not advance"):
                    self.load()
                self.assertEqual(self.cache, {})

    def test_truncated_last_page_is_not_success(self):
        self.session.post.return_value = response(page([image()], 2))
        with self.assertRaisesRegex(imdb.IMDbError, "reports 2"):
            self.load()
        self.assertEqual(self.cache, {})

    def test_changed_gallery_does_not_replace_previous_complete_cache(self):
        self.session.post.return_value = response(page([image()], 1))
        self.load()
        saved = copy.deepcopy(self.cache)
        self.session.post.side_effect = [response(page([image()], 3, True, "one")),
                                         response(page([image("rm2", "two")], 4))]
        with self.assertRaisesRegex(imdb.IMDbError, "changed during pagination"):
            self.load(quick_scan=False)
        self.assertEqual(self.cache, saved)

    def test_dirty_and_restricted_galleries_fail(self):
        for field, value in [("isPaginationDirty", True), ("restriction", {"reasons": ["RESTRICTED"], "unrestrictedTotal": 4})]:
            payload = page([image()], 1)
            payload["data"]["title"]["images"][field] = value
            self.session.post.return_value = response(payload)
            with self.subTest(field=field), self.assertRaises(imdb.IMDbError):
                self.load()
            self.assertEqual(self.cache, {})

    def test_malformed_response_never_becomes_empty_gallery(self):
        payloads = [[], {}, {"data": {}}, {"data": {"title": None}},
                    {"data": {"title": {"id": "tt2"}}}, {"errors": [{"message": "bad filter"}]},
                    page([dict(id="rm1", url="https://evil.test/p.jpg")], 1)]
        for payload in payloads:
            self.session.post.return_value = response(payload)
            with self.subTest(payload=payload), self.assertRaises(imdb.IMDbError):
                self.load()
            self.assertEqual(self.cache, {})

    def test_non_json_challenge_fails(self):
        self.session.post.return_value = response()
        self.session.post.return_value.json.side_effect = ValueError("html")
        with self.assertRaisesRegex(imdb.IMDbError, "non-JSON"):
            self.load()

    def test_cache_expiry_refresh_and_selection_isolation(self):
        self.session.post.return_value = response(page([image()], 1))
        with patch.object(imdb.time, "time", return_value=1000):
            self.load()
            self.load()
        self.assertEqual(self.session.post.call_count, 1)
        with patch.object(imdb.time, "time", return_value=1301):
            self.load()
        self.assertEqual(self.session.post.call_count, 2)
        self.load(TITLE_URL + "mediaindex/?types=poster")
        self.assertEqual(self.session.post.call_count, 3)
        self.load(quick_scan=False)
        self.assertEqual(self.session.post.call_count, 4)

    def test_block_statuses_do_not_retry_or_return_zero(self):
        for status in [202, 401, 403]:
            self.session.post.reset_mock()
            self.session.post.return_value = response(status=status)
            with self.subTest(status=status), self.assertRaisesRegex(imdb.IMDbError, "refused gallery access"):
                self.load()
            self.assertEqual(self.session.post.call_count, 1)
            self.assertEqual(self.cache, {})

    def test_transient_failure_retries_and_closes_response(self):
        failed = response(status=429, headers={"Retry-After": "2"})
        self.session.post.side_effect = [failed, response(page([image()], 1))]
        with patch.object(imdb.time, "sleep") as sleep:
            self.load()
        sleep.assert_called_once_with(2)
        failed.__exit__.assert_called_once()

    def test_stop_before_request_and_during_retry(self):
        flag = threading.Event()
        flag.set()
        with self.assertRaisesRegex(imdb.IMDbError, "stopped"):
            self.load(stop_flag=flag)
        self.session.post.assert_not_called()
        flag = Mock()
        flag.is_set.return_value = False
        flag.wait.return_value = True
        self.session.post.return_value = response(status=503)
        with self.assertRaisesRegex(imdb.IMDbError, "stopped"):
            self.load(stop_flag=flag)
        self.assertEqual(self.session.post.call_count, 1)

    def test_viewer_resolves_exact_photo_and_exposes_both_choices(self):
        self.session.post.side_effect = [response(page([image()], 1)),
                                         response({"data": {"image": image("rm1102560512", "viewer")}})]
        tree = imdb.discover_tree(VIEWER_URL, self.session, self.cache)
        self.assertEqual(tree["adapter"], "imdb")
        selected, all_photos = tree["albums"]
        self.assertEqual(selected["url"], VIEWER_URL)
        self.assertNotEqual(selected["path"], all_photos["path"])
        entries = imdb.image_entries(selected["url"], self.session, self.cache)
        self.assertEqual(entries[0][1], [CDN + "viewer._V1_.jpg"])
        self.assertEqual(self.session.post.call_count, 2)

    def test_viewer_rejects_wrong_image_id(self):
        self.session.post.return_value = response({"data": {"image": image()}})
        with self.assertRaisesRegex(imdb.IMDbError, "different image"):
            imdb.load_image(VIEWER_URL, self.session, self.cache)

    def test_entries_deduplicate_assets_and_only_offer_originals(self):
        self.session.post.return_value = response(page([image(), image("rm2", "asset")], 2))
        entries = imdb.image_entries(TITLE_URL, self.session, self.cache)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1], [CDN + "asset._V1_.jpg"])
        tree = imdb.discover_tree(TITLE_URL, self.session, self.cache)
        self.assertEqual(tree["albums"][0]["image_count"], 1)


class IMDbIntegrationTests(unittest.TestCase):
    def test_interrupted_download_retries_without_retaining_partial_file(self):
        def interrupted():
            yield b"partial"
            raise requests.ConnectionError("stream interrupted")

        first = response(headers={"Content-Type": "image/jpeg"})
        first.iter_content.side_effect = interrupted
        second = response(headers={"Content-Type": "image/jpeg"})
        second.iter_content.return_value = [b"complete"]
        with tempfile.TemporaryDirectory() as folder, patch.object(gr.session, "get", side_effect=[first, second]) as get, patch.object(gr, "rate_limiter_for_url", return_value=Mock()), patch.object(gr.time, "sleep"):
            self.assertTrue(gr.download_image_candidates([CDN + "asset._V1_.jpg"], folder, lambda msg: None, max_attempts=2))
            self.assertEqual(Path(folder, "asset._V1_.jpg").read_bytes(), b"complete")
            self.assertEqual(len(list(Path(folder).iterdir())), 1)
            self.assertEqual(get.call_count, 2)
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_permanent_stream_failure_leaves_no_completed_file(self):
        def interrupted():
            yield b"partial"
            raise requests.ConnectionError("stream interrupted")

        download = response(headers={"Content-Type": "image/jpeg"})
        download.iter_content.side_effect = interrupted
        with tempfile.TemporaryDirectory() as folder, patch.object(gr.session, "get", return_value=download), patch.object(gr, "rate_limiter_for_url", return_value=Mock()):
            self.assertFalse(gr.download_image_candidates([CDN + "asset._V1_.jpg"], folder, lambda msg: None, max_attempts=1))
            self.assertEqual(list(Path(folder).iterdir()), [])
        download.close.assert_called_once()

    def test_existing_original_is_skipped_without_http_request(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(gr.session, "get") as get:
            Path(folder, "asset._V1_.jpg").write_bytes(b"previous original")
            self.assertFalse(gr.download_image_candidates([CDN + "asset._V1_.jpg"], folder, lambda msg: None))
            get.assert_not_called()

    def test_empty_media_response_is_not_saved_as_an_original(self):
        download = response(headers={"Content-Type": "image/jpeg"})
        download.iter_content.return_value = []
        with tempfile.TemporaryDirectory() as folder, patch.object(gr.session, "get", return_value=download), patch.object(gr, "rate_limiter_for_url", return_value=Mock()):
            self.assertFalse(gr.download_image_candidates([CDN + "asset._V1_.jpg"], folder, lambda msg: None, max_attempts=1))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_discovery_uses_imdb_adapter_and_persists_cache(self):
        with patch.object(gr, "load_page_cache", return_value=({}, None)), patch.object(gr, "save_page_cache") as save, patch.object(gr.session, "post", return_value=response(page([image()], 1))), patch.object(gr, "discover_tree") as coppermine:
            tree = gr.discover_or_load_gallery_tree(TITLE_URL, lambda msg: None)
        self.assertEqual(tree["albums"][0]["image_count"], 1)
        self.assertIn("imdb", next(iter(save.call_args.args[2].values())))
        coppermine.assert_not_called()

    def test_batch_download_uses_imdb_entries_and_exact_response_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            photo = image()
            # Real JPEG bytes ensure the common downloader accepts the media
            # response and writes it verbatim without image conversion.
            stream = io.BytesIO()
            gr.Image.new("RGB", (3, 2), "red").save(stream, format="JPEG")
            media = stream.getvalue()
            download = response(status=200, headers={"Content-Type": "image/jpeg"})
            download.iter_content.return_value = [media]
            selected = [("All photos", TITLE_URL + "mediaindex/", ["IMDb", "Jurassic Park"])]
            with patch.object(gr, "load_page_cache", return_value=({}, None)), patch.object(gr, "save_page_cache"), patch.object(gr.session, "post", return_value=response(page([photo], 1))), patch.object(gr.session, "get", return_value=download) as get, patch.object(gr.image_rate_limiter, "wait"), patch.object(gr, "get_all_candidate_images_from_album") as coppermine:
                gr.rip_galleries(selected, folder, lambda msg: None, TITLE_URL)
            saved = Path(folder, "IMDb", "Jurassic Park", "asset._V1_.jpg")
            self.assertEqual(saved.read_bytes(), media)
            self.assertEqual(get.call_args.args[0], CDN + "asset._V1_.jpg")
            self.assertEqual(get.call_args.kwargs["headers"]["Referer"], TITLE_URL + "mediaindex/")
            coppermine.assert_not_called()


if __name__ == "__main__":
    unittest.main()
