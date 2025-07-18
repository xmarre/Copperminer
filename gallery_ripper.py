import os
import threading
import asyncio
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, IntVar
from ttkbootstrap.tooltip import ToolTip
import webbrowser
from tkinter.scrolledtext import ScrolledText
import ttkbootstrap as tb
from ttkbootstrap.constants import *
import re
import json
import time
import random
import hashlib
import subprocess
import sys
import glob
import queue
from collections import deque

SETTINGS_FILE = "settings.json"


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(settings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)

def compute_child_hash(subcats, albums):
    """Return a stable hash for the discovered subcats/albums list."""
    h = hashlib.sha1()
    for name, url in sorted(subcats):
        h.update(name.encode("utf-8", errors="ignore"))
        h.update(url.encode("utf-8", errors="ignore"))
    for alb in sorted(albums, key=lambda a: a["url"]):
        h.update(alb["name"].encode("utf-8", errors="ignore"))
        h.update(alb["url"].encode("utf-8", errors="ignore"))
    return h.hexdigest()


def compute_hash_from_list(items):
    """Return a SHA1 hash for the given list of strings."""
    h = hashlib.sha1()
    for it in sorted(items):
        h.update(it.encode("utf-8", errors="ignore"))
    return h.hexdigest()


def get_git_version():
    """Return the current git version (tag or commit hash)."""
    repo_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    try:
        version = subprocess.check_output(
            ["git", "describe", "--tags", "--always"],
            cwd=repo_dir,
            text=True,
        ).strip()
        return version
    except Exception:
        return "(unknown)"


