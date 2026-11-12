"""Archived.moe recovery and live-4chan folder compatibility regressions."""
import base64
import hashlib
import json
import tempfile
import requests
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import archived_moe as am
import gallery_ripper as gr


THREAD_ID = "956262976"
ARCHIVE_URL = f"https://archived.moe/b/thread/{THREAD_ID}/#{THREAD_ID}"
CANONICAL = f"archived.moe:b/{THREAD_ID}"


def make_thread():
    return {
        THREAD_ID: {
            "op": {
                "num": THREAD_ID, "title": "A &amp; B",
                "media": {
                    "media_filename": "original.jpg",
                    "media_orig": "1000000000001.jpg",
                    "media_link": "https://archived.moe/b/redirect/1000000000001.jpg",
                    "thumb_link": "https://archived.moe/b/thumb/1000000000001s.jpg",
                },
            },
            "posts": {
                "956262978": {
                    "num": "956262978",
                    "media": {
                        "media_filename": "other.webm",
                        "media_orig": "1000000000002.webm",
                        "media_link": None,
                        "remote_media_link": "https://thebarchive.com/b/full_image/1000000000002.webm",
                    },
                },
                "956262980": {
                    "num": "956262980",
                    "media": {"media_filename": "lost.png", "thumb_link": "https://archived.moe/b/thumb/x.jpg"},
                },
                "956262981": {"num": "956262981", "comment": "Text only"},
            },
        }
    }


