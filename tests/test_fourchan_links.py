"""Regression tests for direct-file links in 4chan and archived comments."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import archived_moe
import fourchan_links as links
import gallery_ripper as gr


THREAD = "956554481"
BASE_URL = f"https://boards.4chan.org/b/thread/{THREAD}"
CATBOX = [
    f"https://files.catbox.moe/{name}.mp4"
    for name in ("cc1p6m", "2hykqx", "f8rxgy", "jmw339")
]


def live_thread():
    return {"posts": [
        {"no": int(THREAD), "sub": "Enjoy",
         "tim": 999999, "ext": ".jpg", "filename": "cover",
         "com": "Enjoy<br>" + "<br>".join(
             f'<a href="{url}">{url}</a> (embed)' for url in CATBOX
         )},
        {"no": int(THREAD) + 1,
         "com": CATBOX[0] + " and https://example.com/page.html"},
    ]}


class ExtractLinkedFilesTests(unittest.TestCase):
    def test_links_in_post_html_and_plain_text(self):
        entries = gr.fourchan_thread_images("b", THREAD, thread_data=live_thread())
        self.assertEqual(len(entries), 5)
        self.assertEqual(entries[0], (
            "cover.jpg", ["https://i.4cdn.org/b/999999.jpg"], None
        ))
        self.assertEqual(
            [entry[1][0] for entry in entries[1:]], CATBOX
        )
        self.assertEqual([entry[0] for entry in entries[1:]], [
            "cc1p6m.mp4", "2hykqx.mp4", "f8rxgy.mp4", "jmw339.mp4"
        ])
        self.assertTrue(all(entry[3] is True for entry in entries[1:]))

    def test_no_crawling_of_non_file_pages_or_unsafe_destinations(self):
        invalid = [
            "http://files.catbox.moe/c.mp4",
            "https://127.0.0.1/c.mp4",
            "https://127.1/c.mp4",
            "https://10.0/c.mp4",
            "https://[::1]/c.mp4",
            "https://localhost/c.mp4",
            "https://host.internal/c.mp4",
            "https://router.lan/c.mp4",
            "https://user:pass@files.catbox.moe/c.mp4",
            "https://files.catbox.moe:8443/c.mp4",
            "https://example.org/watch?v=c.mp4",
            "https://files.catbox.moe/c.exe",
            "javascript:alert(1)",
        ]
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(links.direct_file_url(value))
        self.assertFalse(links.public_https_url("https://127.0.0.1/c.mp4"))
        self.assertTrue(links.public_https_url("https://files.catbox.moe/c.mp4"))

    def test_html_entities_fragment_and_duplicates(self):
        comments = [
            {"com": '<a href="https://files.catbox.moe/c.mp4?token=a&amp;v=b#x">file</a>'},
            {"com": "https://files.catbox.moe/c.mp4?token=a&v=b#y"},
        ]
        entries = links.with_linked_files(comments, [])
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            entries[0][1][0], "https://files.catbox.moe/c.mp4?token=a&v=b"
        )

    def test_same_name_different_host_and_attachment_do_not_overwrite(self):
        originals = [("file.mp4", ["https://i.4cdn.org/b/1.mp4"], None)]
        comments = [{"com": (
            "https://files.catbox.moe/file.mp4 "
            "https://media.example.org/file.mp4 "
            "https://files.catbox.moe/file.mp4"
        )}]
        result = links.with_linked_files(comments, originals)
        self.assertEqual(len(result), 3)
        self.assertEqual(len({entry[0].casefold() for entry in result}), 3)
        self.assertTrue(all(name.endswith(".mp4") for name, *_ in result))
        self.assertTrue(all(name != "file.mp4" for name, *_ in result[1:]))

    def test_escaped_traversal_and_windows_names_are_safe(self):
        entries = links.with_linked_files([
            {"com": "https://files.catbox.moe/%2E%2E%5C%2E%2E%5CCON.mp4"}
        ], [])
        self.assertEqual(len(entries), 1)
        name = entries[0][0]
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertNotEqual(name.lower(), "con.mp4")
        self.assertTrue(name.endswith(".mp4"))

    def test_external_4cdn_attachment_link_is_not_queued_twice(self):
        existing = [("cover.jpg", ["https://i.4cdn.org/b/999999.jpg"], None)]
        items = links.with_linked_files([
            {"com": '<a href="https://i.4cdn.org/b/999999.jpg">cover</a>'}
        ], existing)
        self.assertEqual(items, existing)

    def test_unsafe_redirect_is_rejected_before_following(self):
        handler = links.PublicMediaRedirectHandler()
        req = gr.urllib.request.Request(CATBOX[0])
        for new_url in ("http://example.org/file.mp4",
                        "https://localhost/file.mp4",
                        "https://127.0.0.1/file.mp4"):
            with self.subTest(new_url=new_url):
                with self.assertRaises(gr.urllib.error.HTTPError):
                    handler.redirect_request(req, None, 302, "Found", {}, new_url)


class LinkedMediaPipelineTests(unittest.TestCase):
    def test_discovery_counts_linked_files_from_one_thread_fetch(self):
        with patch.object(gr, "fetch_fourchan_thread", return_value=live_thread()) as fetch:
            tree = gr.fourchan_discover_tree(BASE_URL)
        fetch.assert_called_once_with("b", THREAD)
        self.assertEqual(tree["albums"][0]["image_count"], 5)

    def test_archived_comment_links_and_original_attachments(self):
        data = {
            THREAD: {
                "op": {"num": THREAD, "title": "Enjoy", "comment": CATBOX[0],
                       "media": {"media_filename": "cover.jpg",
                                 "media_orig": "999999.jpg"}},
                "posts": {"9": {"num": "9", "comment":
                           '<a href="' + CATBOX[1] + '">linked video</a>'}},
            }
        }
        subject, entries = gr.archived_moe_thread_images(
            "b", THREAD, archive_data=data,
        )
        self.assertEqual(subject, "Enjoy")
        self.assertEqual(len(entries), 3)
        self.assertEqual([entry[1][0] for entry in entries[1:]], CATBOX[:2])
        self.assertTrue(all(entry[3] is True for entry in entries[1:]))

    def test_downloads_external_links_with_original_validation(self):
        downloads = []

        def fake_download(url, folder, _log, fname, _idx, _total, stats, *args, **kwargs):
            downloads.append((url, fname, kwargs))
            path = Path(folder, fname)
            if not path.exists():
                path.write_bytes(b"media")
                stats["downloaded"] += 1
                stats["total_bytes"] += 5
            return True

        with tempfile.TemporaryDirectory() as folder, patch.object(
            gr, "fetch_fourchan_thread", return_value=live_thread()
        ), patch.object(
            gr, "download_4chan_image_oldschool", side_effect=fake_download
        ):
            selected = [("Enjoy", f"4chan:b/{THREAD}", ["4chan", "b", f"Enjoy {THREAD}"])]
            gr._rip_galleries_impl(selected, folder, lambda _m: None, BASE_URL, workers=1)
            dest = Path(folder, "4chan", "b", f"Enjoy {THREAD}")
            self.assertEqual(len(list(dest.iterdir())), 5)
        self.assertEqual(len(downloads), 5)
        self.assertFalse(downloads[0][2].get("linked_media", False))
        for url, filename, kwargs in downloads[1:]:
            self.assertTrue(kwargs["linked_media"])
            self.assertTrue(kwargs["require_original"])
            self.assertEqual(url.rsplit("/", 1)[-1], filename)

    def test_archive_links_bypass_missing_original_candidate_retry(self):
        data = {
            THREAD: {
                "op": {"num": THREAD, "title": "Test", "comment": CATBOX[0]},
                "posts": {},
            }
        }
        calls = []
        def fake_download(*args, **kwargs):
            calls.append((args, kwargs))
            return True
        with tempfile.TemporaryDirectory() as folder, patch.object(
            gr, "download_4chan_image_oldschool", side_effect=fake_download
        ), patch.object(
            gr, "download_4chan_archive_candidates"
        ) as archive:
            gr._rip_galleries_impl(
                [("Test", f"archived.moe:b/{THREAD}",
                  ["4chan", "b", f"Test ({THREAD})"])],
                folder, lambda _m: None, f"archived.moe:b/{THREAD}",
                workers=1, archive_data=data,
            )
        archive.assert_not_called()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1]["linked_media"])
        self.assertTrue(calls[0][1]["require_original"])

    def test_linked_html_response_is_not_saved_as_video(self):
        response = Mock(status=200, headers={"Content-Type": "text/html"})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.geturl.return_value = CATBOX[0]
        response.read.side_effect = [b"<html>not a video</html>", b""]
        opener = Mock()
        opener.open.return_value = response
        with tempfile.TemporaryDirectory() as folder, patch.object(
            gr.urllib.request, "build_opener", return_value=opener
        ), patch.object(gr.session.pacer, "wait"), patch.object(
            gr.session.pacer, "record_response"
        ):
            self.assertFalse(gr.download_4chan_image_oldschool(
                CATBOX[0], folder, lambda _msg: None,
                require_original=True, linked_media=True,
                max_attempts=1,
            ))
            self.assertFalse(list(Path(folder).iterdir()))


if __name__ == "__main__":
    unittest.main()