def ensure_https_remote(repo_dir):
    """Ensure the git remote uses HTTPS rather than SSH."""
    try:
        subprocess.run(
            [
                "git",
                "remote",
                "set-url",
                "origin",
                "https://github.com/xmarre/Copperminer.git",
            ],
            cwd=repo_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
    except Exception:
        # If this fails we still attempt the update normally
        pass


# --- Filtering helpers -------------------------------------------------------

UI_IMAGE_FILENAMES = {
    "rate_empty.png",
    "rate_full.png",
    "rate_highlight.png",
    "folder.gif",
    "thumbs.db",
    "spacer.gif",
}


def is_ui_image(url: str, name: str) -> bool:
    """Return True if *name* or *url* looks like a UI/icon asset."""
    name = name.lower()
    if name in UI_IMAGE_FILENAMES:
        return True
    patterns = ["/themes/", "/images/", "/icons/", "/button_", "/star", "/rating"]
    if any(p in url for p in patterns):
        return True
    return False


def is_probably_thumbnail(url: str) -> bool:
    """Return True if the remote resource is very small (<4KB)."""
    try:
        r = session.head(url, timeout=5, allow_redirects=True)
        length = int(r.headers.get("content-length", 0))
        if 0 < length < 4096:
            return True
    except Exception:
        pass
    return False


# --- Universal gallery adapter ----------------------------------------------

DEFAULT_RULES = {
    "theplace2": {
        "domains": ["theplace2.ru", "theplace2.com"],
        "root_album_selector": "a[href^='/photos/']:not([href$='.html'])",
        "pagination_selector": ".pagination a[href]",
        "thumb_selector": "a[href^='pic-']",
        "detail_image_selector": ".big-photo-wrapper a[href]",
    },
    "theplace-2com": {
        "domains": ["theplace-2.com"],
        "root_album_selector": "a[href^='/photos/'][href*='-pictures-'][href$='.htm']",
        # Works for both mobile and desktop navigation
        "pagination_selector": "nav[aria-label*='pagination'] a.page-link[href]",
        # Each thumbnail link points to a pic-XXXXXX.html detail page
        "thumb_selector": ".pic-card a.link[href*='pic-']",
        "detail_image_selector": ".big-photo-wrapper a[href]",
    },
    "livejournal": {
        "domains": ["livejournal.com"],
        # Albums use /photo/album/<id>
        "root_album_selector": "a[href*='/photo/album/']",
        "pagination_selector": "a[href*='page=']",
        # Each thumbnail links to a /photo/item/<id> detail page
        "thumb_selector": "a[href*='/photo/item/']",
        # Detail pages show the image directly inside an <img>
        "detail_image_selector": "img[src]",
    },
}

# --------------------------------------------------------------------------- #
#  Small utility: get text for an <a> element even if it has no inner text.
#  Many LiveJournal album links rely on title or aria-label instead.
# --------------------------------------------------------------------------- #
def _link_text(a):
    return (
        a.get_text(strip=True)
        or a.get("title", "").strip()
        or a.get("aria-label", "").strip()
        or a.get("alt", "").strip()
    )

# Recursively search a nested structure for the first occurrence of *key* -------
def _find_key(node, key):
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            result = _find_key(v, key)
            if result is not None:
                return result
    elif isinstance(node, list):
        for v in node:
            result = _find_key(v, key)
            if result is not None:
                return result
    return None


def select_universal_rules(url: str):
    """Return scraping rules for *url* if the domain is supported."""
    domain = urlparse(url).netloc.lower()
    for rules in DEFAULT_RULES.values():
        for d in rules.get("domains", []):
            if d in domain:
                return rules
    return None


def select_adapter_for_url(url: str) -> str:
    """Return the adapter key for *url* ("universal", "coppermine", or "4chan")."""
    url = url.strip()
    if url.lower().startswith("4chan"):
        return "4chan"
    domain = urlparse(url).netloc.lower()
    if (
        "4chan.org" in domain
        or "4channel.org" in domain
        or "4cdn.org" in domain
    ):
        return "4chan"
    if select_universal_rules(url):
        return "universal"
    return "coppermine"


def universal_get_album_pages(album_url, rules, page_cache, log=lambda msg: None, quick_scan=False):
    """Return all pagination URLs for a gallery using *rules*.

    Some galleries only show a subset of page links (e.g. ``?page=1``, ``?page=2``)
    around the current page.  To avoid missing pages we collect every pagination
    link found and also try to determine the maximum ``page`` number so we can
    generate the full list of page URLs.
    """
    html, _ = fetch_html_cached(album_url, page_cache, log=log, quick_scan=quick_scan)
    soup = BeautifulSoup(html, "html.parser")

    pages = set([album_url])
    selector = rules.get("pagination_selector")
    if selector:
        for a in soup.select(selector):
            purl = urljoin(album_url, a.get("href", ""))
            if purl:
                pages.add(purl)

        # Attempt to find the highest page number and create URLs up to that
        max_page = 1
        for a in soup.select(selector):
            href = a.get("href", "")
            if "page=" in href:
                try:
                    n = int(href.split("page=")[-1])
                    if n > max_page:
                        max_page = n
                except Exception:
                    continue
        for i in range(1, max_page + 1):
            pages.add(f"{album_url}?page={i}")

    return sorted(pages), soup


def universal_get_album_image_count(album_url, rules, page_cache=None):
    if page_cache is None:
        page_cache = {}
    pages, soup = universal_get_album_pages(album_url, rules, page_cache, quick_scan=False)
    count = 0
    thumb_sel = rules.get("thumb_selector")
    for idx, page in enumerate(pages):
        if idx == 0:
            current_soup = soup
        else:
            html, _ = fetch_html_cached(page, page_cache, log=lambda m: None, quick_scan=False)
            current_soup = BeautifulSoup(html, "html.parser")
        if thumb_sel:
            count += len(current_soup.select(thumb_sel))
    return count


def universal_discover_tree(root_url, rules, log=lambda msg: None, page_cache=None, quick_scan=True, cached_nodes=None):
    if page_cache is None:
        page_cache = {}
    html, _ = fetch_html_cached(root_url, page_cache, log=log, quick_scan=quick_scan)
    if isinstance(html, bytes):
        if html.startswith(b"\x1f\x8b"):
            try:
                import gzip
                html = gzip.decompress(html).decode("utf-8", "replace")
                log("[DEBUG] Decompressed gzipped body")
            except Exception as exc:
                log(f"WARNING: failed to decompress body: {exc}")
                html = html.decode("utf-8", "replace")
        else:
            html = html.decode("utf-8", "replace")
    if "livejournal.com" in root_url:
        log(f"[DEBUG] LiveJournal HTML length: {len(html)} chars")
        snippet = re.sub(r"\s+", " ", html[:800])
        log(f"[DEBUG] First 800 chars: {snippet}")
        low = snippet.lower()
        if len(html) < 2000 or any(x in low for x in ("enable javascript", "access denied")):
            log("[DEBUG] Warning: HTML looks like a challenge or error page")
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    gallery_title = title_tag.text.strip() if title_tag else root_url
    node = {
        "type": "category",
        "name": gallery_title,
        "url": root_url,
        "children": [],
        "specials": [],
        "albums": [],
    }
    albums = []

    # ------------------------------------------------------------------- #
    # 1) Generic discovery driven by CSS    (works for LiveJournal, etc.)
    # ------------------------------------------------------------------- #
    root_sel = rules.get("root_album_selector")
    if root_sel:
        for a in soup.select(root_sel):
            href = a.get("href", "")
            if not href:
                continue
            alb_url = urljoin(root_url, href)
            if any(x["url"] == alb_url for x in albums):
                continue
            albums.append({
                "type": "album",
                "name": _link_text(a) or os.path.basename(href.rstrip("/")),
                "url": alb_url,
                "image_count": "?",
            })

    # ------------------------------------------------------------------- #
    # 1-bis) LiveJournal fallback – parse the JSON in <script id="__NEXT_DATA__">
    # ------------------------------------------------------------------- #
    if not albums and "livejournal.com" in urlparse(root_url).netloc:
        data_tag = soup.find("script", id="__NEXT_DATA__")
        raw_json = None
        if data_tag:
            raw_json = (data_tag.string or data_tag.text or "").strip()
        if not raw_json:
            for scr in soup.find_all("script"):
                if not scr.string:
                    continue
                txt = scr.string
                m = re.search(r"__INITIAL_STATE__\s*=\s*({.*?});", txt, re.DOTALL)
                if m:
                    raw_json = m.group(1)
                    break
        if raw_json:
            try:
                state = json.loads(raw_json)
                candidate = (
                    _find_key(state, "albums")
                    or _find_key(state, "photoalbums")
                    or _find_key(state, "albumsList")
                    or []
                )
                if isinstance(candidate, dict):
                    iterable = candidate.values()
                else:
                    iterable = candidate
                count_before = len(albums)
                for alb in iterable:
                    if not isinstance(alb, dict):
                        continue
                    sec = str(alb.get("security", "")).lower()
                    if sec not in ("", "0", "public"):
                        continue
                    a_id = alb.get("id") or alb.get("albumId") or alb.get("aid")
                    if not a_id:
                        continue
                    title = (
                        alb.get("title")
                        or alb.get("name")
                        or f"Album {a_id}"
                    )
                    count = (
                        alb.get("itemsCount")
                        or alb.get("count")
                        or "?"
                    )
                    a_url = urljoin(root_url, f"/photo/album/{a_id}/")
                    if any(x["url"] == a_url for x in albums):
                        continue
                    albums.append(
                        {
                            "type": "album",
                            "name": title,
                            "url": a_url,
                            "image_count": count,
                        }
                    )
                if len(albums) > count_before:
                    log(
                        f"Found {len(albums) - count_before} LiveJournal albums via embedded JSON."
                    )
            except Exception as exc:
                log(
                    f"WARNING: LJ JSON parse failed ({len(raw_json)} bytes): {exc}"
                )

    # ------------------------------------------------------------------- #
    # 1-ter) LAST RESORT LJ fallback: regex-scan raw HTML for album IDs
    # ------------------------------------------------------------------- #
    if not albums and "livejournal.com" in urlparse(root_url).netloc:
        album_ids = set(re.findall(r"/photo/album/(\d+)", html))
        album_ids.update(re.findall(r'"albumId"\s*:\s*(\d+)', html))
        if album_ids:
            log(f"[DEBUG] Regex fallback found {len(album_ids)} candidate album IDs.")
            for aid in sorted(album_ids, key=int):
                a_url = urljoin(root_url, f"/photo/album/{aid}/")
                if any(x["url"] == a_url for x in albums):
                    continue
                name = None
                m = re.search(rf'(?:albumId"\s*:\s*{aid}[^{{}}]*?"title"\s*:\s*"([^"]+)")', html)
                if m:
                    name = m.group(1).strip()
                else:
                    m2 = re.search(rf'([A-Za-z0-9 _\-]{{3,80}})/photo/album/{aid}', html)
                    if m2:
                        cand = m2.group(1).rsplit('"', 1)[-1].strip()
                        if 3 < len(cand) < 80:
                            name = cand
                if not name:
                    name = f"Album {aid}"
                albums.append({
                    "type": "album",
                    "name": name,
                    "url": a_url,
                    "image_count": "?",
                })
            log(f"Added {len(album_ids)} LiveJournal albums via regex fallback.")

    # ------------------------------------------------------------------- #
    # 2) *ThePlace*-specific legacy code (kept unchanged, runs afterwards)
    # ------------------------------------------------------------------- #

    # (1) If on /photos, get all A-Z letter pages
    letter_links = []
    box_photo_letters = soup.find("div", class_="box_photo_letters")
    if box_photo_letters:
        for a in box_photo_letters.select("a.letter-item[href]"):
            l_url = urljoin(root_url, a['href'])
            letter_links.append(l_url)

    # (2) On main /photos also get the "Popular celebrities" directly
    for card in soup.select(".model-card__body a.model-card__body__title[href]"):
        alb_url = urljoin(root_url, card['href'])
        name = card.text.strip()

        count_str = None
        card_parent = card.find_parent(class_="model-card__body")
        if card_parent:
            img_count_div = card_parent.select_one(".model-card__body__data span")
            if img_count_div:
                count_str = img_count_div.next_sibling or img_count_div.text
        img_count = None
        if count_str:
            try:
                img_count = int("".join(filter(str.isdigit, count_str)))
            except Exception:
                img_count = None

        albums.append({
            "type": "album",
            "name": name,
            "url": alb_url,
            "image_count": img_count or "?",
        })

    # (3) For each letter page, fetch and add all celeb albums
    for letter_url in letter_links:
        l_html, _ = fetch_html_cached(letter_url, page_cache, log=log, quick_scan=quick_scan)
        l_soup = BeautifulSoup(l_html, "html.parser")
        for card in l_soup.select(".model-card__body a.model-card__body__title[href]"):
            alb_url = urljoin(letter_url, card['href'])
            name = card.text.strip()

            count_str = None
            card_parent = card.find_parent(class_="model-card__body")
            if card_parent:
                img_count_div = card_parent.select_one(".model-card__body__data span")
                if img_count_div:
                    count_str = img_count_div.next_sibling or img_count_div.text
            img_count = None
            if count_str:
                try:
                    img_count = int("".join(filter(str.isdigit, count_str)))
                except Exception:
                    img_count = None

            if any(x["url"] == alb_url for x in albums):
                continue
            albums.append({
                "type": "album",
                "name": name,
                "url": alb_url,
                "image_count": img_count or "?",
            })

    child_hash = compute_child_hash([], albums)
    if root_url in page_cache:
        page_cache[root_url]["child_hash"] = child_hash
    node["albums"] = albums
    node["child_hash"] = child_hash
    return node


def universal_get_all_candidate_images_from_album(album_url, rules, log=lambda msg: None, page_cache=None, quick_scan=True):
    if page_cache is None:
        page_cache = {}
    pages, soup = universal_get_album_pages(album_url, rules, page_cache, log=log, quick_scan=quick_scan)
    image_entries = []
    seen = set()
    thumb_sel = rules.get("thumb_selector")
    for idx, page in enumerate(pages):
        if idx == 0:
            current_soup = soup
        else:
            html, _ = fetch_html_cached(page, page_cache, log=log, quick_scan=quick_scan)
            current_soup = BeautifulSoup(html, "html.parser")
        for a in current_soup.select(thumb_sel or ""):
            detail_url = urljoin(page, a.get("href", ""))
            # Load the detail page to get the real image (not just the thumb)
            det_html, _ = fetch_html_cached(detail_url, page_cache, log=log, quick_scan=quick_scan)
            det_soup = BeautifulSoup(det_html, "html.parser")
            # Find the <a class="fancybox" href="..."> or the largest <img>
            full_img = None
            fancy = det_soup.select_one("a.fancybox[href]")
            if fancy:
                full_img = urljoin(detail_url, fancy["href"])
            if not full_img:
                img = det_soup.select_one("img")
                if img and "src" in img.attrs:
                    full_img = urljoin(detail_url, img["src"])
            if not full_img and rules.get("detail_image_selector"):
                tag = det_soup.select_one(rules["detail_image_selector"])
                if tag:
                    if tag.name == "img" and tag.get("src"):
                        full_img = urljoin(detail_url, tag["src"])
                    elif tag.get("href"):
                        full_img = urljoin(detail_url, tag["href"])
            # Use filename as entry name
            if full_img and full_img not in seen:
                seen.add(full_img)
                image_entries.append((os.path.basename(full_img), [full_img], detail_url))
    filtered_entries = []
    for name, candidates, referer in image_entries:
        main_url = candidates[0]
        fname = os.path.basename(main_url.split("?")[0])
        if is_ui_image(main_url, fname):
            log(f"Skipping UI/icon image: {fname}")
            continue
        if is_probably_thumbnail(main_url):
            log(f"Skipping small image (likely icon): {main_url}")
            continue
        filtered_entries.append((name, candidates, referer))

    entry_urls = []
    for _, candidates, _ in filtered_entries:
        entry_urls.extend(candidates)
    img_hash = compute_hash_from_list(entry_urls)
    if album_url in page_cache:
        page_cache[album_url]["images"] = filtered_entries
        page_cache[album_url]["image_hash"] = img_hash
    return filtered_entries

# -- 4chan helpers ------------------------------------------------------------

FOURCHAN_URL_RE = re.compile(
    r"(?:https?://)?(?:boards|a|i)\.4(?:chan|channel|cdn)\.org/([^/]+)/?(?:thread/(\d+))?",
    re.IGNORECASE,
)


def normalize_fourchan_url(url: str) -> str:
    """Return a canonical '4chan:board[/thread]' form for *url*."""
    url = url.strip()
    if not url:
        return "4chan"
    if url.lower().startswith("4chan"):
        return url.rstrip("/")
    m = FOURCHAN_URL_RE.search(url)
    if m:
        board = m.group(1)
        thread = m.group(2)
        return f"4chan:{board}/{thread}" if thread else f"4chan:{board}"
    return "4chan"


def fourchan_list_boards(log=lambda msg: None):
    """Return a list of boards from the 4chan API."""
    try:
        data = fetch_json_simple("https://a.4cdn.org/boards.json")
        return data.get("boards", [])
    except Exception as e:
        log(f"Error fetching boards: {e}")
        return []


def fourchan_list_threads(board, log=lambda msg: None):
    """Return threads for *board* using the catalog API."""
    board = board.strip().strip("/")
    try:
        data = fetch_json_simple(f"https://a.4cdn.org/{board}/catalog.json")
    except Exception as e:
        log(f"Error fetching catalog for /{board}/: {e}")
        return []
    threads = []
    for page in data:
        for th in page.get("threads", []):
            subject = th.get("sub") or ""
            teaser = th.get("com", "")
            teaser = re.sub(r"<.*?>", "", teaser)[:80] if teaser else ""
            title = subject or teaser or f"Thread {th['no']}"
            threads.append({
                "thread_id": th["no"],
                "subject": title,
                "image_count": th.get("images", 0),
            })
    return threads


def fourchan_thread_images(board, thread_id, log=lambda msg: None):
    """Return image entries from a specific thread."""
    board = board.strip().strip("/")
    try:
        data = fetch_json_simple(
            f"https://a.4cdn.org/{board}/thread/{thread_id}.json"
        )
    except Exception as e:
        log(f"Error fetching thread /{board}/{thread_id}: {e}")
        return []
    entries = []
    for post in data.get("posts", []):
        if "tim" in post and "ext" in post:
            fname = f"{post.get('filename', str(post['tim']))}{post['ext']}"
            url = f"https://i.4cdn.org/{board}/{post['tim']}{post['ext']}"
            entries.append((fname, [url], None))
    return entries


def fourchan_discover_tree(root_url, log=lambda msg: None, quick_scan=True):
    """Return a tree of boards and threads for 4chan."""
    canonical = normalize_fourchan_url(root_url)
    if canonical.lower() in {"4chan", "4chan:"}:
        boards = fourchan_list_boards(log=log)
        root = {
            "type": "category",
            "name": "4chan",
            "url": "4chan",
            "children": [],
            "specials": [],
            "albums": [],
        }
        for b in boards:
            root["children"].append({
                "type": "category",
                "name": f"/{b['board']}/ - {b['title']}",
                "url": f"4chan:{b['board']}",
                "children": [],
                "specials": [],
                "albums": [],
            })
        return root

    after_colon = canonical.split(":", 1)[-1]
    if "/" in after_colon:
        board, thread_id = after_colon.split("/", 1)
        thread_id = thread_id.strip()
        info = fourchan_thread_images(board, thread_id, log=log)
        subject = f"Thread {thread_id}"
        try:
            data = fetch_json_simple(
                f"https://a.4cdn.org/{board}/thread/{thread_id}.json"
            )
            if data.get("posts"):
                op = data["posts"][0]
                subject = op.get("sub") or re.sub(r"<.*?>", "", op.get("com", ""))[:80] or subject
        except Exception:
            pass
        safe_subj = sanitize_folder_name(subject.strip()) or thread_id
        node = {
            "type": "category",
            "name": f"/{board}/",
            "url": f"4chan:{board}",
            "children": [],
            "specials": [],
            "albums": [
                {
                    "type": "album",
                    "name": f"{subject} ({thread_id})",
                    "url": canonical,
                    "image_count": len(info),
                    "path": ["4chan", board, f"{safe_subj} ({thread_id})"],
                }
            ],
        }
        return node

    board = after_colon.strip()
    threads = fourchan_list_threads(board, log=log)
    node = {
        "type": "category",
        "name": f"/{board}/",
        "url": canonical,
        "children": [],
        "specials": [],
        "albums": [],
    }
    for th in threads:
        safe_subj = sanitize_folder_name(th['subject'].strip()) or th['thread_id']
        node["albums"].append(
            {
                "type": "album",
                "name": f"{th['subject']} ({th['thread_id']})",
                "url": f"4chan:{board}/{th['thread_id']}",
                "image_count": th.get("image_count", 0),
                "path": ["4chan", board, f"{safe_subj} ({th['thread_id']})"],
            }
        )
    return node


def fetch_html_cached(url, page_cache, log=lambda msg: None, quick_scan=True, indent=""):
    """Return HTML for *url* using the cache and indicate if it changed."""
    entry = page_cache.get(url)
    if entry and quick_scan:
        headers = {}
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]
        try:
            r = session.head(url, headers=headers, allow_redirects=True, timeout=10)
            if r.status_code == 304:
                entry["timestamp"] = time.time()
                log(f"{indent}Using cached page (304): {url}")
                return entry["html"], False
            if r.status_code == 200:
                et = r.headers.get("ETag")
                lm = r.headers.get("Last-Modified")
                if (et and et == entry.get("etag")) or (lm and lm == entry.get("last_modified")):
                    entry["timestamp"] = time.time()
                    log(f"{indent}Using cached page (headers match): {url}")
                    return entry["html"], False
        except Exception:
            pass
        # No expiration check: always use cached page if above conditions aren't met
        log(f"{indent}Using cached page: {url}")
        return entry["html"], False

    if entry and not quick_scan:
        log(f"{indent}Using cached page: {url}")
        return entry["html"], False

    resp = session.get(url)
    resp.raise_for_status()
    html = resp.text
    page_cache[url] = {
        "html": html,
        "timestamp": time.time(),
        "etag": resp.headers.get("ETag"),
        "last_modified": resp.headers.get("Last-Modified"),
    }
    log(f"{indent}Fetched: {url}")
    return html, True

