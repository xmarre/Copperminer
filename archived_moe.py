"""FoolFuuka archived.moe thread metadata for recovering original 4chan media.

The archive's thumbnail links are intentionally never download candidates.
"""
import base64
import binascii
import copy
import html
import json
import re
from urllib.parse import urljoin, urlparse

_ARCHIVE_THREAD = re.compile(r"/([a-z0-9]+)/thread/(\d+)/?", re.I)
_CANONICAL_THREAD = re.compile(r"archived\.moe:([a-z0-9]+)/(\d+)", re.I)
_ORIGINAL_NAME = re.compile(r"[A-Za-z0-9_-]+\.(?:jpe?g|png|gif|webp|bmp|webm|mp4|pdf|swf)", re.I)
_BAD_FILENAME = re.compile(r'[\\/*?:"<>|\x00-\x1f]')
_WINDOWS_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)
MAX_SAVED_THREAD_JSON_BYTES = 64 * 1024 * 1024


class ArchiveAccessBlockedError(RuntimeError):
    """A server denied access to archived thread metadata."""

    def __init__(self, api_url, status_code):
        self.api_url = api_url
        self.status_code = status_code
        super().__init__(
            f"Archive metadata API returned HTTP {status_code} at {api_url}. "
            "The host may require browser verification or prohibit automated API access. "
            "Open an archive API URL in your browser and import a saved JSON response."
        )


def load_saved_thread_json(path, board, thread_id):
    """Load a browser-saved FoolFuuka thread response, rejecting wrong threads.

    The bytes are read once and can be passed to discovery and download without
    any further HTTP requests to the archive metadata API.
    """
    with open(path, "rb") as stream:
        raw = stream.read(MAX_SAVED_THREAD_JSON_BYTES + 1)
    if len(raw) > MAX_SAVED_THREAD_JSON_BYTES:
        raise ValueError("Saved archive JSON exceeds the 64 MiB import limit.")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "This is not valid archived.moe API JSON. Browser challenge pages "
            "and saved HTML pages cannot be imported as thread metadata."
        ) from exc
    # In addition to validating the response shape, check the board when the
    # archive provides it. The thread ID must be present as the response key.
    thread = data.get(str(thread_id)) if isinstance(data, dict) else None
    if not isinstance(thread, dict):
        raise ValueError(
            f"Saved JSON does not contain thread /{board}/{thread_id}."
        )
    op = thread.get("op")
    if not isinstance(op, dict):
        raise ValueError("Saved archive JSON has no valid original post.")
    board_meta = op.get("board")
    if isinstance(board_meta, dict):
        recorded_board = board_meta.get("shortname")
        if recorded_board and str(recorded_board).lower() != board.lower():
            raise ValueError(
                f"Saved JSON belongs to /{recorded_board}/, not /{board}/."
            )
    extract_thread(data, board, thread_id)
    return data


def parse_thread_url(url):
    """Return (board, thread ID) for a supported archive URL or internal key."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    match = _CANONICAL_THREAD.fullmatch(url)
    if match:
        return match.group(1).lower(), match.group(2)
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower()
    if host not in {
        "archived.moe", "www.archived.moe",
        "thebarchive.com", "www.thebarchive.com",
    }:
        return None
    match = _ARCHIVE_THREAD.fullmatch(parsed.path)
    if not match:
        return None
    board, thread_id = match.group(1).lower(), match.group(2)
    if host in {"thebarchive.com", "www.thebarchive.com"} and board != "b":
        return None
    return board, thread_id


def canonical_thread_url(board, thread_id):
    return f"archived.moe:{board}/{thread_id}"


def thread_api_url(board, thread_id):
    if not re.fullmatch(r"[a-z0-9]+", str(board), re.I) or not re.fullmatch(
        r"\d+", str(thread_id)
    ):
        raise ValueError("Invalid archived.moe board or thread ID")
    return f"https://archived.moe/_/api/chan/thread/?board={board}&num={thread_id}"


def thread_api_candidates(board, thread_id, preferred="archived.moe"):
    """Return supported metadata endpoints; /b/ has a dedicated second archive.

    These are public archive API endpoints, not anti-bot bypasses. A 403 can
    still occur on both hosts, in which case saved browser JSON is required.
    """
    primary = thread_api_url(board, thread_id)
    if str(board).lower() != "b":
        return [primary]
    secondary = (
        "https://thebarchive.com/_/api/chan/thread/"
        f"?board=b&num={thread_id}"
    )
    if preferred in {"thebarchive.com", "www.thebarchive.com"}:
        return [secondary, primary]
    return [primary, secondary]


def resolve_relative_media_links(data, board, thread_id, source_host):
    """Anchor relative original URLs to the archive that supplied metadata.

    A snapshot stays valid after discovery because resolved media links are
    embedded in a separate copy of its JSON. Only known archive hosts are
    accepted; no user-provided arbitrary download base is introduced.
    """
    host = str(source_host).lower()
    if host not in {"archived.moe", "www.archived.moe",
                    "thebarchive.com", "www.thebarchive.com"}:
        raise ValueError("Unsupported archive origin")
    if host.startswith("thebarchive") or host.startswith("www.thebarchive"):
        if board != "b":
            raise ValueError("TheBArchive is supported for /b/ only")
    if host in {"archived.moe", "www.archived.moe"}:
        # Existing extraction already resolves relative links against archived.moe.
        return data
    resolved = copy.deepcopy(data)
    thread = resolved.get(str(thread_id)) if isinstance(resolved, dict) else None
    if not isinstance(thread, dict):
        return resolved
    posts = [thread.get("op")]
    replies = thread.get("posts") or {}
    if isinstance(replies, dict):
        posts.extend(replies.values())
    elif isinstance(replies, list):
        posts.extend(replies)
    for post in posts:
        media = post.get("media") if isinstance(post, dict) else None
        if not isinstance(media, dict):
            continue
        for key in ("media_link", "remote_media_link"):
            value = media.get(key)
            if isinstance(value, str) and value.strip():
                media[key] = urljoin(f"https://{host}/", value.strip())
    return resolved


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
        # TheBArchive uses the full_image path for original /b/ media.
        # Offer this candidate even when the archive record has no redirect
        # link; availability is still verified by the downloader.
        if board == "b":
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
