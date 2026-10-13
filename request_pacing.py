"""Per-host request start pacing shared by discovery and downloads.

Pacing is cooperative across threads within this process. It spaces request
starts; it does not promise a maximum number of simultaneous in-flight
connections or provide protection against site-specific access restrictions.
"""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import random
import threading
import time
from urllib.parse import urlsplit

import requests


VIDEO_EXTENSIONS = (".webm", ".mp4", ".gif")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")


class PacingCancelled(requests.RequestException):
    """The caller cancelled while waiting for its request slot."""


def retry_after_seconds(header, *, now=None):
    """Parse either form of HTTP Retry-After; ignore invalid/expired values."""
    if header is None:
        return None
    try:
        seconds = float(header)
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(str(header))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            current = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
            seconds = (date - current).total_seconds()
        except (ValueError, TypeError, OverflowError, IndexError):
            return None
    if not (0 <= seconds < float("inf")):
        return None
    return seconds


def request_kind(url):
    path = urlsplit(url).path.lower()
    if path.endswith(VIDEO_EXTENSIONS):
        return "video"
    if path.endswith(IMAGE_EXTENSIONS):
        return "download"
    return "page"


class HostRequestPacer:
    """Atomically assign each request a start slot for its target host."""

    def __init__(self, *, clock=None, jitter=None):
        self._clock = clock or time.monotonic
        self._jitter = jitter or random.uniform
        self._condition = threading.Condition()
        self._hosts = {}
        self._polite = False

    def set_polite(self, enabled):
        with self._condition:
            self._polite = bool(enabled)
            self._condition.notify_all()

    def wait(self, url, *, kind=None, stop_event=None):
        host = urlsplit(url).netloc.lower()
        if not host:
            return
        kind = kind or request_kind(url)
        with self._condition:
            state = self._hosts.setdefault(host, {
                "next_at": 0.0, "blocked_until": 0.0,
                "penalty": 0.0, "successes": 0,
            })
            while True:
                if stop_event is not None and stop_event.is_set():
                    raise PacingCancelled("Request cancelled while waiting for host pacing.")
                now = self._clock()
                ready_at = max(state["next_at"], state["blocked_until"])
                if now >= ready_at:
                    # Discovery is unchanged when the option is off. Downloads
                    # remain conservatively paced, including with the option off.
                    base = 4.0 if kind == "video" else 0.35 if kind == "download" else 0.0
                    if self._polite:
                        base = max(base, 0.7) + self._jitter(0.0, 0.3)
                    state["next_at"] = now + max(base, state["penalty"])
                    self._condition.notify_all()
                    return
                timeout = ready_at - now
                # Poll cancellation without delaying another host or holding
                # the condition lock during the wait.
                self._condition.wait(min(timeout, 0.2) if stop_event is not None else timeout)

    def record_response(self, url, status, headers=None):
        host = urlsplit(url).netloc.lower()
        if not host:
            return
        headers = headers or {}
        with self._condition:
            state = self._hosts.setdefault(host, {
                "next_at": 0.0, "blocked_until": 0.0,
                "penalty": 0.0, "successes": 0,
            })
            if status == 429 or status in (500, 502, 503, 504):
                state["successes"] = 0
                state["penalty"] = min(20.0, max(1.0, state["penalty"] * 2))
                delay = retry_after_seconds(headers.get("Retry-After"))
                state["blocked_until"] = max(
                    state["blocked_until"],
                    self._clock() + (state["penalty"] if delay is None else delay),
                )
                state["next_at"] = max(state["next_at"], state["blocked_until"])
            elif 200 <= status < 400:
                state["successes"] += 1
                if state["successes"] >= 8:
                    state["penalty"] = max(0.0, state["penalty"] / 2)
                    if state["penalty"] < 0.1:
                        state["penalty"] = 0.0
                    state["successes"] = 0
            self._condition.notify_all()


class PacedSession(requests.Session):
    """Pace outgoing requests at every destination, including redirects."""

    def __init__(self):
        super().__init__()
        self.pacer = HostRequestPacer()
        self._pacing_context = threading.local()

    def request(self, method, url, **kwargs):
        kind = kwargs.pop("_pacing_kind", None)
        stop_event = kwargs.pop("_pacing_stop_event", None)
        previous = getattr(self._pacing_context, "current", None)
        self._pacing_context.current = (kind, stop_event)
        try:
            return super().request(method, url, **kwargs)
        finally:
            self._pacing_context.current = previous

    def send(self, request, **kwargs):
        context = getattr(self._pacing_context, "current", None)
        kind, stop_event = context if context is not None else (None, None)
        self.pacer.wait(request.url, kind=kind, stop_event=stop_event)
        try:
            response = super().send(request, **kwargs)
        except requests.RequestException as exc:
            if not isinstance(exc, PacingCancelled) and not getattr(exc, "_pacing_accounted", False):
                self.pacer.record_response(request.url, 503)
                exc._pacing_accounted = True
            raise
        # Session.send returns the final response after redirects. Attribute
        # each response to its original host; nested send() covers destinations.
        own_response = next(
            (item for item in response.history if item.request is not None
             and item.request.url == request.url),
            response,
        )
        self.pacer.record_response(request.url, own_response.status_code, own_response.headers)
        return response