BASE_URL = ""  # Will be set from GUI

# Single User-Agent used for all outbound HTTP requests. Some sites,
# including 4chan, are sensitive to persistent connections or unusual
# headers, so we keep it simple.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)

session = requests.Session()
session.headers.update({'User-Agent': DEFAULT_USER_AGENT})
session.headers.update({
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.livejournal.com/",
    "Connection": "keep-alive",
})

CACHE_DIR = ".coppermine_cache"
DOWNLOAD_WORKERS = 4


class SmartRateLimiter:
    """Predictive backoff that adapts before hitting rate limits."""

    def __init__(
        self,
        initial_delay=1.33,  # ~0.75 req/s as a safe starting point
        min_delay=0.25,
        max_delay=20.0,
        ramp_window=60,
        increase_factor=0.95,
        backoff_factor=2.0,
        allow_ramp=True,
    ):
        self.initial_delay = initial_delay
        self.delay = initial_delay
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.ramp_window = ramp_window
        self.increase_factor = increase_factor
        self.backoff_factor = backoff_factor
        self.allow_ramp = allow_ramp

        self.lock = threading.Lock()
        self.last_request = 0.0
        self.history = deque(maxlen=1000)  # (timestamp, status)
        self.last_429 = 0.0
        self.predicted_safe_delay = initial_delay

    def wait(self):
        with self.lock:
            now = time.time()
            wait_time = self.delay - (now - self.last_request)
        if wait_time > 0:
            time.sleep(wait_time)
        with self.lock:
            self.last_request = time.time()

    def record_result(self, status_code, retry_after=None):
        now = time.time()
        with self.lock:
            self.history.append((now, status_code))
            if status_code == 429:
                self.last_429 = now
                self.delay = min(
                    self.max_delay,
                    max(self.delay * self.backoff_factor, self.initial_delay),
                )
                if retry_after:
                    self.delay = max(self.delay, retry_after)
                self.predicted_safe_delay = self.delay
            else:
                window_start = now - self.ramp_window
                recent = [s for t, s in self.history if t >= window_start]
                if (
                    self.allow_ramp
                    and len(recent) > 20
                    and all(s != 429 for s in recent)
                ):
                    self.delay = max(self.min_delay, self.delay * self.increase_factor)
                    self.predicted_safe_delay = self.delay
                else:
                    self.delay = max(self.delay, self.predicted_safe_delay)

    def record_success(self):
        self.record_result(200)

    def record_error(self, retry_after=None, status_code=429):
        self.record_result(status_code, retry_after=retry_after)

    def reset(self):
        with self.lock:
            self.delay = self.initial_delay
            self.history.clear()
            self.last_429 = 0.0
            self.predicted_safe_delay = self.initial_delay


IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')
MEDIA_EXTS = ('.webm', '.mp4', '.gif')

# Separate limiters for images vs. videos/gifs. Images can be fetched
# fairly quickly, but 4chan is much stricter with webm/mp4/gif files.
image_rate_limiter = SmartRateLimiter(
    initial_delay=0.35,  # ~3 req/s start
    min_delay=0.20,
    max_delay=3.0,
    allow_ramp=True,
)
media_rate_limiter = SmartRateLimiter(
    initial_delay=4.0,  # ~0.33 req/s start
    min_delay=2.0,
    max_delay=20.0,
    allow_ramp=False,
)

def rate_limiter_for_url(url: str) -> SmartRateLimiter:
    """Return the appropriate rate limiter based on file extension."""
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    if ext in MEDIA_EXTS:
        return media_rate_limiter
    return image_rate_limiter

def get_soup(url):
    resp = session.get(url)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")

def fetch_json_simple(url: str):
    """Return parsed JSON from *url* using the requests session."""
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()

def sanitize_name(name: str) -> str:
    """Return a filesystem-safe version of *name*."""
    cleaned = "".join(c for c in name if c.isalnum() or c in " _-").strip()
    return cleaned or "unnamed"

def sanitize_folder_name(name: str) -> str:
    """Sanitize a folder name by replacing illegal characters."""
    return re.sub(r'[\\/*?:"<>|]', '_', name)

def get_downloaded_file_count(folder: str) -> int:
    """Return the number of downloaded media files in *folder*."""
    if not os.path.isdir(folder):
        return 0
    exts = ('*.jpg', '*.jpeg', '*.png', '*.gif', '*.webm', '*.mp4')
    count = 0
    for ext in exts:
        count += len(glob.glob(os.path.join(folder, ext)))
    return count

def get_album_image_count(album_url, page_cache=None):
    """Extract image count from album page (uses cache if present)."""
    if page_cache is None:
        page_cache = {}
    html, _ = fetch_html_cached(album_url, page_cache, log=lambda m: None, quick_scan=False)
    soup = BeautifulSoup(html, "html.parser")
    filecount = None
    info_div = soup.find(string=re.compile(r"files", re.I))
    if info_div:
        m = re.search(r"(\d+)\s+files?", info_div)
        if m:
            filecount = int(m.group(1))
    if not filecount:
        filecount = len(soup.find_all("a", href=re.compile(r"displayimage\.php")))
    return filecount

SPECIALS = [
    ("Last uploads", "lastup"),
    ("Last comments", "lastcom"),
    ("Most viewed", "topn"),
    ("Top rated", "toprated"),
    ("My Favorites", "favpics"),
    ("Random", "random"),
    ("By date", "date"),
    ("Search", "search"),
]