class ArchiveMetadataTests(unittest.TestCase):
    def test_archive_url_routes_to_4chan_but_preserves_source(self):
        self.assertEqual(am.parse_thread_url(ARCHIVE_URL), ("b", THREAD_ID))
        self.assertEqual(am.parse_thread_url(CANONICAL), ("b", THREAD_ID))
        self.assertEqual(gr.select_adapter_for_url(ARCHIVE_URL), "4chan")
        self.assertEqual(gr.select_adapter_for_url(CANONICAL), "4chan")
        self.assertEqual(gr.normalize_fourchan_url(ARCHIVE_URL), CANONICAL)
        self.assertEqual(gr.normalize_fourchan_url(CANONICAL), CANONICAL)
        self.assertEqual(
            am.parse_thread_url(f"https://thebarchive.com/b/thread/{THREAD_ID}/"),
            ("b", THREAD_ID),
        )
        self.assertEqual(
            gr.normalize_fourchan_url(f"https://thebarchive.com/b/thread/{THREAD_ID}/"),
            CANONICAL,
        )
        self.assertEqual(
            gr.fourchan_thread_identity(f"https://boards.4chan.org/b/thread/{THREAD_ID}"),
            ("b", THREAD_ID),
        )
        self.assertEqual(
            gr.normalize_fourchan_url(f"https://boards.4chan.org/b/thread/{THREAD_ID}"),
            f"4chan:b/{THREAD_ID}",
        )
        for url in (
            f"https://archived.moe.evil.test/b/thread/{THREAD_ID}",
            f"https://notarchived.moe/b/thread/{THREAD_ID}",
            "https://archived.moe/b/search/text/word/",
            "https://archived.moe/b/thread/not-an-id/",
        ):
            self.assertIsNone(am.parse_thread_url(url))

    def test_archived_api_origins_names_and_unavailable_media(self):
        title, entries, unavailable = am.extract_thread(make_thread(), "b", THREAD_ID)
        self.assertEqual(title, "A & B")
        self.assertEqual([name for name, _, _ in entries], ["original.jpg", "other.webm"])
        self.assertEqual(unavailable, 1)
        first_urls = entries[0][1]
        self.assertEqual(first_urls[0], "https://archived.moe/b/redirect/1000000000001.jpg")
        self.assertIn("https://thebarchive.com/b/full_image/1000000000001.jpg", first_urls)
        self.assertIn("https://i.4cdn.org/b/1000000000001.jpg", first_urls)
        self.assertNotIn("thumb", " ".join(first_urls).lower())
        self.assertEqual(entries[1][1][0], "https://thebarchive.com/b/full_image/1000000000002.webm")
        self.assertIn("https://thebarchive.com/b/full_image/1000000000002.web", entries[1][1])
        self.assertEqual(entries[1][2], None)

    def test_archive_metadata_exposes_original_size_and_md5(self):
        data = make_thread()
        body = b"original media content"
        first = data[THREAD_ID]["op"]["media"]
        first["media_size"] = str(len(body))
        first["media_hash"] = base64.b64encode(hashlib.md5(body).digest()).decode("ascii")
        _, entries, _ = am.extract_thread(data, "b", THREAD_ID)
        self.assertEqual(
            entries[0][2],
            {"size": len(body), "md5": hashlib.md5(body).digest()},
        )

    def test_original_filenames_do_not_escape_download_dir_or_collide(self):
        data = make_thread()
        data[THREAD_ID]["posts"] = {
            "11": {
                "num": "11",
                "media": {
                    "media_filename": "../../original.jpg",
                    "media_orig": "1000000000003.jpg",
                    "media_link": "/b/redirect/1000000000003.jpg",
                },
            },
            "12": {
                "num": "12",
                "media": {
                    "media_filename": "CON.png",
                    "media_orig": "1000000000004.png",
                },
            },
        }
        _, entries, unavailable = am.extract_thread(data, "b", THREAD_ID)
        self.assertEqual(unavailable, 0)
        self.assertEqual(
            [name for name, _, _ in entries],
            ["original.jpg", "original_1000000000003.jpg", "_CON.png"],
        )
        self.assertEqual(entries[1][1][0], "https://archived.moe/b/redirect/1000000000003.jpg")

    def test_malformed_api_does_not_look_like_empty_thread(self):
        for data in ({}, [], {THREAD_ID: {"posts": {}}}):
            with self.assertRaises(ValueError):
                am.extract_thread(data, "b", THREAD_ID)

    def test_discovery_uses_archive_api_and_4chan_folder_layout(self):
        response = Mock()
        response.json.return_value = make_thread()
        messages = []
        with patch.object(gr, "_discovery_request", return_value=response) as get:
            tree = gr.fourchan_discover_tree(ARCHIVE_URL, log=messages.append)
        get.assert_called_once()
        self.assertEqual(get.call_args.args[1], am.thread_api_url("b", THREAD_ID))
        response.close.assert_called_once()
        album = tree["albums"][0]
        self.assertEqual(album["url"], CANONICAL)
        self.assertEqual(album["image_count"], 2)
        self.assertEqual(album["path"], ["4chan", "b", f"A & B ({THREAD_ID})"])
        self.assertTrue(any("1 attachment(s)" in message for message in messages))

    def test_api_http_errors_propagate_instead_of_reporting_zero_files(self):
        response = Mock()
        response.raise_for_status.side_effect = RuntimeError("HTTP 403")
        with patch.object(gr, "_discovery_request", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "403"):
                gr.fourchan_discover_tree(ARCHIVE_URL)

    def test_only_thumb_metadata_never_becomes_an_original_candidate(self):
        data = make_thread()
        data[THREAD_ID]["op"]["media"] = {
            "media_filename": "still.jpg",
            "media_link": "/b/thumb/x.jpg",
            "thumb_link": "/b/thumb/y.jpg",
        }
        data[THREAD_ID]["posts"] = {}
        _, entries, unavailable = am.extract_thread(data, "b", THREAD_ID)
        self.assertEqual(entries, [])
        self.assertEqual(unavailable, 1)


