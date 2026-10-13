"""Host-pacing and integration regressions."""
import concurrent.futures
import io
import tempfile
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import threading
import time
import unittest
from unittest.mock import Mock, patch

import requests
import gallery_ripper as gr
from request_pacing import HostRequestPacer, PacedSession, PacingCancelled, retry_after_seconds


class HostRequestPacerTests(unittest.TestCase):
    def test_multiple_workers_never_reserve_the_same_host_slot(self):
        pacer = HostRequestPacer(jitter=lambda _a, _b: 0)
        starts = []
        lock = threading.Lock()
        def task(_index):
            pacer.wait("https://example.test/image.jpg")
            with lock:
                starts.append(time.monotonic())
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(task, range(5)))
        starts.sort()
        self.assertEqual(len(starts), 5)
        self.assertTrue(all(b - a >= 0.30 for a, b in zip(starts, starts[1:])), starts)

    def test_independent_hosts_do_not_share_cooldowns(self):
        pacer = HostRequestPacer()
        pacer.record_response("https://a.test/file.jpg", 429, {"Retry-After": "30"})
        start = time.monotonic()
        pacer.wait("https://b.test/file.jpg")
        self.assertLess(time.monotonic() - start, 0.3)

    def test_polite_option_paces_page_requests(self):
        pacer = HostRequestPacer(jitter=lambda _a, _b: 0)
        pacer.set_polite(True)
        start = time.monotonic()
        pacer.wait("https://example.test/gallery")
        pacer.wait("https://example.test/gallery?page=2")
        self.assertGreaterEqual(time.monotonic() - start, 0.67)

    def test_default_discovery_remains_unthrottled(self):
        pacer = HostRequestPacer()
        start = time.monotonic()
        pacer.wait("https://example.test/one")
        pacer.wait("https://example.test/two")
        self.assertLess(time.monotonic() - start, 0.2)

    def test_retry_after_http_date_and_invalid_value(self):
        now = datetime.now(timezone.utc)
        self.assertAlmostEqual(
            retry_after_seconds(format_datetime(now + timedelta(seconds=50))),
            50, delta=2,
        )
        self.assertIsNone(retry_after_seconds("invalid"))
        self.assertIsNone(retry_after_seconds("-2"))

    def test_cancel_during_host_cooldown(self):
        pacer = HostRequestPacer()
        pacer.record_response("https://a.test/item.jpg", 429, {"Retry-After": "30"})
        flag = threading.Event()
        future = None
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(pacer.wait, "https://a.test/item.jpg", stop_event=flag)
            flag.set()
            with self.assertRaises(PacingCancelled):
                future.result(timeout=1)

    def test_server_backoff_is_shared_by_discovery_and_downloads(self):
        pacer = HostRequestPacer(jitter=lambda _a, _b: 0)
        pacer.record_response("https://a.test/gallery", 429, {"Retry-After": "12"})
        with pacer._condition:
            self.assertGreater(pacer._hosts["a.test"]["blocked_until"], time.monotonic() + 10)
        # The same host entry is used for page and media, unlike the old
        # global file-type limiters.
        self.assertEqual(len(pacer._hosts), 1)