def discover_tree(root_url, parent_cat=None, parent_title=None, log=lambda msg: None, depth=0, visited=None, page_cache=None, quick_scan=True, cached_nodes=None):
    """Recursively build nested tree of categories, albums, and special albums.

    Parameters
    ----------
    root_url: str
        URL of the category/album to crawl.
    parent_cat: str | None
        Parent category id.
    parent_title: str | None
        Title of the parent category.
    log: callable
        Logger function accepting a single string argument. It should be thread
        safe if used from a thread.
    depth: int
        Current recursion depth (used for log indentation).
    visited: set | None
        Set of URLs that have already been crawled. Used to avoid recursion
        loops when categories link back to their parents.
    cached_nodes: dict | None
        Mapping of previously discovered nodes keyed by URL. Used for quick
        delta scans to skip unchanged subtrees.
    """
    if visited is None:
        visited = set()
    indent = "  " * depth
    if root_url in visited:
        log(f"{indent}↪ Already visited: {root_url}, skipping.")
        return None
    visited.add(root_url)
    log(f"{indent}→ Crawling: {root_url}")

    if page_cache is None:
        page_cache = {}

    html, _ = fetch_html_cached(root_url, page_cache, log=log, quick_scan=quick_scan, indent=indent)
    soup = BeautifulSoup(html, "html.parser")
    cat_title = parent_title or soup.title.text.strip()
    log(f"{indent}   In category: {cat_title}")

    match = re.search(r'cat=(\d+)', root_url)
    cat_id = match.group(1) if match else "0"

    node = {
        "type": "category",
        "name": cat_title,
        "url": root_url,
        "cat_id": cat_id,
        "children": [],
        "specials": [],
        "albums": [],
    }

    for label, key in SPECIALS:
        special_url = re.sub(
            r"index\.php(\?cat=\d+)?",
            f"thumbnails.php?album={key}{f'&cat={cat_id}' if cat_id != '0' else ''}",
            root_url,
        )
        node["specials"].append({"type": "special", "name": label, "url": special_url})

    subcats = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if "index.php?cat=" in href and not href.endswith(f"cat={cat_id}"):
            name = a.text.strip()
            if not name or name == cat_title:
                continue
            subcats.append((name, urljoin(root_url, href)))
            log(f"{indent}   Found subcategory: {name}")

    albums = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'thumbnails.php?album=' in href:
            name = a.text.strip()
            m = re.search(r'album=([a-zA-Z0-9_]+)', href)
            if not m or not name:
                continue
            album_id = m.group(1)
            if album_id in [key for _, key in SPECIALS]:
                continue
            album_url = urljoin(root_url, href)
            if cat_id != album_id:
                img_count = get_album_image_count(album_url, page_cache)
                albums.append({
                    "type": "album",
                    "name": name,
                    "url": album_url,
                    "image_count": img_count,
                })
                log(f"{indent}     Found album: {name} ({img_count} images)")

    child_hash = compute_child_hash(subcats, albums)
    if root_url in page_cache:
        page_cache[root_url]['child_hash'] = child_hash
    node['child_hash'] = child_hash

    cached_node = None
    if quick_scan and cached_nodes:
        cached_node = cached_nodes.get(root_url)
    if quick_scan and cached_node and cached_node.get('child_hash') == child_hash:
        log(f"{indent}   No changes detected; skipping subtree")
        if cached_node.get('name') != cat_title:
            cached_node = dict(cached_node)
            cached_node['name'] = cat_title
        return cached_node

    seen_cats = set()
    for name, subcat_url in subcats:
        cat_num = re.search(r'cat=(\d+)', subcat_url)
        if not cat_num:
            continue
        if cat_num.group(1) in seen_cats:
            continue
        seen_cats.add(cat_num.group(1))
        child = discover_tree(
            subcat_url,
            parent_cat=cat_id,
            parent_title=name,
            log=log,
            depth=depth + 1,
            visited=visited,
            page_cache=page_cache,
            quick_scan=quick_scan,
            cached_nodes=cached_nodes,
        )
        if child:
            node['children'].append(child)

    node['albums'] = albums

    log(
        f"{indent}   -> {len(node['children'])} subcategories | {len(node['albums'])} albums"
    )

    return node


def site_cache_path(root_url):
    h = hashlib.sha1(root_url.encode()).hexdigest()
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    return os.path.join(CACHE_DIR, f"{h}.json")


def load_page_cache(root_url):
    """Return the per-page cache and previously saved tree for *root_url*."""
    if select_adapter_for_url(root_url) == "4chan":
        return {}, None
    path = site_cache_path(root_url)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pages = data.get("pages", {})
        tree = data.get("tree")
        return pages, tree
    return {}, None


def save_page_cache(root_url, tree, pages):
    if select_adapter_for_url(root_url) == "4chan":
        return
    path = site_cache_path(root_url)
    gallery_title = tree.get("name") if tree else root_url
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": time.time(),
            "root_url": root_url,
            "gallery_title": gallery_title,
            "tree": tree,
            "pages": pages,
        }, f, indent=2)


def list_cached_galleries():
    """Return a list of cached galleries as (url, title) tuples."""
    galleries = []
    if not os.path.exists(CACHE_DIR):
        return galleries
    for filename in glob.glob(os.path.join(CACHE_DIR, "*.json")):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
            url = data.get("root_url")
            title = data.get("gallery_title", url)
            ts = data.get("timestamp", 0)
            if url:
                galleries.append((ts, url, title))
        except Exception:
            continue
    galleries.sort(key=lambda x: x[0], reverse=True)
    seen = set()
    ordered = []
    for _, url, title in galleries:
        if url not in seen:
            ordered.append((url, title))
            seen.add(url)
    return ordered


def _build_url_map(node, mapping=None):
    """Return a dictionary mapping URLs to nodes for *node* and its children."""
    if mapping is None:
        mapping = {}
    if not node:
        return mapping
    mapping[node.get("url")] = node
    for child in node.get("children", []):
        _build_url_map(child, mapping)
    return mapping


def discover_or_load_gallery_tree(root_url, log, quick_scan=True, force_refresh=False):
    """Discover galleries using cached pages when possible."""
    pages, cached_tree = load_page_cache(root_url)
    if force_refresh:
        log("Forcing full refresh (ignoring cache)...")
        pages = {}
    elif cached_tree:
        log("Cache found; using quick scan" if quick_scan else "Cache found.")
    cached_nodes = _build_url_map(cached_tree) if cached_tree else None
    site_type = select_adapter_for_url(root_url)
    log(f"[DEBUG] Adapter chosen: {site_type}")
    if site_type == "universal":
        rules = select_universal_rules(root_url)
        tree = universal_discover_tree(
            root_url,
            rules,
            log=log,
            page_cache=pages,
            quick_scan=quick_scan,
            cached_nodes=cached_nodes,
        )
    elif site_type == "4chan":
        tree = fourchan_discover_tree(
            root_url,
            log=log,
            quick_scan=quick_scan,
        )
    else:
        tree = discover_tree(
            root_url,
            log=log,
            page_cache=pages,
            quick_scan=quick_scan,
            cached_nodes=cached_nodes,
        )
    if site_type != "4chan":
        save_page_cache(root_url, tree, pages)
    return tree

def get_image_links_from_js(album_url):
    """Extract image URLs from the fb_imagelist JavaScript variable."""
    soup = get_soup(album_url)
    html = str(soup)
    js_var_pattern = re.compile(
        r'var\s+js_vars\s*=\s*(\{.*?"fb_imagelist".*?\});',
        re.DOTALL,
    )
    match = js_var_pattern.search(html)
    if not match:
        print(f"[DEBUG] js_vars not found in {album_url}")
        return []
    js_vars_json = match.group(1)
    try:
        if js_vars_json.endswith(";"):
            js_vars_json = js_vars_json[:-1]
        js_vars_json = js_vars_json.replace("'", '"')
        js_vars_json = re.sub(r'([,{])(\w+):', r'\1"\2":', js_vars_json)
        js_vars = json.loads(js_vars_json)
        fb_imagelist = js_vars.get("fb_imagelist", [])
        base = album_url.split("/thumbnails.php")[0]
        image_urls = []
        for img in fb_imagelist:
            src = img.get("src")
            if src and not src.startswith("http"):
                full_url = base + "/" + src.replace("\\/", "/").lstrip("/")
            else:
                full_url = src
            image_urls.append(full_url)
        return image_urls
    except Exception as e:
        print(f"[DEBUG] Error parsing fb_imagelist: {e}")
        return []


def extract_album_image_links(html, album_url):
    """Return list of image or displayimage links found in album HTML."""
    links = []
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "displayimage.php" in href and "pid=" in href:
            links.append(urljoin(album_url, href))
        elif re.search(r"\.(jpe?g|png|gif|webp|bmp|tiff)$", href, re.I):
            links.append(urljoin(album_url, href))

    js_var_pattern = re.compile(
        r'var\s+js_vars\s*=\s*(\{.*?"fb_imagelist".*?\});',
        re.DOTALL,
    )
    match = js_var_pattern.search(html)
    if match:
        js_vars_json = match.group(1)
        try:
            if js_vars_json.endswith(";"):
                js_vars_json = js_vars_json[:-1]
            js_vars_json = js_vars_json.replace("'", '"')
            js_vars_json = re.sub(r'([,{])(\w+):', r'\1"\2":', js_vars_json)
            js_vars = json.loads(js_vars_json)
            base = album_url.split("/thumbnails.php")[0]
            for img in js_vars.get("fb_imagelist", []):
                src = img.get("src")
                if src:
                    if src.startswith("http"):
                        links.append(src)
                    else:
                        links.append(base + "/" + src.replace("\\/", "/").lstrip("/"))
        except Exception:
            pass
    return list(dict.fromkeys(links))


def compute_album_image_hash(html, album_url):
    """Return a stable hash for the image links inside an album page."""
    links = extract_album_image_links(html, album_url)
    return compute_hash_from_list(links)


def get_base_for_relative_images(page_url):
    """Return the base URL for resolving relative image paths.

    Coppermine installs often live in subdirectories like ``/photos/`` or
    ``/gallery/``. Pages such as ``displayimage.php`` then reference images
    relative to that directory (e.g. ``albums/foo/bar.jpg``).  Without using
    this base, ``urljoin`` would incorrectly resolve those paths against the
    domain root and yield 404 errors.
    """
    # Example: https://example.com/photos/displayimage.php?id=1 -> https://example.com/photos/
    return page_url.rsplit('/', 1)[0] + '/'


def _fetch_fullsize_image(full_url, log):
    """Retrieve <img src> from a fullsize link or return the URL if it's an image."""
    try:
        resp = session.get(full_url)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "")
        if ctype.startswith("image") or ctype.startswith("video"):
            return [full_url]
        sub = BeautifulSoup(resp.text, "html.parser")
        base = get_base_for_relative_images(full_url)
        img = sub.find("img")
        if img and img.get("src"):
            return [urljoin(base, img["src"])]
    except Exception as e:
        log(f"[DEBUG] Failed to fetch fullsize {full_url}: {e}")
    return []