class ArchiveDownloadTests(unittest.TestCase):
    def test_archive_reuses_one_existing_live_thread_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory, "4chan", "b")
            existing = parent / f"Original live subject {THREAD_ID}"
            existing.mkdir(parents=True)
            path = ["4chan", "b", f"Different archive subject ({THREAD_ID})"]
            self.assertEqual(
                Path(gr.fourchan_output_directory(directory, CANONICAL, path)),
                existing,
            )
            self.assertEqual(
                Path(gr.fourchan_output_directory(directory, f"4chan:b/{THREAD_ID}", path)),
                parent / gr.sanitize_name(path[-1]),
            )
            (parent / f"Second copy ({THREAD_ID})").mkdir()
            self.assertEqual(
                Path(gr.fourchan_output_directory(directory, CANONICAL, path)),
                parent / gr.sanitize_name(path[-1]),
            )

    def test_archive_skips_old_files_and_adds_missing_into_same_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory, "4chan", "b", f"Live original {THREAD_ID}")
            folder.mkdir(parents=True)
            (folder / "original.jpg").write_bytes(b"old and correct")
            entries = [
                ("original.jpg", ["https://i.4cdn.org/b/1.jpg"], None),
                ("other.webm", ["https://thebarchive.com/b/full_image/2.webm"], None),
            ]
            downloaded = []
            def fake_download(url, outdir, log, fname, _index, _total, stats, *_args, **_kwargs):
                downloaded.append((url, outdir, fname))
                Path(outdir, fname).write_bytes(b"new original bytes")
                stats["total_bytes"] += len(b"new original bytes")
                stats["downloaded"] += 1
                return True

            with patch.object(gr, "archived_moe_thread_images", return_value=("Archive", entries)), patch.object(
                gr, "download_4chan_image_oldschool", side_effect=fake_download
            ):
                gr._rip_galleries_impl(
                    [("Archived thread", CANONICAL, ["4chan", "b", f"Archive ({THREAD_ID})"])],
                    directory, lambda _message: None, ARCHIVE_URL, workers=1,
                )
            self.assertEqual((folder / "original.jpg").read_bytes(), b"old and correct")
            self.assertEqual((folder / "other.webm").read_bytes(), b"new original bytes")
            self.assertEqual(
                downloaded,
                [("https://thebarchive.com/b/full_image/2.webm", str(folder), "other.webm")],
            )
            self.assertFalse(Path(directory, "4chan", "b", f"Archive {THREAD_ID}").exists())

    def test_archive_attempts_next_original_and_counts_once(self):
        stats = {"total_bytes": 0, "total_time": 0.0, "downloaded": 0, "errors": 0}
        urls = ["https://archived.moe/b/redirect/1.jpg", "https://i.4cdn.org/b/1.jpg"]
        with tempfile.TemporaryDirectory() as directory, patch.object(
            gr, "download_4chan_image_oldschool", side_effect=[False, False]
        ) as fetch:
            result = gr.download_4chan_archive_candidates(
                urls, directory, lambda _msg: None, "file.jpg", 1, 1, stats,
            )
        self.assertFalse(result)
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(all(call.kwargs["require_original"] for call in fetch.call_args_list))

    def test_archive_does_not_write_html_error_or_thumbnail_redirect(self):
        for headers, final_url in (
            ({"Content-Type": "text/html"}, "https://archived.moe/error"),
            ({"Content-Type": "image/jpeg"}, "https://archived.moe/b/thumb/1.jpg"),
        ):
            response = Mock(status=200, headers=headers)
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.geturl.return_value = final_url
            response.read.side_effect = [b"<html>wrong media</html>", b""]
            with tempfile.TemporaryDirectory() as directory, patch.object(
                gr.urllib.request, "urlopen", return_value=response
            ), patch.object(gr.session.pacer, "wait"), patch.object(
                gr.session.pacer, "record_response"
            ), patch.object(gr.time, "sleep"):
                self.assertFalse(gr.download_4chan_image_oldschool(
                    "https://archived.moe/b/redirect/1.jpg", directory,
                    lambda _msg: None, fname="file.jpg", max_attempts=1,
                    require_original=True,
                ))
                self.assertFalse(Path(directory, "file.jpg").exists())

    def test_archive_verifies_original_bytes_before_publication(self):
        payload = b"the genuine original bytes"
        good_hash = hashlib.md5(payload).digest()
        cases = (
            ({"size": len(payload), "md5": good_hash}, True),
            ({"size": len(payload) + 1, "md5": good_hash}, False),
            ({"size": len(payload), "md5": bytes(16)}, False),
        )
        for expected, should_succeed in cases:
            with self.subTest(expected=expected):
                response = Mock(status=200, headers={"Content-Type": "image/jpeg"})
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                response.geturl.return_value = "https://thebarchive.com/b/full_image/1.jpg"
                response.read.side_effect = [payload, b""]
                with tempfile.TemporaryDirectory() as directory, patch.object(
                    gr.urllib.request, "urlopen", return_value=response
                ), patch.object(gr.session.pacer, "wait"), patch.object(
                    gr.session.pacer, "record_response"
                ), patch.object(gr.time, "sleep"):
                    success = gr.download_4chan_image_oldschool(
                        "https://thebarchive.com/b/full_image/1.jpg",
                        directory, lambda _msg: None, fname="file.jpg",
                        max_attempts=1, require_original=True, expected=expected,
                    )
                    self.assertIs(success, should_succeed)
                    path = Path(directory, "file.jpg")
                    self.assertEqual(path.exists(), should_succeed)
                    if should_succeed:
                        self.assertEqual(path.read_bytes(), payload)
                    self.assertFalse(list(Path(directory).glob("*.part")))

    def test_archive_detects_html_payload_without_content_type(self):
        response = Mock(status=200, headers={})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.geturl.return_value = "https://example.test/full_image/1.jpg"
        response.read.side_effect = [b"<!DOCTYPE html><html>blocked</html>", b""]
        with tempfile.TemporaryDirectory() as directory, patch.object(
            gr.urllib.request, "urlopen", return_value=response
        ), patch.object(gr.session.pacer, "wait"), patch.object(
            gr.session.pacer, "record_response"
        ), patch.object(gr.time, "sleep"):
            self.assertFalse(gr.download_4chan_image_oldschool(
                "https://example.test/full_image/1.jpg", directory,
                lambda _msg: None, fname="file.jpg", max_attempts=1,
                require_original=True,
            ))
            self.assertFalse(Path(directory, "file.jpg").exists())