class DownloaderIntegrationTests(unittest.TestCase):
    def test_fourchan_http_429_retries_under_shared_pacing(self):
        url = "https://i.4cdn.org/test/123.jpg"
        limited = urllib.error.HTTPError(url, 429, "rate limit", {"Retry-After": "0"}, None)
        response = Mock(status=200, headers={})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = [b"original media", b""]
        with tempfile.TemporaryDirectory() as folder, patch.object(
            gr.session.pacer, "wait"
        ) as wait, patch.object(
            gr.session.pacer, "record_response"
        ) as record, patch.object(
            gr.urllib.request, "urlopen", side_effect=[limited, response]
        ), patch.object(gr.time, "sleep"):
            self.assertTrue(gr.download_4chan_image_oldschool(
                url, folder, lambda _msg: None, max_attempts=2,
            ))
            self.assertEqual(Path(folder, "123.jpg").read_bytes(), b"original media")
        self.assertEqual(wait.call_count, 2)
        self.assertEqual(record.call_args_list[0].args[:2], (url, 429))
        self.assertEqual(record.call_args_list[1].args[:2], (url, 200))

    def test_cancelled_candidate_does_not_retry_other_urls(self):
        from request_pacing import PacingCancelled
        with tempfile.TemporaryDirectory() as folder, patch.object(
            gr.session, "get", side_effect=PacingCancelled("stopped")
        ) as get:
            result = gr.download_image_candidates(
                ["https://a.test/one.jpg", "https://a.test/two.jpg"],
                folder, lambda _msg: None, max_attempts=3,
            )
        self.assertFalse(result)
        get.assert_called_once()

    def test_worker_error_acknowledges_queue_item(self):
        import queue
        items = queue.Queue()
        stats = {"downloaded": 0, "errors": 0}
        items.put((1, "album", "/invalid", ["https://a.test/one.jpg"],
                   None, None, 1, stats))
        with patch.object(gr.os, "makedirs", side_effect=OSError("blocked")):
            gr.threaded_download_worker(items, lambda _msg: None, None)
        self.assertEqual(items.unfinished_tasks, 0)
        self.assertEqual(stats["errors"], 1)


    def test_job_pacing_context_restores_nested_mode(self):
        session = PacedSession()
        self.assertIsNone(getattr(session._pacing_context, "polite", None))
        with session.polite_mode(True):
            self.assertIs(session._pacing_context.polite, True)
            with session.polite_mode(False):
                self.assertIs(session._pacing_context.polite, False)
            self.assertIs(session._pacing_context.polite, True)
        self.assertIsNone(session._pacing_context.polite)

    def test_job_pacing_context_is_thread_local(self):
        session = PacedSession()
        barrier = threading.Barrier(2)
        def task(value):
            with session.polite_mode(value):
                barrier.wait(timeout=2)
                return session._pacing_context.polite
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(task, value) for value in (True, False)]
            self.assertCountEqual([future.result(timeout=3) for future in futures], [True, False])


class PacedSessionTests(unittest.TestCase):
    @staticmethod
    def response(url, status=200, headers=None):
        value = requests.Response()
        value.status_code = status
        value.url = url
        value.request = requests.Request("GET", url).prepare()
        value.headers.update(headers or {})
        value._content = b""
        value.raw = io.BytesIO(b"")
        return value

    def test_get_post_preserve_arguments_and_use_pacer(self):
        session = PacedSession()
        session.trust_env = False
        with patch.object(session.pacer, "wait") as wait, patch.object(
            session.pacer, "record_response"
        ) as record, patch.object(
            requests.adapters.HTTPAdapter, "send",
            side_effect=[
                self.response("https://a.test/graphql"),
                self.response("https://a.test/photo.jpg"),
            ],
        ) as send:
            session.post("https://a.test/graphql", json={"query": "test"}, timeout=20)
            session.get("https://a.test/photo.jpg", stream=True, timeout=30,
                        _pacing_kind="download")
        self.assertEqual([x.args[0] for x in wait.call_args_list],
                         ["https://a.test/graphql", "https://a.test/photo.jpg"])
        self.assertTrue(send.call_args.kwargs["stream"])
        self.assertEqual(send.call_args.args[0].url, "https://a.test/photo.jpg")
        self.assertEqual(record.call_count, 2)

    def test_redirect_is_paced_at_destination_and_recorded_per_host(self):
        session = PacedSession()
        session.trust_env = False
        first = self.response("https://a.test/start", 302,
                              {"Location": "https://b.test/final"})
        second = self.response("https://b.test/final")
        with patch.object(session.pacer, "wait") as wait, patch.object(
            session.pacer, "record_response"
        ) as record, patch.object(
            requests.adapters.HTTPAdapter, "send", side_effect=[first, second]
        ):
            result = session.get("https://a.test/start")
        self.assertEqual(result.url, "https://b.test/final")
        self.assertEqual([call.args[0] for call in wait.call_args_list],
                         ["https://a.test/start", "https://b.test/final"])
        self.assertCountEqual(
            [(call.args[0], call.args[1]) for call in record.call_args_list],
            [("https://a.test/start", 302), ("https://b.test/final", 200)],
        )

    def test_failed_request_enters_backoff(self):
        session = PacedSession()
        session.trust_env = False
        with patch.object(session.pacer, "wait"), patch.object(
            requests.adapters.HTTPAdapter, "send",
            side_effect=requests.ConnectionError("offline")
        ), patch.object(session.pacer, "record_response") as record:
            with self.assertRaises(requests.ConnectionError):
                session.get("https://a.test/gallery")
        record.assert_called_once_with("https://a.test/gallery", 503)


if __name__ == "__main__":
    unittest.main()