def extract_all_displayimage_candidates(displayimage_url, log=lambda msg: None):
    """Return every plausible original image URL from a displayimage.php page.

    Parses fancybox links, <img> tags, onclick handlers and data-* attributes
    to gather potential full-size image URLs.
    """
    try:
        soup = get_soup(displayimage_url)
    except Exception as e:
        log(f"[DEBUG] Failed to load {displayimage_url}: {e}")
        return []

    candidates = []
    base = get_base_for_relative_images(displayimage_url)

    # 0. Follow any explicit fullsize links or onclick targets first
    fullsize_links = []
    for tag in soup.find_all(["a", "img"]):
        if tag.get("href") and "fullsize" in tag["href"]:
            fullsize_links.append(urljoin(base, tag["href"]))
        oc = tag.get("onclick")
        if oc:
            m = re.search(r"(displayimage\.php[^'\"\s]*fullsize=1[^'\"\s]*)", oc)
            if m:
                fullsize_links.append(urljoin(base, m.group(1)))
    fullsize_links = list(dict.fromkeys(fullsize_links))
    for fl in fullsize_links:
        candidates.extend(_fetch_fullsize_image(fl, log))

    # 1. <a class="fancybox" href="...">
    for a in soup.find_all("a", href=True):
        classes = a.get("class", [])
        rels = a.get("rel", [])
        if "fancybox" in classes or "fancybox-thumb" in classes or "fancybox-thumb" in rels:
            href = a["href"]
            if re.search(r"\.(jpe?g|png|gif|webp|bmp|tiff)$", href, re.I):
                candidates.append(urljoin(base, href))

    # 2. <img class="image" src="...">
    img = soup.find("img", class_="image")
    if img and img.get("src"):
        candidates.append(urljoin(base, img["src"]))

    # 3. Largest <img> on the page
    imgs = soup.find_all("img")
    if imgs:
        imgs = sorted(
            imgs,
            key=lambda i: int(i.get("width", 0)) * int(i.get("height", 0)),
            reverse=True,
        )
        if imgs[0].get("src"):
            candidates.append(urljoin(base, imgs[0]["src"]))

    # 4. Any <a href="..."> that points directly to an image
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"\.(jpe?g|png|gif|webp|bmp|tiff)$", href, re.I):
            candidates.append(urljoin(base, href))

    # 5. Look for URLs inside onclick handlers or data-* attributes
    pattern = re.compile(r"['\"]([^'\"]+\.(?:jpe?g|png|gif|webp|bmp|tiff))['\"]", re.I)
    for tag in soup.find_all(["a", "img"]):
        oc = tag.get("onclick")
        if oc:
            for m in pattern.findall(oc):
                candidates.append(urljoin(base, m))
        for attr, val in tag.attrs.items():
            if attr.startswith("data") and isinstance(val, str) and re.search(r"\.(jpe?g|png|gif|webp|bmp|tiff)$", val, re.I):
                candidates.append(urljoin(base, val))

    # Deduplicate
    seen = set()
    unique_candidates = []
    for c in candidates:
        if c not in seen:
            unique_candidates.append(c)
            seen.add(c)

    def score(url):
        s = 0
        if "thumb" in url:
            s += 2
        if "normal_" in url:
            s += 1
        return s

    unique_candidates.sort(key=score)

    log(f"[DEBUG] Candidates from {displayimage_url}: {unique_candidates}")
    return unique_candidates


def get_all_candidate_images_from_album(album_url, log=lambda msg: None, visited=None, page_cache=None, quick_scan=True):
    """Return all candidate image URLs from an album.

    The return value is a list of tuples ``(display_title, [url1, url2, ...], referer)``
    where ``referer`` is the page URL the candidates were extracted from. Some
    galleries require that same page be supplied as the HTTP ``Referer`` when
    fetching the direct image URLs.
    """
    if visited is None:
        visited = set()
    if album_url in visited:
        return []
    visited.add(album_url)

    if page_cache is None:
        page_cache = {}

    html, changed = fetch_html_cached(album_url, page_cache, log=log, quick_scan=quick_scan)
    entry = page_cache.get(album_url, {})
    if quick_scan and not changed and entry.get("images"):
        log(f"[DEBUG] Using cached image list for {album_url}")
        return entry["images"]

    soup = BeautifulSoup(html, "html.parser")
    log = log or (lambda msg: None)
    image_entries = []
    unique_urls = set()

    # 1. Try JS fb_imagelist (if present, it's best)
    js_links = get_image_links_from_js(album_url)
    if js_links:
        log(f"Found {len(js_links)} images via fb_imagelist.")
        for idx, url in enumerate(js_links, 1):
            if url and url not in unique_urls:
                log(f"[DEBUG] fb_imagelist -> {url}")
                image_entries.append((f"Image {idx}", [url], album_url))
                unique_urls.add(url)

    # 2. Try all displayimage.php pages (these are "original" image pages)
    display_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # only consider unique displayimage.php?album=...&pid=...
        if "displayimage.php" in href and "album=" in href and "pid=" in href:
            display_links.append(urljoin(album_url, href))
    display_links = list(dict.fromkeys(display_links))  # dedupe
    if display_links:
        log(f"[DEBUG] Found {len(display_links)} displayimage links")

    for idx, dlink in enumerate(display_links, 1):
        candidates = extract_all_displayimage_candidates(dlink, log)
        good_candidates = [url for url in candidates if url not in unique_urls]
        if good_candidates:
            image_entries.append((f"Image (displayimage) {idx}", good_candidates, dlink))
            unique_urls.update(good_candidates)

    # 3. Direct <img> links that aren't thumbnails
    for img in soup.find_all("img", src=True):
        src = img["src"]
        if (
            "thumb" in src
            or "/thumbs/" in src
            or re.search(r"_(s|t|thumb)\.", src)
        ):
            continue
        width = int(img.get("width", 0))
        height = int(img.get("height", 0))
        if width and height and (width < 300 or height < 200):
            continue
        url = urljoin(album_url, src)
        if url and url not in unique_urls:
            log(f"[DEBUG] img tag -> {url}")
            image_entries.append((f"Image (img tag)", [url], album_url))
            unique_urls.add(url)

    # 4. Direct <a> links to image files
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"\.(jpe?g|png|webp|gif)$", href, re.I):
            url = urljoin(album_url, href)
            if url and url not in unique_urls:
                log(f"[DEBUG] a tag -> {url}")
                image_entries.append((f"Image (a tag)", [url], album_url))
                unique_urls.add(url)

    # 5. Pagination support (recurse for all "page=" links)
    pagelinks = set()
    for a in soup.find_all("a", href=True):
        if "page=" in a["href"]:
            pagelinks.add(urljoin(album_url, a["href"]))
    for pl in pagelinks:
        log(f"[DEBUG] pagination -> {pl}")
        image_entries.extend(
            get_all_candidate_images_from_album(
                pl, log=log, visited=visited, page_cache=page_cache, quick_scan=quick_scan
            )
        )

    if image_entries:
        log(f"Found {len(image_entries)} images total after all strategies.")
    else:
        log("No images found in album after all strategies.")

    filtered_entries = []
    for name, candidates, referer in image_entries:
        main_url = candidates[0]
        fname = os.path.basename(main_url.split("?")[0])
        if is_ui_image(main_url, fname):
            log(f"Skipping UI/icon image: {fname}")
            continue
        if is_probably_thumbnail(main_url):
            log(f"Skipping small image (likely icon): {main_url}")
            continue
        filtered_entries.append((name, candidates, referer))

    entry_urls = []
    for _, candidates, _ in filtered_entries:
        entry_urls.extend(candidates)
    img_hash = compute_hash_from_list(entry_urls)
    if album_url in page_cache:
        page_cache[album_url]["images"] = filtered_entries
        page_cache[album_url]["image_hash"] = img_hash

    return filtered_entries

def download_image_candidates(candidate_urls, output_dir, log, index=None, total=None,
                             album_stats=None, max_attempts=3, referer=None):
    """Try every candidate once, then retry the whole block if all fail.

    Parameters
    ----------
    candidate_urls : list[str]
        Possible URLs for the same image (largest first).
    output_dir : str
        Folder where the file should be saved.
    log : callable
        Logging function.
    index : int | None
        Index of the current file within the batch (for progress messages).
    total : int | None
        Total number of files in the batch.
    album_stats : dict | None
        Dictionary used to accumulate statistics across an album.
    max_attempts : int
        How many times to retry the whole block of URLs if all fail.
    referer : str | None
        Optional Referer header value to send with the request. Some galleries
        require a valid Referer to allow direct image downloads.
    """
    for block_attempt in range(1, max_attempts + 1):
        for candidate in candidate_urls:
            fname = os.path.basename(candidate.split("?")[0])
            fpath = os.path.join(output_dir, fname)
            if os.path.exists(fpath):
                log(f"Already downloaded: {fname}")
                return False
            try:
                headers = {'Referer': referer} if referer else {}
                log(f"[DEBUG] Attempting download: {candidate} (Referer: {referer})")
                rlim = rate_limiter_for_url(candidate)
                rlim.wait()
                r = session.get(candidate, headers=headers, stream=True, timeout=20)
                if r.status_code == 429:
                    retry = int(r.headers.get("Retry-After", "5"))
                    log(f"Rate limited. Backing off for {retry}s...")
                    rlim.record_error(retry_after=retry)
                    time.sleep(retry)
                    continue
                r.raise_for_status()
                ctype = r.headers.get("Content-Type", "")
                if not (ctype.startswith("image") or ctype.startswith("video")):
                    raise Exception(
                        f"URL does not return media: {candidate} (Content-Type: {ctype})"
                    )
                total_bytes = 0
                start_time = time.time()
                with open(fpath, "wb") as f:
                    for chunk in r.iter_content(1024 * 16):
                        if chunk:
                            f.write(chunk)
                            total_bytes += len(chunk)
                elapsed = time.time() - start_time
                speed = total_bytes / 1024 / elapsed if elapsed > 0 else 0
                size_str = (
                    f"{total_bytes / 1024 / 1024:.2f} MB" if total_bytes > 1024 * 1024 else f"{total_bytes / 1024:.1f} KB"
                )
                speed_str = (
                    f"{speed / 1024:.2f} MB/s" if speed > 1024 else f"{speed:.1f} KB/s"
                )
                prefix = ""
                if index is not None and total is not None:
                    prefix = f"File {index} of {total}: "
                log(f"{prefix}Downloaded: {fname} ({size_str}) at {speed_str}")
                if album_stats is not None:
                    album_stats['total_bytes'] += total_bytes
                    album_stats['total_time'] += elapsed
                    album_stats['downloaded'] += 1
                rlim.record_success()
                return True
            except Exception as e:
                log(f"Error downloading {candidate}: {e}")
                rlim = rate_limiter_for_url(candidate)
                rlim.record_error()
        if block_attempt < max_attempts:
            log(f"All candidate URLs failed for this image (attempt {block_attempt}/{max_attempts}), retrying all methods.")
            time.sleep(1.0)
        else:
            log(f"FAILED to download after {max_attempts} attempts: {candidate_urls}")
            if album_stats is not None:
                album_stats['errors'] += 1
    return False