class ArchiveBrowserFallbackTests(unittest.TestCase):
    def test_403_prompts_for_browser_metadata_instead_of_empty_album(self):
        import requests
        api_url = am.thread_api_url("b", THREAD_ID)
        denied = Mock(status_code=403)
        denied.raise_for_status.side_effect = requests.HTTPError(
            "403 Client Error", response=denied
        )
        with patch.object(gr, "_discovery_request", return_value=denied):
            with self.assertRaises(am.ArchiveAccessBlockedError) as raised:
                gr.fourchan_discover_tree(ARCHIVE_URL)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.api_url, api_url)
        self.assertEqual(denied.close.call_count, 2)

    def test_json_file_import_validates_thread_and_board(self):
        data = make_thread()
        data[THREAD_ID]["op"]["board"] = {"shortname": "b"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "saved.json")
            path.write_text(json.dumps(data), encoding="utf-8-sig")
            self.assertEqual(
                am.load_saved_thread_json(path, "b", THREAD_ID), data
            )
            with self.assertRaisesRegex(ValueError, r"does not contain thread"):
                am.load_saved_thread_json(path, "b", "123456789")
            with self.assertRaisesRegex(ValueError, r"not /g/"):
                am.load_saved_thread_json(path, "g", THREAD_ID)
            path.write_text("<!DOCTYPE html>Cloudflare challenge", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not valid"):
                am.load_saved_thread_json(path, "b", THREAD_ID)
            with patch.object(am, "MAX_SAVED_THREAD_JSON_BYTES", 10):
                with self.assertRaisesRegex(ValueError, "64 MiB"):
                    am.load_saved_thread_json(path, "b", THREAD_ID)

    def test_imported_api_data_bypasses_network_discovery(self):
        data = make_thread()
        with patch.object(gr, "_discovery_request") as request:
            tree = gr.discover_or_load_gallery_tree(
                ARCHIVE_URL, lambda _message: None, archive_data=data,
            )
        request.assert_not_called()
        self.assertEqual(tree["albums"][0]["url"], CANONICAL)
        self.assertEqual(tree["albums"][0]["image_count"], 2)

    def test_download_uses_imported_snapshot_without_reaching_metadata_api(self):
        data = make_thread()
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory, "4chan", "b", f"Live name {THREAD_ID}")
            existing.mkdir(parents=True)
            (existing / "original.jpg").write_bytes(b"already here")
            with patch.object(gr, "_discovery_request") as request, patch.object(
                gr, "download_4chan_image_oldschool", return_value=False
            ) as downloader, patch.object(gr.time, "sleep"):
                gr._rip_galleries_impl(
                    [("From browser JSON", CANONICAL, ["4chan", "b", f"Archived ({THREAD_ID})"])],
                    directory, lambda _msg: None, ARCHIVE_URL,
                    workers=1, archive_data=data,
                )
            request.assert_not_called()
            self.assertEqual((existing / "original.jpg").read_bytes(), b"already here")
            self.assertFalse(Path(directory, "4chan", "b", f"Archived {THREAD_ID}").exists())
            self.assertGreaterEqual(downloader.call_count, 1)
            self.assertTrue(all(call.kwargs.get("require_original") for call in downloader.call_args_list))

    def test_import_button_retains_same_thread_snapshot_and_starts_discovery(self):
        data = make_thread()
        app = Mock()
        app.url_var.get.return_value = ARCHIVE_URL
        app.discovery_thread = None
        app.download_thread = None
        app._archive_snapshot = None
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "saved.json")
            path.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(gr.filedialog, "askopenfilename", return_value=str(path)), patch.object(
                gr.messagebox, "showerror"
            ) as showerror:
                gr.GalleryRipperApp.import_archived_moe_json(app)
            showerror.assert_not_called()
        self.assertEqual(app._archive_snapshot[0], ("b", THREAD_ID))
        self.assertEqual(app._archive_snapshot[1], data)
        app.discover_albums.assert_called_once()
        self.assertIs(
            gr.GalleryRipperApp._archive_data_for_url(app, ARCHIVE_URL),
            app._archive_snapshot[1],
        )
        self.assertIsNone(
            gr.GalleryRipperApp._archive_data_for_url(
                app, "https://archived.moe/b/thread/956262977"
            )
        )

    def test_rejected_import_does_not_replace_existing_snapshot(self):
        app = Mock()
        app.url_var.get.return_value = ARCHIVE_URL
        app.discovery_thread = None
        app.download_thread = None
        prior = (("b", THREAD_ID), make_thread())
        app._archive_snapshot = prior
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "bad.json")
            path.write_text('{"something":"else"}', encoding="utf-8")
            with patch.object(gr.filedialog, "askopenfilename", return_value=str(path)), patch.object(
                gr.messagebox, "showerror"
            ) as showerror:
                gr.GalleryRipperApp.import_archived_moe_json(app)
            showerror.assert_called_once()
        self.assertIs(app._archive_snapshot, prior)
        app.discover_albums.assert_not_called()

    def test_403_dialog_offers_exact_browser_api_url(self):
        app = Mock()
        error = am.ArchiveAccessBlockedError(
            am.thread_api_url("b", THREAD_ID), 403
        )
        with patch.object(gr.messagebox, "askyesno", return_value=True) as dialog, patch.object(
            gr.webbrowser, "open"
        ) as browser:
            gr.GalleryRipperApp.handle_archived_moe_blocked(app, error)
        browser.assert_called_once_with(am.thread_api_url("b", THREAD_ID))
        self.assertIn("Import saved thread JSON", dialog.call_args.args[1])

    def test_archive_import_accepts_original_live_4chan_url(self):
        data = make_thread()
        app = Mock()
        live_url = f"https://boards.4chan.org/b/thread/{THREAD_ID}"
        app.url_var.get.return_value = live_url
        app.discovery_thread = None
        app.download_thread = None
        app._archive_snapshot = None
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "archive.json")
            path.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(gr.filedialog, "askopenfilename", return_value=str(path)):
                gr.GalleryRipperApp.import_archived_moe_json(app)
        self.assertEqual(app._archive_snapshot, (("b", THREAD_ID), data))
        self.assertEqual(
            gr.GalleryRipperApp._archive_data_for_url(app, live_url), data
        )
        app.discover_albums.assert_called_once()

    def test_archive_buttons_accept_live_thread_and_select_provider(self):
        live_url = f"https://boards.4chan.org/b/thread/{THREAD_ID}"
        app = Mock()
        app.url_var.get.return_value = live_url
        urls = am.thread_api_candidates("b", THREAD_ID)
        with patch.object(gr.webbrowser, "open") as browser:
            gr.GalleryRipperApp.open_archive_api_in_browser(app)
            gr.GalleryRipperApp.open_secondary_archive_api_in_browser(app)
        self.assertEqual(
            [call.args[0] for call in browser.call_args_list], urls
        )

    def test_thebarchive_thread_url_prioritizes_its_own_api(self):
        url = f"https://thebarchive.com/b/thread/{THREAD_ID}/"
        data = make_thread()
        response = Mock(status_code=200)
        response.json.return_value = data
        with patch.object(gr, "_discovery_request", return_value=response) as fetch:
            tree = gr.fourchan_discover_tree(url)
        self.assertEqual(tree["albums"][0]["url"], CANONICAL)
        self.assertEqual(tree["albums"][0]["image_count"], 2)
        self.assertEqual(
            fetch.call_args.args[1],
            am.thread_api_candidates("b", THREAD_ID, preferred="thebarchive.com")[0],
        )

    def test_live_thread_is_only_fetched_once(self):
        live_url = f"https://boards.4chan.org/b/thread/{THREAD_ID}"
        live_data = {
            "posts": [
                {"no": int(THREAD_ID), "sub": "Live subject", "filename": "a", "tim": 123, "ext": ".jpg"},
                {"no": int(THREAD_ID) + 1, "filename": "b", "tim": 124, "ext": ".png"},
            ],
        }
        with patch.object(gr, "fetch_json_simple", return_value=live_data) as live, patch.object(
            gr, "_discovery_request"
        ) as archive:
            tree = gr.fourchan_discover_tree(live_url)
        live.assert_called_once_with(f"https://a.4cdn.org/b/thread/{THREAD_ID}.json")
        archive.assert_not_called()
        self.assertEqual(tree["albums"][0]["image_count"], 2)
        self.assertEqual(tree["albums"][0]["url"], f"4chan:b/{THREAD_ID}")
        self.assertNotIn("_archive_data", tree)

    def test_live_404_recovers_archive_and_keeps_snapshot_for_download(self):
        live_url = f"https://boards.4chan.org/b/thread/{THREAD_ID}"
        official_api = f"https://a.4cdn.org/b/thread/{THREAD_ID}.json"
        expired = Mock(status_code=404, url=official_api)
        error = requests.HTTPError("404 Not Found", response=expired)
        archive_response = Mock(status_code=200)
        data = make_thread()
        archive_response.json.return_value = data
        messages = []
        with patch.object(gr, "fetch_json_simple", side_effect=error) as live, patch.object(
            gr, "_discovery_request", return_value=archive_response
        ) as archive:
            tree = gr.fourchan_discover_tree(live_url, log=messages.append)
            self.assertEqual(tree["albums"][0]["url"], CANONICAL)
            self.assertEqual(tree["albums"][0]["image_count"], 2)
            self.assertIs(tree["_archive_data"], data)
            self.assertTrue(any("expired" in line for line in messages))
            with tempfile.TemporaryDirectory() as folder, patch.object(
                gr, "download_4chan_image_oldschool", return_value=False
            ):
                gr._rip_galleries_impl(
                    [(
                        tree["albums"][0]["name"],
                        tree["albums"][0]["url"],
                        tree["albums"][0]["path"],
                    )],
                    folder, messages.append, live_url, workers=1, archive_data=tree["_archive_data"],
                )
        live.assert_called_once()
        archive.assert_called_once()
        self.assertEqual(archive_response.close.call_count, 1)

    def test_live_403_does_not_masquerade_as_expired_thread(self):
        live_url = f"https://boards.4chan.org/b/thread/{THREAD_ID}"
        denied = Mock(status_code=403)
        with patch.object(
            gr, "fetch_json_simple",
            side_effect=requests.HTTPError("403 Forbidden", response=denied)
        ), patch.object(gr, "_discovery_request") as archive:
            with self.assertRaises(requests.HTTPError):
                gr.fourchan_discover_tree(live_url)
        archive.assert_not_called()

    def test_both_archives_missing_is_an_error_not_empty_album(self):
        live_url = f"https://boards.4chan.org/b/thread/{THREAD_ID}"
        expired = Mock(status_code=404)
        response = Mock(status_code=404)
        response.raise_for_status.side_effect = requests.HTTPError(
            "404 Not Found", response=response
        )
        with patch.object(
            gr, "fetch_json_simple",
            side_effect=requests.HTTPError("404 Not Found", response=expired)
        ), patch.object(gr, "_discovery_request", return_value=response) as archive:
            with self.assertRaisesRegex(RuntimeError, "No archived metadata"):
                gr.fourchan_discover_tree(live_url)
        self.assertEqual(archive.call_count, 2)

    def test_archived_moe_403_tries_thebarchive_successfully(self):
        denied = Mock(status_code=403)
        denied.raise_for_status.side_effect = requests.HTTPError(
            "403 Forbidden", response=denied
        )
        recovered = Mock(status_code=200)
        recovered.json.return_value = make_thread()
        with patch.object(gr, "_discovery_request", side_effect=[denied, recovered]) as fetch:
            tree = gr.fourchan_discover_tree(ARCHIVE_URL)
        self.assertEqual(tree["albums"][0]["image_count"], 2)
        self.assertEqual(
            [call.args[1] for call in fetch.call_args_list],
            am.thread_api_candidates("b", THREAD_ID),
        )
        denied.close.assert_called_once()
        recovered.close.assert_called_once()

    def test_4chan_live_discovery_stores_recovered_snapshot_in_gui(self):
        live_url = f"https://boards.4chan.org/b/thread/{THREAD_ID}"
        app = Mock()
        app._archive_snapshot = None
        app.discovery_stop_flag = None
        tree = {
            "type": "category", "name": "/b/", "url": "4chan:b",
            "albums": [{"name": "Recovered", "url": CANONICAL}],
            "children": [], "_archive_data": make_thread(),
        }
        with patch.object(gr, "discover_or_load_gallery_tree", return_value=tree):
            gr.GalleryRipperApp.do_discover(app, live_url, True)
        self.assertEqual(app._archive_snapshot, (("b", THREAD_ID), make_thread()))
        self.assertNotIn("_archive_data", tree)
        self.assertEqual(app.albums_tree_data["albums"][0]["url"], CANONICAL)

    def test_thebarchive_relative_media_links_keep_the_source_domain(self):
        thread_url = f"https://thebarchive.com/b/thread/{THREAD_ID}/"
        source = make_thread()
        source[THREAD_ID]["op"]["media"]["media_link"] = "/b/redirect/1000000000001.jpg"
        response = Mock(status_code=200)
        response.json.return_value = source
        with patch.object(gr, "_discovery_request", return_value=response):
            tree = gr.fourchan_discover_tree(thread_url)
        snapshot = tree["_archive_data"]
        self.assertEqual(
            snapshot[THREAD_ID]["op"]["media"]["media_link"],
            "https://thebarchive.com/b/redirect/1000000000001.jpg",
        )
        self.assertEqual(
            source[THREAD_ID]["op"]["media"]["media_link"],
            "/b/redirect/1000000000001.jpg",
        )
        _, entries, _ = am.extract_thread(snapshot, "b", THREAD_ID)
        self.assertEqual(
            entries[0][1][0],
            "https://thebarchive.com/b/redirect/1000000000001.jpg",
        )

    def test_alternate_browser_import_preserves_relative_host_through_snapshot(self):
        live_url = f"https://boards.4chan.org/b/thread/{THREAD_ID}"
        source = make_thread()
        source[THREAD_ID]["op"]["media"]["media_link"] = "/b/redirect/1000000000001.jpg"
        app = Mock()
        app.url_var.get.return_value = live_url
        app.discovery_thread = None
        app.download_thread = None
        app._archive_snapshot = None
        app._archive_import_origin = (("b", THREAD_ID), "thebarchive.com")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "saved.json")
            path.write_text(json.dumps(source), encoding="utf-8")
            with patch.object(gr.filedialog, "askopenfilename", return_value=str(path)):
                gr.GalleryRipperApp.import_archived_moe_json(app)
        data = app._archive_snapshot[1]
        self.assertEqual(
            data[THREAD_ID]["op"]["media"]["media_link"],
            "https://thebarchive.com/b/redirect/1000000000001.jpg",
        )
        self.assertEqual(
            gr.GalleryRipperApp._archive_data_for_url(app, live_url), data
        )

    def test_b_original_without_redirect_has_full_image_archive_candidate(self):
        data = make_thread()
        op_media = data[THREAD_ID]["op"]["media"]
        op_media.pop("media_link")
        _, entries, _ = am.extract_thread(data, "b", THREAD_ID)
        self.assertEqual(
            entries[0][1],
            [
                "https://thebarchive.com/b/full_image/1000000000001.jpg",
                "https://i.4cdn.org/b/1000000000001.jpg",
            ],
        )

    def test_4chan_api_requests_enable_documented_minimum_spacing(self):
        response = Mock()
        response.json.return_value = {"posts": []}
        with patch.object(gr.session, "get", return_value=response) as get, patch.object(
            gr.session.pacer, "set_host_min_interval"
        ) as rate_limit:
            result = gr.fetch_json_simple("https://a.4cdn.org/b/catalog.json")
        self.assertEqual(result, {"posts": []})
        rate_limit.assert_called_once_with(
            "https://a.4cdn.org/b/catalog.json", 1.0
        )
        get.assert_called_once()
        self.assertIsNone(get.call_args.kwargs["headers"]["Referer"])

    def test_snapshot_for_download_is_captured_before_worker_starts(self):
        app = Mock()
        app.url_var.get.return_value = ARCHIVE_URL
        app.path_var.get.return_value = "testfolder"
        app.download_workers_var.get.return_value = 1
        app.mimic_var.get.return_value = False
        app.quick_scan_var.get.return_value = True
        app.download_thread = None
        app.selected_album_urls = {"album1"}
        app.item_to_album = {
            "album1": ("Archived", CANONICAL, ["4chan", "b", f"Thread ({THREAD_ID})"])
        }
        data = make_thread()
        app._archive_data_for_url.return_value = data
        with patch.object(gr.threading, "Thread") as worker:
            gr.GalleryRipperApp.start_download(app)
        args = worker.call_args.kwargs["args"]
        self.assertIs(args[-1], data)
        self.assertEqual(args[2], ARCHIVE_URL)


if __name__ == "__main__":
    unittest.main()
