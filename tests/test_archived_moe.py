"""Archived.moe recovery and live-4chan folder compatibility regressions."""
import base64
import hashlib
import tempfile
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


if __name__ == "__main__":
    unittest.main()