def download_4chan_image_oldschool(
    url,
    output_dir,
    log,
    fname=None,
    index=None,
    total=None,
    album_stats=None,
    max_attempts=3,
):
    """Download a single 4chan image using urllib like older versions."""
    if fname is None:
        fname = os.path.basename(url)
    fpath = os.path.join(output_dir, fname)
    if os.path.exists(fpath):
        log(f"Already downloaded: {fname}")
        return False

    for attempt in range(1, max_attempts + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                },
            )
            rlim = rate_limiter_for_url(fname)
            rlim.wait()
            start_time = time.time()
            with urllib.request.urlopen(req, timeout=60) as resp, open(fpath, "wb") as out:
                if resp.status == 429:
                    retry = int(resp.headers.get("Retry-After", "5"))
                    log(
                        f"Rate limited. Backing off for {retry}s... (attempt {attempt}/{max_attempts})"
                    )
                    rlim.record_error(retry_after=retry)
                    time.sleep(retry)
                    continue
                total_bytes = 0
                while True:
                    chunk = resp.read(1024 * 16)
                    if not chunk:
                        break
                    out.write(chunk)
                    total_bytes += len(chunk)
            elapsed = time.time() - start_time
            speed = total_bytes / 1024 / elapsed if elapsed > 0 else 0
            size_str = (
                f"{total_bytes / 1024 / 1024:.2f} MB" if total_bytes > 1024 * 1024 else f"{total_bytes / 1024:.1f} KB"
            )
            speed_str = f"{speed / 1024:.2f} MB/s" if speed > 1024 else f"{speed:.1f} KB/s"
            prefix = f"File {index} of {total}: " if (index and total) else ""
            log(f"{prefix}Downloaded: {fname} ({size_str}) at {speed_str}")
            if album_stats is not None:
                album_stats['total_bytes'] += total_bytes
                album_stats['total_time'] += elapsed
                album_stats['downloaded'] += 1
            rlim.record_success()
            return True
        except Exception as e:
            log(f"Error downloading {url}: {e}")
            rlim = rate_limiter_for_url(fname)
            rlim.record_error()
            if attempt < max_attempts:
                log(f"Retrying {url} (attempt {attempt + 1}/{max_attempts})")
                time.sleep(1.0)
    if album_stats is not None:
        album_stats['errors'] += 1
    return False


def threaded_download_worker(download_queue, log, stop_flag):
    """Worker thread that downloads images from a queue."""
    while True:
        try:
            args = download_queue.get_nowait()
        except queue.Empty:
            break
        if stop_flag and stop_flag.is_set():
            download_queue.task_done()
            break
        (
            idx,
            album_name,
            outdir,
            candidate_urls,
            referer,
            total_images,
            mimic_human,
            stats,
        ) = args

        if stats['downloaded'] > 0:
            avg_time = stats['total_time'] / stats['downloaded']
            eta = avg_time * (total_images - idx + 1)
            eta_str = f" (ETA {int(eta)//60}:{int(eta)%60:02d})"
        else:
            eta_str = ""

        log(f"File {idx} of {total_images}{eta_str}... [{album_name}]")
        os.makedirs(outdir, exist_ok=True)

        was_downloaded = download_image_candidates(
            candidate_urls,
            outdir,
            log,
            index=idx,
            total=total_images,
            album_stats=stats,
            referer=referer,
        )

        if was_downloaded and mimic_human:
            time.sleep(random.uniform(0.7, 2.5))
            if idx % random.randint(18, 28) == 0:
                log("...taking a longer break to mimic human behavior...")
                time.sleep(random.uniform(5, 8))

        download_queue.task_done()

def rip_galleries(selected_albums, output_root, log, root_url, quick_scan=True, mimic_human=True, stop_flag=None):
    """Download all images from the selected albums with batch-wide progress (tries all candidates for each image)."""
    log(
        "Will download {} album(s): {}".format(
            len(selected_albums), ["/".join(a[2]) for a in selected_albums]
        )
    )

    site_type = select_adapter_for_url(root_url)
    if site_type == "4chan":
        log("Detected 4chan; using fast download path.")

        async def rip_4chan():
            stats = {
                'total_bytes': 0,
                'total_time': 0.0,
                'downloaded': 0,
                'errors': 0,
                'start_time': time.time(),
            }
            loop = asyncio.get_event_loop()
            for album_name, album_url, album_path in selected_albums:
                if stop_flag and stop_flag.is_set():
                    log("Download stopped by user.")
                    return
                log(f"\nScraping 4chan thread: {album_name}")
                board, tid = album_url.split(":", 1)[-1].split("/", 1)
                board = board.strip().strip("/")
                tid = tid.strip().split("/")[0]
                image_entries = fourchan_thread_images(board, tid, log=log)
                log(f"  Found {len(image_entries)} images in {album_name}.")
                outdir = os.path.join(
                    output_root,
                    *[sanitize_name(p) for p in album_path],
                )
                os.makedirs(outdir, exist_ok=True)
                total_images = len(image_entries)

                sem = asyncio.Semaphore(DOWNLOAD_WORKERS)

                async def worker(idx, fname, url):
                    if stop_flag and stop_flag.is_set():
                        return
                    async with sem:
                        await loop.run_in_executor(
                            None,
                            download_4chan_image_oldschool,
                            url,
                            outdir,
                            log,
                            fname,
                            idx,
                            total_images,
                            stats,
                        )
                    if mimic_human:
                        await asyncio.sleep(random.uniform(0.7, 2.5))
                        if idx % random.randint(18, 28) == 0:
                            log("...taking a longer break to mimic human behavior...")
                            await asyncio.sleep(random.uniform(5, 8))

                tasks = [
                    asyncio.create_task(worker(idx, fname, urls[0]))
                    for idx, (fname, urls, _) in enumerate(image_entries, 1)
                ]
                await asyncio.gather(*tasks)

            total_mb = stats['total_bytes'] / 1024 / 1024
            elapsed = time.time() - stats['start_time']
            avg_speed = total_mb / elapsed if elapsed > 0 else 0
            log(
                f"\nFinished all downloads!\n"
                f"  Downloaded {stats['downloaded']} files, {total_mb:.2f} MB in {elapsed:.1f}s\n"
                f"  Avg speed: {avg_speed:.2f} MB/s | Errors: {stats['errors']}"
            )
        asyncio.run(rip_4chan())
        return
    pages, tree = load_page_cache(root_url)
    rules = select_universal_rules(root_url) if site_type == "universal" else None
    download_queue = []
    for album_name, album_url, album_path in selected_albums:
        if stop_flag and stop_flag.is_set():
            log("Download stopped by user.")
            return
        log(f"\nScraping album: {album_name}")
        if site_type == "universal":
            image_entries = universal_get_all_candidate_images_from_album(
                album_url, rules, log=log, page_cache=pages, quick_scan=quick_scan
            )
        else:
            image_entries = get_all_candidate_images_from_album(
                album_url, log=log, page_cache=pages, quick_scan=quick_scan
            )
        log(f"  Found {len(image_entries)} images in {album_name}.")
        if not image_entries:
            continue
        for entry_name, candidates, referer in image_entries:
            download_queue.append((album_name, album_path, candidates, referer))

    total_images = len(download_queue)
    if total_images == 0:
        log("No images to download.")
        return

    stats = {
        'total_bytes': 0,
        'total_time': 0.0,
        'downloaded': 0,
        'errors': 0,
        'start_time': time.time(),
    }

    log(f"Total images in queue: {total_images}")

    q = queue.Queue()
    for idx, (album_name, album_path, candidate_urls, referer) in enumerate(download_queue, 1):
        outdir = os.path.join(
            output_root,
            *[sanitize_name(p) for p in album_path],
        )
        q.put((idx, album_name, outdir, candidate_urls, referer, total_images, mimic_human, stats))

    threads = []
    for _ in range(DOWNLOAD_WORKERS):
        t = threading.Thread(target=threaded_download_worker, args=(q, log, stop_flag))
        t.daemon = True
        t.start()
        threads.append(t)

    q.join()
    for t in threads:
        t.join()

    total_mb = stats['total_bytes'] / 1024 / 1024
    elapsed = time.time() - stats['start_time']
    avg_speed = total_mb / elapsed if elapsed > 0 else 0
    log(
        f"\nFinished all downloads!\n"
        f"  Downloaded {stats['downloaded']} files, {total_mb:.2f} MB in {elapsed:.1f}s\n"
        f"  Avg speed: {avg_speed:.2f} MB/s | Errors: {stats['errors']}"
    )
    save_page_cache(root_url, tree, pages)
