"""FoolFuuka archived.moe thread metadata for recovering original 4chan media.

The archive's thumbnail links are intentionally never download candidates.
"""
import base64
import binascii
import html
import re
from urllib.parse import urljoin, urlparse

_ARCHIVE_THREAD = re.compile(r"/([a-z0-9]+)/thread/(\d+)/?", re.I)
_CANONICAL_THREAD = re.compile(r"archived\.moe:([a-z0-9]+)/(\d+)", re.I)
_ORIGINAL_NAME = re.compile(r"[A-Za-z0-9_-]+\.(?:jpe?g|png|gif|webp|bmp|webm|mp4|pdf|swf)", re.I)
_BAD_FILENAME = re.compile(r'[\\/*?:"<>|\x00-\x1f]')
_WINDOWS_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


def parse_thread_url(url):
    """Return (board, thread ID) for a supported archive URL or internal key."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    match = _CANONICAL_THREAD.fullmatch(url)
    if match:
        return match.group(1).lower(), match.group(2)
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or (
        parsed.hostname or ""
    ).lower() not in {"archived.moe", "www.archived.moe"}:
        return None
    match = _ARCHIVE_THREAD.fullmatch(parsed.path)
    if not match:
        return None
    return match.group(1).lower(), match.group(2)


def canonical_thread_url(board, thread_id):
    return f"archived.moe:{board}/{thread_id}"


def thread_api_url(board, thread_id):
    if not re.fullmatch(r"[a-z0-9]+", str(board), re.I) or not re.fullmatch(
        r"\d+", str(thread_id)
    ):
        raise ValueError("Invalid archived.moe board or thread ID")
    return f"https://archived.moe/_/api/chan/thread/?board={board}&num={thread_id}"


def _clean_subject(value):
    value = html.unescape(re.sub(r"<[^>]*>", "", str(value or "")))
    return " ".join(value.split())[:80]


def _safe_filename(value):
    if not isinstance(value, str):
        return ""
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    name = _BAD_FILENAME.sub("_", name).strip(" .")
    if not name:
        return ""
    if _WINDOWS_RESERVED.match(name):
        name = "_" + name
    if len(name) > 180:
        stem, dot, extension = name.rpartition(".")
        name = stem[:170 - len(extension)] + dot + extension if dot else name[:180]
    return name


def _original_candidates(media, board):
    """Get original-file locations only, never FoolFuuka thumbnails."""
    urls = []

    def add(value):
        if not isinstance(value, str) or not value.strip():
            return
        absolute = urljoin("https://archived.moe/", value.strip())
        parsed = urlparse(absolute)
        if parsed.scheme != "https" or not parsed.hostname or (
            any(segment in {"thumb", "thumbnail", "preview"} for segment in
                parsed.path.lower().split("/"))
        ):
            return
        if absolute not in urls:
            urls.append(absolute)

    add(media.get("media_link"))
    add(media.get("remote_media_link"))

    original = media.get("media_orig")
    if isinstance(original, str) and _ORIGINAL_NAME.fullmatch(original):
        # archived.moe's /b/ redirect targets an independently hosted full-image
        # archive. Attempt the direct location if the redirect itself is blocked.
        if board == "b" and any(
            urlparse(url).hostname in {"archived.moe", "www.archived.moe"}
            and "/redirect/" in urlparse(url).path
            for url in urls
        ):
            add(f"https://thebarchive.com/b/full_image/{original}")
        # The /b/ full-image archive also uses '.web' for some WEBM files.
        if board == "b":
            for location in tuple(urls):
                if urlparse(location).hostname == "thebarchive.com" and location.endswith(".webm"):
                    add(location[:-1])
        # Recently expired threads may still have media on 4chan's CDN.
        add(f"https://i.4cdn.org/{board}/{original}")
    return urls


def _verification_metadata(media):
    """Return original byte count/MD5 if FoolFuuka supplied valid metadata."""
    result = {}
    try:
        size = int(media.get("media_size"))
        if size > 0:
            result["size"] = size
    except (ValueError, TypeError):
        pass
    md5 = media.get("media_hash")
    if isinstance(md5, str):
        try:
            decoded = base64.b64decode(md5.strip(), validate=True)
            if len(decoded) == 16:
                result["md5"] = decoded
        except (ValueError, binascii.Error):
            pass
    return result or None


def _post_order(post):
    try:
        return int(str(post.get("num", "0")).split("_", 1)[0])
    except (ValueError, TypeError):
        return 0


def extract_thread(data, board, thread_id):
    """Return (subject, (filename, original URLs, referer) entries, unavailable).

    The archive API returns a mapping keyed by OP ID, with an 'op' object and
    a 'posts' mapping. Entries retain the original 4chan filename when present
    so rerunning a thread skips matching files in an existing live-thread rip.
    """
    thread = data.get(str(thread_id)) if isinstance(data, dict) else None
    if not isinstance(thread, dict) or not isinstance(thread.get("op"), dict):
        raise ValueError(f"Archived thread /{board}/{thread_id} is unavailable or malformed")

    op = thread["op"]
    subject = _clean_subject(op.get("title") or op.get("comment"))
    subject = subject or f"Thread {thread_id}"
    replies = thread.get("posts") or {}
    if isinstance(replies, dict):
        replies = list(replies.values())
    if not isinstance(replies, list):
        raise ValueError("Archived thread replies have an unsupported format")

    entries = []
    filenames = set()
    unavailable = 0
    for post in [op] + sorted(
        (post for post in replies if isinstance(post, dict)), key=_post_order
    ):
        media = post.get("media")
        if not isinstance(media, dict):
            continue
        original = media.get("media_orig")
        fname = _safe_filename(
            media.get("media_filename") or media.get("media_orig")
        )
        urls = _original_candidates(media, board)
        if not fname or not urls:
            unavailable += 1
            continue
        if fname.casefold() in filenames:
            stem, dot, ext = fname.rpartition(".")
            stem = stem if dot else fname
            suffix = original.rpartition(".")[0] if isinstance(original, str) else ""
            suffix = suffix if re.fullmatch(r"[A-Za-z0-9_-]+", suffix) else str(_post_order(post))
            candidate = f"{stem}_{suffix}{dot}{ext}"
            index = 2
            while candidate.casefold() in filenames:
                candidate = f"{stem}_{suffix}_{index}{dot}{ext}"
                index += 1
            fname = candidate
        filenames.add(fname.casefold())
        entries.append((fname, urls, _verification_metadata(media)))
    return subject, entries, unavailable
