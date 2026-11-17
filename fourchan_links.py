"""Extract direct, publicly hosted file links from 4chan post comments.

Only HTTPS URLs with an explicit supported file extension are queued.
Page links and untrusted local/network destinations are not probed.
"""
import hashlib
import html
import ipaddress
import os
import urllib.error
import urllib.request
import re
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

import archived_moe


DIRECT_VIDEO_EXTENSIONS = frozenset({
    ".webm", ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".mpg", ".mpeg",
})
DIRECT_FILE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff",
    ".webm", ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".mpg", ".mpeg",
    ".mp3", ".m4a", ".ogg", ".flac", ".wav",
    ".pdf", ".zip", ".rar", ".7z",
})
_PLAIN_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_PUBLIC_HOST_RE = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", re.I)
_BLOCKED_TOP_LEVEL = {"localhost", "local", "lan", "home", "corp", "internal", "test", "invalid", "example", "onion"}


def public_https_url(url):
    """Reject non-HTTPS, credentials, custom ports and private hostnames/IPs."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme.lower() != "https" or not host or
                parsed.username is not None or parsed.password is not None or
                parsed.port is not None or len(host) > 253 or
                not _PUBLIC_HOST_RE.fullmatch(host)):
            return False
        if any(len(label) > 63 or label.startswith("-") or label.endswith("-")
               for label in host.split(".")):
            return False
        tld = host.rsplit(".", 1)[-1]
        # 127.1 and other shortened numeric IPv4 spellings can resolve to
        # loopback even though ipaddress.ip_address() rejects their syntax.
        if tld.isdigit() or tld in _BLOCKED_TOP_LEVEL:
            return False
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return False
        return True
    except (TypeError, ValueError):
        return False


def direct_file_url(value):
    """Return a normalized direct-file URL and safe filename, or None."""
    if not isinstance(value, str):
        return None
    value = html.unescape(value.strip()).rstrip(".,;!?)]}>")
    if value.startswith("//"):
        value = "https:" + value
    if not public_https_url(value):
        return None
    parsed = urlsplit(value)
    filename = archived_moe._safe_filename(unquote(parsed.path.rsplit("/", 1)[-1]))
    if not filename or os.path.splitext(filename)[1].lower() not in DIRECT_FILE_EXTENSIONS:
        return None
    if any(segment.lower() in {"thumb", "thumbnail", "preview"} for segment in parsed.path.split("/")):
        return None
    # Fragments are browser-side navigation; never part of a download key.
    normalized = parsed._replace(netloc=parsed.netloc.lower(), fragment="").geturl()
    return normalized, filename


def comment_file_links(comment):
    """Yield direct files from anchors and visible bare URLs, in post order."""
    if not isinstance(comment, str) or not comment:
        return
    soup = BeautifulSoup(html.unescape(comment), "html.parser")
    candidates = [
        anchor.get("href") for anchor in soup.find_all("a", href=True)
    ]
    candidates.extend(_PLAIN_URL_RE.findall(soup.get_text(" ", strip=True)))
    seen = set()
    for candidate in candidates:
        parsed = direct_file_url(candidate)
        if parsed and parsed[0] not in seen:
            seen.add(parsed[0])
            yield parsed


def with_linked_files(posts, original_entries):
    """Append unique external files, retaining original attachment entries.

    The fourth tuple element marks external links so archived-thread downloads
    do not mistake them for unverifiable archived attachments.
    """
    entries = list(original_entries)
    used_names = {name.casefold() for name, *_ in entries}
    seen_urls = {url for _, urls, *_ in entries for url in urls}
    for post in posts:
        if not isinstance(post, dict):
            continue
        comment = post.get("com") or post.get("comment") or ""
        for url, filename in comment_file_links(comment):
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if filename.casefold() in used_names:
                stem, ext = os.path.splitext(filename)
                suffix = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
                stem = stem[:max(1, 180 - len(ext) - len(suffix) - 1)]
                filename = f"{stem}_{suffix}{ext}"
                count = 2
                while filename.casefold() in used_names:
                    filename = f"{stem}_{suffix}_{count}{ext}"
                    count += 1
            used_names.add(filename.casefold())
            entries.append((filename, [url], None, True))
    return entries

class PublicMediaRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Block redirects from linked files to local addresses or HTTP."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_https_url(newurl):
            raise urllib.error.HTTPError(req.full_url, 403, "Unsafe linked-media redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