# ---------- GUI ----------
class GalleryRipperApp(tb.Window):
    def __init__(self):
        super().__init__(themename="darkly")
        self.title("Coppermine Gallery Ripper")
        self.geometry("980x700")
        self.minsize(700, 480)
        self.albums_tree_data = None
        self.download_thread = None
        self.discovery_thread = None
        self.stop_flag = threading.Event()
        self.history_stack = []
        self.forward_stack = []

        self.url_var = tk.StringVar()
        self.path_var = tk.StringVar()
        settings = load_settings()
        if "download_folder" in settings:
            self.path_var.set(settings["download_folder"])

        control_frame = ttk.Frame(self)
        control_frame.pack(fill="x", padx=10, pady=(10, 0))

        urlf = ttk.Frame(control_frame)
        urlf.pack(fill="x")
        ttk.Label(urlf, text="Gallery Root URL:").pack(side="left")
        nav = ttk.Frame(urlf)
        nav.pack(side="left", padx=(5, 0))
        self.back_btn = ttk.Button(nav, text="<-", width=3, command=self.go_back, state="disabled")
        self.back_btn.pack(side="left")
        self.fwd_btn = ttk.Button(nav, text="->", width=3, command=self.go_forward, state="disabled")
        self.fwd_btn.pack(side="left")
        url_entry = ttk.Entry(urlf, textvariable=self.url_var, width=60)
        url_entry.pack(side="left", padx=5, expand=True, fill="x")
        ttk.Button(urlf, text="Discover Galleries", command=self.discover_albums).pack(side="left")
        ttk.Button(urlf, text="History", command=self.show_history).pack(side="left", padx=(5, 0))

        pathf = ttk.Frame(control_frame)
        pathf.pack(fill="x", pady=(8, 0))
        ttk.Label(pathf, text="Download Folder:").pack(side="left")
        ttk.Entry(pathf, textvariable=self.path_var, width=50).pack(side="left", padx=5, expand=True, fill="x")
        ttk.Button(pathf, text="Browse...", command=self.select_folder).pack(side="left")

        optionsf = ttk.Frame(control_frame)
        optionsf.pack(fill="x", pady=(4, 0))

        self.mimic_var = tk.BooleanVar(value=True)
        mimic_chk = ttk.Checkbutton(optionsf, text="Mimic human behavior", variable=self.mimic_var)
        mimic_chk.pack(side="left")

        self.show_specials_var = tk.BooleanVar(value=False)
        specials_chk = ttk.Checkbutton(optionsf, text="Show special galleries", variable=self.show_specials_var, command=self.refresh_tree)
        specials_chk.pack(side="left", padx=(10, 0))

        self.quick_scan_var = tk.BooleanVar(value=True)
        quick_chk = ttk.Checkbutton(optionsf, text="Quick scan", variable=self.quick_scan_var)
        quick_chk.pack(side="left", padx=(10, 0))

        self.download_workers_var = IntVar(value=DOWNLOAD_WORKERS)

        def update_worker_label(value):
            self.worker_label.config(text=f"Workers: {int(float(value))}")

        workerf = ttk.Frame(control_frame)
        workerf.pack(fill="x", pady=(4, 0))

        self.worker_label = ttk.Label(workerf, text=f"Workers: {DOWNLOAD_WORKERS}")
        self.worker_label.pack(side="left")

        self.worker_slider = ttk.Scale(
            workerf,
            from_=1,
            to=16,
            orient="horizontal",
            variable=self.download_workers_var,
            command=update_worker_label,
            length=160,
        )
        self.worker_slider.pack(side="left", padx=(5, 0))
        ToolTip(
            self.worker_slider,
            text="Avoid using more than 4 workers on 4chan or other sites to prevent rate limits or bans.",
        )
        btf = ttk.Frame(control_frame)
        btf.pack(fill="x", pady=(8, 0))

        left_buttons = ttk.Frame(btf)
        left_buttons.pack(side="left")
        ttk.Button(left_buttons, text="Select All", command=self.select_all_leaf_albums).pack(side="left")
        ttk.Button(left_buttons, text="Unselect All", command=self.unselect_all_leaf_albums).pack(side="left", padx=(5, 0))

        right_buttons = ttk.Frame(btf)
        right_buttons.pack(side="right")
        self.version_label = ttk.Label(right_buttons, text="Current version: " + get_git_version())
        self.version_label.pack(side="right", padx=6)
        ttk.Button(right_buttons, text="Update from Git", command=self.start_git_update).pack(side="right", padx=5)
        ttk.Button(right_buttons, text="Stop", command=self.stop_download).pack(side="right", padx=5)
        ttk.Button(right_buttons, text="Start Download", command=self.start_download, width=16).pack(side="right")

        # -- Search/filter frame --
        search_frame = ttk.Frame(control_frame)
        search_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(search_frame, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=30)
        search_entry.pack(side="left", fill="x", expand=True)
        self.search_var.trace_add("write", self.on_search)

        self._all_albums = None

        # Button for Coppermine/tree search
        self.search_all_btn = ttk.Button(
            search_frame,
            text="Search All Albums in Tree",
            command=self.search_all_albums_in_tree,
        )
        self.search_all_btn.pack(side="left", padx=5)
        self.search_all_btn.pack_forget()

        paned = ttk.Panedwindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        treeframe = ttk.LabelFrame(paned, text="Albums & Categories (expand/collapse and select leafs to download)")
        treeframe.pack(fill="both", expand=True, pady=0)

        self.tree = tb.Treeview(treeframe, show="tree", bootstyle="dark", selectmode="extended")
        ysb = ttk.Scrollbar(treeframe, orient="vertical", command=self.tree.yview)
        self.tree.config(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")

        self.tree["columns"] = ("sel",)
        self.tree.column("sel", width=30, anchor="center")
        self.tree.heading("sel", text="\u2714")

        self.selected_album_urls = set()
        self.item_to_album = {}
        self.item_to_category = {}
        self._prev_selection = set()

        logframe = ttk.LabelFrame(paned, text="Log")
        logframe.pack(fill="both", expand=False, pady=0)
        self.log_box = ScrolledText(logframe, height=10, state='disabled', font=("Consolas", 9),
                                    background="#181818", foreground="#EEEEEE", insertbackground="#EEEEEE")
        self.log_box.pack(fill="both", expand=True)

        paned.add(treeframe, weight=3)
        paned.add(logframe, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", self.on_tree_doubleclick)
        self.tree.bind("<Button-1>", self.on_tree_click)

    def select_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.path_var.set(folder)
            settings = load_settings()
            settings["download_folder"] = folder
            save_settings(settings)

    def show_history(self):
        history = list_cached_galleries()
        if not history:
            messagebox.showinfo("History", "No cached galleries found.")
            return
        win = tk.Toplevel(self)
        win.title("Recent Galleries")
        win.geometry("440x340")
        listbox = tk.Listbox(win, activestyle="dotbox")
        for url, title in history:
            listbox.insert(tk.END, f"{title} | {url}")
        listbox.pack(fill="both", expand=True, padx=10, pady=10)

        button_frame = ttk.Frame(win)
        button_frame.pack(pady=5)

        def do_select(event=None):
            selection = listbox.curselection()
            if selection:
                idx = selection[0]
                self.url_var.set(history[idx][0])
                win.destroy()
                self.discover_albums()

        def do_delete():
            selection = listbox.curselection()
            if not selection:
                messagebox.showinfo("Delete", "No gallery selected to delete.")
                return
            idx = selection[0]
            url, title = history[idx]
            # Ask for confirmation
            answer = messagebox.askyesno(
                "Delete Gallery Cache",
                f"Do you really want to delete the cached gallery:\n\n{title}\n{url}?"
            )
            if not answer:
                return
            # Remove the cache file
            import hashlib, os
            cache_file = os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest() + ".json")
            deleted = False
            if os.path.exists(cache_file):
                try:
                    os.remove(cache_file)
                    deleted = True
                except Exception as e:
                    messagebox.showerror("Delete", f"Error deleting file:\n{e}")
                    return
            else:
                messagebox.showinfo("Delete", "Cache file not found.")
            # If currently loaded gallery matches, clear the tree
            current = self.url_var.get().strip()
            if deleted and current == url:
                self.tree.delete(*self.tree.get_children())
                self.albums_tree_data = None
                self.selected_album_urls.clear()
                self.item_to_album.clear()
                self._prev_selection.clear()
                self.log(f"Gallery cache for '{title}' deleted and main tree cleared.")
            elif deleted:
                self.log(f"Gallery cache for '{title}' deleted.")
            # Refresh the list
            win.destroy()
            self.show_history()

        listbox.bind("<Double-1>", do_select)
        ttk.Button(button_frame, text="Select", command=do_select).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="Delete Selected", command=do_delete).pack(side="left")
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 5))

    def update_nav_buttons(self):
        state = "normal" if self.history_stack else "disabled"
        self.back_btn.configure(state=state)
        state = "normal" if self.forward_stack else "disabled"
        self.fwd_btn.configure(state=state)

    def go_back(self):
        if self.history_stack:
            current = self.url_var.get()
            self.forward_stack.append(current)
            prev_url = self.history_stack.pop()
            self.url_var.set(prev_url)
            self.discover_albums()
        self.update_nav_buttons()

    def go_forward(self):
        if self.forward_stack:
            current = self.url_var.get()
            self.history_stack.append(current)
            next_url = self.forward_stack.pop()
            self.url_var.set(next_url)
            self.discover_albums()
        self.update_nav_buttons()

    def log(self, msg):
        self.log_box.configure(state='normal')
        self.log_box.insert(tk.END, msg+'\n')
        self.log_box.see(tk.END)
        self.log_box.configure(state='disabled')
        self.update_idletasks()

    def thread_safe_log(self, msg):
        """Log from worker threads without touching tkinter from outside"""
        self.after(0, lambda m=msg: self.log(m))

    def insert_album_nodes(self, albums):
        """Insert the given albums into the tree under root."""
        self.tree.delete(*self.tree.get_children())
        self.selected_album_urls.clear()
        self.item_to_album.clear()
        self.item_to_category.clear()
        self._prev_selection.clear()
        for alb in albums:
            img_count = alb.get("image_count", "?")
            label = alb["name"]
            album_path = alb.get("path") or [alb["name"]]
            if isinstance(img_count, int):
                dl_folder = os.path.join(
                    self.path_var.get().strip(),
                    *[sanitize_name(p) for p in album_path],
                )
                existing = get_downloaded_file_count(dl_folder)
                missing = img_count - existing
                if missing <= 0:
                    label = f"{alb['name']} ({img_count}) [\u2713]"
                else:
                    label = f"{alb['name']} ({img_count}) [+{missing}]"
            else:
                label = f"{alb['name']} ({img_count})"
            alb_id = self.tree.insert(
                "",
                "end",
                text=f"\U0001F4F7 {label}",
                open=False,
            )
            self.tree.set(alb_id, "sel", "\u25A1")
            self.item_to_album[alb_id] = (alb['name'], alb['url'], album_path)

    def on_search(self, *args):
        """Filter albums in the tree based on search."""
        term = self.search_var.get().strip().lower()
        # Flat mode (universal adapter)
        if self._all_albums is not None:
            if not term:
                albums = self._all_albums
            else:
                albums = [a for a in self._all_albums if term in a['name'].lower()]
            self.insert_album_nodes(albums)
        else:
            # Tree mode: restore tree when clearing search
            if not term:
                self.insert_tree_root_safe(self.albums_tree_data)

    def search_all_albums_in_tree(self):
        """Collect all albums in the current tree and list them for searching."""

        def collect_albums(node):
            albums = list(node.get("albums", []))
            for child in node.get("children", []):
                albums.extend(collect_albums(child))
            return albums

        if self.albums_tree_data:
            all_albums = collect_albums(self.albums_tree_data)
            self._search_tree_albums = all_albums
            self.insert_album_nodes(all_albums)
            self.search_var.trace_add("write", self.on_tree_album_search)

    def on_tree_album_search(self, *args):
        term = self.search_var.get().strip().lower()
        albums = getattr(self, "_search_tree_albums", [])
        if not term:
            filtered = albums
        else:
            filtered = [a for a in albums if term in a['name'].lower()]
        self.insert_album_nodes(filtered)

    def insert_tree_root_safe(self, tree_data):
        # Clear any previous items and related state before inserting a new tree
        # to avoid stale item IDs causing TclError callbacks
        self.tree.selection_remove(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        self.selected_album_urls.clear()
        self.item_to_album.clear()
        self.item_to_category.clear()
        self._prev_selection.clear()
        if "albums" in tree_data and not tree_data.get("children") and not tree_data.get("specials"):
            self._all_albums = tree_data["albums"]
            self.insert_album_nodes(self._all_albums)
            self.search_all_btn.pack_forget()
            return
        # Tree mode
        self._all_albums = None
        self.insert_tree_node("", tree_data, [])
        self.search_all_btn.pack(side="left", padx=5)

    def discover_albums(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please enter the gallery URL.")
            return
        if self.discovery_thread and self.discovery_thread.is_alive():
            messagebox.showinfo("Discovery running", "Please wait for the current discovery to finish.")
            return
        self.thread_safe_log(f"Discovering albums from: {url}")
        self.tree.delete(*self.tree.get_children())
        quick = self.quick_scan_var.get()
        self.discovery_thread = threading.Thread(target=self.do_discover, args=(url, quick), daemon=True)
        self.discovery_thread.start()
        self.update_nav_buttons()

    def do_discover(self, url, quick):
        try:
            tree_data = discover_or_load_gallery_tree(url, self.thread_safe_log, quick_scan=quick, force_refresh=not quick)
            self.albums_tree_data = tree_data
            self.after(0, self.insert_tree_root_safe, tree_data)
            self.after(0, lambda: self.log("Discovery complete! (cached & partial refreshed if needed)"))
        except Exception as e:
            self.after(0, lambda: self.log(f"Discovery failed: {e}"))

    def insert_tree_node(self, parent, node, path=None):
        path = path or []
        label = node["name"]
        is_cat = node["type"] == "category"
        node_icon = "\U0001F4C1" if is_cat else "\U0001F4F7"
        node_id = self.tree.insert(parent, "end", text=f"{node_icon} {label}", open=False)
        self.tree.set(node_id, "sel", "\u25A1")
        node_path = path + [label]
        if is_cat:
            self.item_to_category[node_id] = (node["name"], node.get("url"), node_path)

        if self.show_specials_var.get():
            for spec in node.get("specials", []):
                spec_id = self.tree.insert(node_id, "end", text=f"\u2605 {spec['name']}", open=False)
                self.tree.set(spec_id, "sel", "\u25A1")
                self.item_to_album[spec_id] = (spec['name'], spec['url'], node_path + [spec['name']])

        for alb in node.get("albums", []):
            img_count = alb.get("image_count", "?")
            album_path = alb.get("path") or node_path + [alb['name']]
            label = alb['name']
            if isinstance(img_count, int):
                dl_folder = os.path.join(
                    self.path_var.get().strip(),
                    *[sanitize_name(p) for p in album_path],
                )
                existing = get_downloaded_file_count(dl_folder)
                missing = img_count - existing
                if missing <= 0:
                    label = f"{alb['name']} ({img_count}) [\u2713]"
                else:
                    label = f"{alb['name']} ({img_count}) [+{missing}]"
            else:
                label = f"{alb['name']} ({img_count})"
            alb_id = self.tree.insert(
                node_id,
                "end",
                text=f"\U0001F4F7 {label}",
                open=False,
            )
            self.tree.set(alb_id, "sel", "\u25A1")
            self.item_to_album[alb_id] = (alb['name'], alb['url'], album_path)

        for child in node.get("children", []):
            self.insert_tree_node(node_id, child, node_path)

    def refresh_tree(self):
        if self.albums_tree_data:
            self.insert_tree_root_safe(self.albums_tree_data)

    def on_tree_select(self, event=None):
        if getattr(self, "_ignore_next_select", False):
            self._ignore_next_select = False
            return

        previous_selection = getattr(self, "_prev_selection", set())
        current_selection = set()
        for item in self.tree.selection():
            try:
                self.tree.item(item)
            except tk.TclError:
                continue  # Skip items that vanished due to a tree refresh
            current_selection.add(item)

        newly_selected = current_selection - previous_selection
        newly_unselected = previous_selection - current_selection

        for item in newly_selected:
            text = self.tree.item(item, "text")
            if text.strip().startswith("\U0001F4C1"):
                self.select_descendants(item)
                self.tree.set(item, "sel", "\u2611")
            elif item in self.item_to_album:
                if item not in self.selected_album_urls:
                    self.selected_album_urls.add(item)
                    self.tree.set(item, "sel", "\u2611")

        for item in newly_unselected:
            text = self.tree.item(item, "text")
            if text.strip().startswith("\U0001F4C1"):
                self.unselect_descendants(item)
                self.tree.set(item, "sel", "\u25A1")
            elif item in self.selected_album_urls:
                self.selected_album_urls.discard(item)
                self.tree.set(item, "sel", "\u25A1")

        self._prev_selection = set(self.tree.selection())

    def select_descendants(self, parent):
        for child in self.tree.get_children(parent):
            text = self.tree.item(child, "text")
            if text.strip().startswith("\u2605") and not self.show_specials_var.get():
                continue
            if child in self.item_to_album:
                self.selected_album_urls.add(child)
                self.tree.selection_add(child)
                self.tree.set(child, "sel", "\u2611")
            self.select_descendants(child)

    def unselect_descendants(self, parent):
        for child in self.tree.get_children(parent):
            if child in self.selected_album_urls:
                self.selected_album_urls.discard(child)
                self.tree.selection_remove(child)
                self.tree.set(child, "sel", "\u25A1")
            self.unselect_descendants(child)

    def on_tree_doubleclick(self, event):
        item = self.tree.focus()
        if item in self.item_to_category:
            name, url, _ = self.item_to_category[item]
            after = url.split(":", 1)[-1]
            if select_adapter_for_url(url) == "4chan" and "/" not in after:
                if url != "4chan" and (
                    not self.history_stack or self.history_stack[-1] != self.url_var.get()
                ):
                    self.history_stack.append(self.url_var.get())
                    self.forward_stack.clear()
                self.url_var.set(url)
                self.discover_albums()
                self.update_nav_buttons()
                return
        if self.tree.get_children(item):
            self.tree.item(item, open=not self.tree.item(item, "open"))

    def on_tree_click(self, event):
        """Allow expand/collapse, but ignore selection when clicking the arrow."""
        item = self.tree.identify_row(event.y)
        region = self.tree.identify("region", event.x, event.y)
        if region == "tree":
            bbox = self.tree.bbox(item)
            if bbox and event.x < bbox[0] + 20:
                self._ignore_next_select = True
                return
        self._ignore_next_select = False

    def select_all_leaf_albums(self):
        for item in self.item_to_album:
            label = self.tree.item(item, "text")
            if label.lstrip().startswith("\u2605"):
                continue
            self.selected_album_urls.add(item)
            self.tree.selection_add(item)
            self.tree.set(item, "sel", "\u2611")

    def unselect_all_leaf_albums(self):
        for item in list(self.selected_album_urls):
            self.selected_album_urls.discard(item)
            self.tree.selection_remove(item)
            self.tree.set(item, "sel", "\u25A1")

    def start_download(self):
        if self.download_thread and self.download_thread.is_alive():
            messagebox.showinfo("Download running", "Please wait for the current download to finish.")
            return
        output_dir = self.path_var.get().strip()
        if not output_dir:
            messagebox.showwarning("Missing folder", "Please select a download folder.")
            return
        selected = [self.item_to_album[item] for item in self.selected_album_urls]
        if not selected:
            messagebox.showwarning("No albums selected", "Select at least one album to download.")
            return
        global DOWNLOAD_WORKERS
        DOWNLOAD_WORKERS = self.download_workers_var.get()
        self.log(f"Starting download of {len(selected)} albums with {DOWNLOAD_WORKERS} workers...")
        self.download_thread = threading.Thread(
            target=self.download_worker,
            args=(selected, output_dir, self.url_var.get().strip()),
            daemon=True,
        )
        self.download_thread.start()

    def stop_download(self):
        self.log("Stop requested by user. Attempting to stop current operation...")
        self.stop_flag.set()

    def download_worker(self, selected, output_dir, root_url):
        try:
            rip_galleries(
                selected,
                output_dir,
                self.log,
                root_url,
                quick_scan=self.quick_scan_var.get(),
                mimic_human=self.mimic_var.get(),
                stop_flag=self.stop_flag,
            )
            self.log("All downloads finished or stopped!")
        except Exception as e:
            self.log(f"Download error: {e}")
        finally:
            self.stop_flag.clear()

    def start_git_update(self):
        repo_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        ensure_https_remote(repo_dir)
        self.log("Checking for updates via git...")
        try:
            prev_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True
            ).strip()
            result = subprocess.run(
                ["git", "pull"],
                cwd=repo_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.log(result.stdout)
            if result.stderr:
                self.log("Error during git pull:\n" + result.stderr)
            new_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True
            ).strip()
            if prev_commit != new_commit:
                self.log("Update applied! Restarting app...")
                self.restart_app()
            else:
                self.log("No updates found. App is up to date.")
        except Exception as e:
            self.log(f"Update failed: {e}")
            messagebox.showerror("Update failed", str(e))

    def restart_app(self):
        python = sys.executable
        script = sys.argv[0]
        args = [python, script] + sys.argv[1:]
        self.log("Restarting app...")
        self.after(1000, lambda: subprocess.Popen(args))
        self.after(1500, self.quit)

if __name__ == "__main__":
    GalleryRipperApp().mainloop()
