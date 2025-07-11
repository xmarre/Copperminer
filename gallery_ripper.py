import os
import threading
import asyncio
import warnings
import argparse
import logging
from typing import Any
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
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

warnings.filterwarnings("ignore", category=ResourceWarning)

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


# -- Configuration ------------------------------------------------------------
settings = load_settings()
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--min-proxies", type=int, help="Minimum working proxies")
parser.add_argument(
    "--validation-concurrency",
    type=int,
    help="Concurrent proxy validation tasks",
)
parser.add_argument("--download-workers", type=int, help="Concurrent downloads")
parser.add_argument("--debug", action="store_true", help="Enable debug logging")
args, _ = parser.parse_known_args()

MIN_PROXIES = args.min_proxies or settings.get("min_proxies", 40)
VALIDATION_CONCURRENCY = args.validation_concurrency or settings.get(
    "proxy_validation_concurrency", 30
)
DOWNLOAD_WORKERS = args.download_workers or settings.get("download_workers", 1)

LOG_LEVEL = logging.DEBUG if args.debug else logging.INFO

# Configure the root logger early so debug/info messages from imported
# modules aren't dropped before the GUI attaches its own handler.
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    stream=sys.stdout,
)

# Logger for additional debug messages
logger = logging.getLogger("ripper.download")

# Global flag to control proxy usage
USE_PROXIES = settings.get("use_proxies", True)

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


def update_requirements(log=print):
    """Install or update dependencies listed in requirements.txt."""
    req_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
    if not os.path.exists(req_path):
        log("requirements.txt not found.")
        return
    log("Installing/updating requirements...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "-r", req_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.stdout:
        log(result.stdout)
    if result.returncode != 0:
        log("Error installing requirements:\n" + result.stderr)



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
        status, headers = run_async(
            async_http.head_with_proxy(url, get_pool_or_none(), headers=session_headers, timeout=5)
        )
        length = int(headers.get("content-length", 0))
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
}


def select_universal_rules(url: str):
    """Return scraping rules for *url* if the domain is supported."""
    domain = urlparse(url).netloc.lower()
    for rules in DEFAULT_RULES.values():
        for d in rules.get("domains", []):
            if d in domain:
                return rules
    return None


def select_adapter_for_url(url: str) -> str:
    """Return the adapter key for *url* ("universal" or "coppermine")."""
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
    soup = safe_soup(html)
    print(f"[DEBUG] Soup loaded, proxies: {USE_PROXIES}")

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
            current_soup = safe_soup(html)
        if thumb_sel:
            count += len(current_soup.select(thumb_sel))
    return count


def universal_discover_tree(root_url, rules, log=lambda msg: None, page_cache=None, quick_scan=True, cached_nodes=None):
    if page_cache is None:
        page_cache = {}
    html, _ = fetch_html_cached(root_url, page_cache, log=log, quick_scan=quick_scan)
    soup = safe_soup(html)
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
            current_soup = safe_soup(html)
        for a in current_soup.select(thumb_sel or ""):
            detail_url = urljoin(page, a.get("href", ""))
            # Load the detail page to get the real image (not just the thumb)
            det_html, _ = fetch_html_cached(detail_url, page_cache, log=log, quick_scan=quick_scan)
            det_soup = safe_soup(det_html)
            # Find the <a class="fancybox" href="..."> or the largest <img>
            full_img = None
            fancy = det_soup.select_one("a.fancybox[href]")
            if fancy:
                full_img = urljoin(detail_url, fancy["href"])
            if not full_img:
                img = det_soup.select_one("img")
                if img and "src" in img.attrs:
                    full_img = urljoin(detail_url, img["src"])
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
    logger.info(
        f"[DEBUG] Returning {len(filtered_entries)} entries from get_all_candidate_images_from_album, proxies: {USE_PROXIES}"
    )
    return filtered_entries


def fetch_html_cached(url, page_cache, log=lambda msg: None, quick_scan=True, indent=""):
    """Return HTML for *url* using the cache and indicate if it changed."""
    entry = page_cache.get(url)
    # Skip the costly HEAD check when proxies are enabled and a cached entry exists
    if entry and quick_scan and USE_PROXIES:
        log(f"{indent}Using cached page (skipping proxy HEAD): {url}")
        return entry["html"], False
    if entry and quick_scan:
        pool = get_pool_or_none()
        if USE_PROXIES and pool is None:
            log(f"{indent}Using cached page (proxy pool not ready): {url}")
            return entry["html"], False
        headers = {}
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]
        try:
            status, hdrs = run_async(
                async_http.head_with_proxy(
                    url, get_pool_or_none(), headers={**session_headers, **headers}, timeout=10
                )
            )
            if status == 304:
                entry["timestamp"] = time.time()
                log(f"{indent}Using cached page (304): {url}")
                return entry["html"], False
            if status == 200:
                et = hdrs.get("ETag")
                lm = hdrs.get("Last-Modified")
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

    pool = get_pool_or_none()
    log(f"{indent}[DEBUG] Fetching {url} using proxy: {pool}")
    html, hdrs = run_async(
        async_http.fetch_html(url, pool, headers=session_headers, timeout=15)
    )
    log(f"{indent}[DEBUG] Finished fetching {url}")
    page_cache[url] = {
        "html": html,
        "timestamp": time.time(),
        "etag": hdrs.get("ETag"),
        "last_modified": hdrs.get("Last-Modified"),
    }
    log(f"{indent}Fetched: {url}")
    return html, True

BASE_URL = ""  # Will be set from GUI
from proxy_manager import ProxyPool
import async_http

session_headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
}

proxy_pool = ProxyPool(
    min_proxies=MIN_PROXIES,
    fast_fill=DOWNLOAD_WORKERS,
    validation_concurrency=VALIDATION_CONCURRENCY,
)


def get_pool_or_none():
    """Return the proxy pool only when it's ready and proxies are enabled."""
    if not USE_PROXIES:
        return None
    if proxy_pool.pool_ready and proxy_pool.pool:
        return proxy_pool
    return None

def _proxy_thread():
    async def runner():
        await proxy_pool.refresh()
        await proxy_pool.start_auto_refresh(interval=600)
        await asyncio.Event().wait()
    asyncio.run(runner())

threading.Thread(target=_proxy_thread, daemon=True).start()

def run_async(coro):
    """Safely execute *coro* whether or not an event loop is running."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop, run a new one
        return asyncio.run(coro)
    else:
        if loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            return fut.result()
        return loop.run_until_complete(coro)

CACHE_DIR = ".coppermine_cache"

def safe_soup(html: str, parser: str = "html.parser", timeout: float = 5.0):
    """Parse *html* safely using BeautifulSoup with a timeout.

    When the builtin ``html.parser`` occasionally hangs on malformed input,
    we fall back to the ``html5lib`` parser instead of blocking indefinitely.
    """
    print(f"[DEBUG] safe_soup called, len(html): {len(html)}, proxies: {USE_PROXIES}")

    result: list[Any] = []

    def _worker() -> None:
        try:
            result.append(BeautifulSoup(html, parser))
        except Exception as e:  # pragma: no cover - best effort
            result.append(e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        logging.getLogger("ripper.parser").warning(
            "[WARN] html.parser timeout; falling back to html5lib"
        )
        return BeautifulSoup(html, "html5lib")

    soup_or_exc = result[0]
    if isinstance(soup_or_exc, Exception):
        logging.getLogger("ripper.parser").warning(
            "[WARN] html.parser failed (%s); using html5lib", soup_or_exc
        )
        return BeautifulSoup(html, "html5lib")
    print(f"[DEBUG] Soup loaded, proxies: {USE_PROXIES}")
    return soup_or_exc


def get_soup(url):
    html, _ = run_async(
        async_http.fetch_html(url, get_pool_or_none(), headers=session_headers)
    )
    return safe_soup(html)

def sanitize_name(name: str) -> str:
    """Return a filesystem-safe version of *name*."""
    cleaned = "".join(c for c in name if c.isalnum() or c in " _-").strip()
    return cleaned or "unnamed"

def get_album_image_count(album_url, page_cache=None):
    """Extract image count from album page (uses cache if present)."""
    if page_cache is None:
        page_cache = {}
    html, _ = fetch_html_cached(album_url, page_cache, log=lambda m: None, quick_scan=False)
    soup = safe_soup(html)
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
    soup = safe_soup(html)
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
    path = site_cache_path(root_url)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pages = data.get("pages", {})
        tree = data.get("tree")
        return pages, tree
    return {}, None


def save_page_cache(root_url, tree, pages):
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
    else:
        tree = discover_tree(
            root_url,
            log=log,
            page_cache=pages,
            quick_scan=quick_scan,
            cached_nodes=cached_nodes,
        )
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
    soup = safe_soup(html)
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
        html, hdrs = run_async(
            async_http.fetch_html(full_url, get_pool_or_none(), headers=session_headers)
        )
        if hdrs.get("Content-Type", "").startswith("image"):
            return [full_url]
        sub = safe_soup(html)
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
    logger.info(
        f"[DEBUG] Called get_all_candidate_images_from_album, proxies: {USE_PROXIES}"
    )
    if visited is None:
        visited = set()
    if album_url in visited:
        logger.info(
            f"[DEBUG] Returning EMPTY LIST from get_all_candidate_images_from_album (already visited), proxies: {USE_PROXIES}"
        )
        return []
    visited.add(album_url)

    if page_cache is None:
        page_cache = {}

    html, changed = fetch_html_cached(album_url, page_cache, log=log, quick_scan=quick_scan)
    logger.info(
        f"[DEBUG] Got HTML ({len(html)} bytes), proxies: {USE_PROXIES}"
    )
    with open("hang-debug.html", "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("[DEBUG] Saved hang-debug.html")
    logger.info(f"[DEBUG] Before soup call, proxies: {USE_PROXIES}")
    entry = page_cache.get(album_url, {})
    logger.info(
        f"[DEBUG] quick_scan={quick_scan}, changed={changed}, entry images={bool(entry.get('images'))}, proxies: {USE_PROXIES}"
    )
    if quick_scan and not changed and entry.get("images"):
        log(f"[DEBUG] Using cached image list for {album_url}")
        logger.info(
            f"[DEBUG] Returning {len(entry.get('images', []))} cached entries from get_all_candidate_images_from_album, proxies: {USE_PROXIES}"
        )
        return entry["images"]

    soup = safe_soup(html)
    logger.info(f"[DEBUG] After soup call, proxies: {USE_PROXIES}")
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
    print(f"[DEBUG] After fb_imagelist discovery, entries: {len(image_entries)}, proxies: {USE_PROXIES}")

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

    for n, dlink in enumerate(display_links, 1):
        log(f"[ALBUM] {n}/{len(display_links)} scan {dlink}")
        candidates = extract_all_displayimage_candidates(dlink, log)
        good_candidates = [url for url in candidates if url not in unique_urls]
        if good_candidates:
            image_entries.append((f"Image (displayimage) {n}", good_candidates, dlink))
            unique_urls.update(good_candidates)
    print(f"[DEBUG] After displayimage discovery, entries: {len(image_entries)}, proxies: {USE_PROXIES}")

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
    print(f"[DEBUG] After img tag discovery, entries: {len(image_entries)}, proxies: {USE_PROXIES}")

    # 4. Direct <a> links to image files
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"\.(jpe?g|png|webp|gif)$", href, re.I):
            url = urljoin(album_url, href)
            if url and url not in unique_urls:
                log(f"[DEBUG] a tag -> {url}")
                image_entries.append((f"Image (a tag)", [url], album_url))
                unique_urls.add(url)
    print(f"[DEBUG] After a tag discovery, entries: {len(image_entries)}, proxies: {USE_PROXIES}")

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
    print(f"[DEBUG] After pagination discovery, entries: {len(image_entries)}, proxies: {USE_PROXIES}")

    if image_entries:
        log(f"Found {len(image_entries)} images total after all strategies.")
    else:
        log("No images found in album after all strategies.")
    print(f"[DEBUG] After all discovery, found {len(image_entries)} entries, proxies: {USE_PROXIES}")

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

    logger.info(
        f"[DEBUG] Returning {len(filtered_entries)} entries from get_all_candidate_images_from_album, proxies: {USE_PROXIES}"
    )
    return filtered_entries

async def download_image_candidates(
    candidate_urls,
    output_dir,
    log,
    index=None,
    total=None,
    album_stats=None,
    max_attempts=3,
    referer=None,
):
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
                log(f"[DEBUG] Attempting download: {candidate} (Referer: {referer})")
                start_time = time.time()
                success = await async_http.download_with_proxy(
                    candidate, fpath, get_pool_or_none(), referer=referer
                )
                if not success:
                    raise Exception("Download failed")
                elapsed = time.time() - start_time
                total_bytes = os.path.getsize(fpath)
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
                return True
            except Exception as e:
                log(f"Error downloading {candidate}: {e}")
        if block_attempt < max_attempts:
            log(
                f"All candidate URLs failed for this image (attempt {block_attempt}/{max_attempts}), retrying all methods."
            )
            await asyncio.sleep(1.0)
        else:
            log(f"FAILED to download after {max_attempts} attempts: {candidate_urls}")
            if album_stats is not None:
                album_stats['errors'] += 1
    return False

async def rip_galleries(
    selected_albums,
    output_root,
    log,
    root_url,
    quick_scan=True,
    mimic_human=True,
    stop_flag=None,
):
    """Download all images from the selected albums with batch-wide progress (tries all candidates for each image)."""
    log(
        "Will download {} album(s): {}".format(
            len(selected_albums), ["/".join(a[2]) for a in selected_albums]
        )
    )
    pages, tree = load_page_cache(root_url)
    site_type = select_adapter_for_url(root_url)
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
        logger.info(
            f"[DEBUG] Queuing {len(image_entries)} images for download, proxies: {USE_PROXIES}"
        )
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

    sem = asyncio.Semaphore(DOWNLOAD_WORKERS)

    async def worker(idx, album_name, album_path, candidate_urls, referer):
        logger.info(
            f"[DEBUG] Starting download worker, proxies: {USE_PROXIES}, images: {len(candidate_urls)}"
        )
        if stop_flag and stop_flag.is_set():
            return
        if stats['downloaded'] > 0:
            avg_time = stats['total_time'] / stats['downloaded']
            eta = avg_time * (total_images - idx + 1)
            eta_str = f" (ETA {int(eta)//60}:{int(eta)%60:02d})"
        else:
            eta_str = ""

        log(f"File {idx} of {total_images}{eta_str}... [{album_name}]")

        outdir = os.path.join(
            output_root,
            *[sanitize_name(p) for p in album_path],
        )
        os.makedirs(outdir, exist_ok=True)

        async with sem:
            was_downloaded = await download_image_candidates(
                candidate_urls,
                outdir,
                log,
                index=idx,
                total=total_images,
                album_stats=stats,
                referer=referer,
            )

        if was_downloaded and mimic_human:
            await asyncio.sleep(random.uniform(0.7, 2.5))
            if idx % random.randint(18, 28) == 0:
                log("...taking a longer break to mimic human behavior...")
                await asyncio.sleep(random.uniform(5, 8))

    tasks = [
        asyncio.create_task(worker(idx, alb, path, urls, ref))
        for idx, (alb, path, urls, ref) in enumerate(download_queue, 1)
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
    save_page_cache(root_url, tree, pages)
# ---------- GUI ----------

class _GuiStream:
    def __init__(self, callback):
        self.callback = callback

    def write(self, msg):
        msg = msg.strip()
        if msg:
            self.callback(msg)

    def flush(self):
        pass

class GalleryRipperApp(tb.Window):
    def __init__(self):
        super().__init__(themename="darkly")
        self.title("Coppermine Gallery Ripper")
        self.geometry("1000x700")
        self.minsize(700, 480)
        self.albums_tree_data = None
        self.download_thread = None
        self.discovery_thread = None
        self.stop_flag = threading.Event()

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
        optionsf.pack(fill="x")

        self.mimic_var = tk.BooleanVar(value=True)
        mimic_chk = ttk.Checkbutton(optionsf, text="Mimic human behavior", variable=self.mimic_var)
        mimic_chk.pack(side="left", padx=(10, 0))

        self.show_specials_var = tk.BooleanVar(value=False)
        specials_chk = ttk.Checkbutton(optionsf, text="Show special galleries", variable=self.show_specials_var, command=self.refresh_tree)
        specials_chk.pack(side="left", padx=(10, 0))

        self.quick_scan_var = tk.BooleanVar(value=True)
        quick_chk = ttk.Checkbutton(optionsf, text="Quick scan", variable=self.quick_scan_var)
        quick_chk.pack(side="left", padx=(10, 0))

        self.verbose_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            optionsf,
            text="Verbose",
            variable=self.verbose_var,
            command=self._toggle_verbose,
        ).pack(side="left", padx=(10, 0))

        self.use_proxies_var = tk.BooleanVar(value=settings.get("use_proxies", True))
        proxies_chk = ttk.Checkbutton(
            optionsf,
            text="Use proxies",
            variable=self.use_proxies_var,
            command=self.on_use_proxies_toggle,
        )
        proxies_chk.pack(side="left", padx=(10, 0))
        btf = ttk.Frame(control_frame)
        btf.pack(fill="x", pady=(8, 0))
        ttk.Button(btf, text="Select All", command=self.select_all_leaf_albums).pack(side="left")
        ttk.Button(btf, text="Unselect All", command=self.unselect_all_leaf_albums).pack(side="left")
        ttk.Button(btf, text="Start Download", command=self.start_download).pack(side="left", padx=8)
        ttk.Button(btf, text="Stop", command=self.stop_download).pack(side="left", padx=8)
        ttk.Button(btf, text="Update from Git", command=self.start_git_update).pack(side="left", padx=8)
        self.version_label = ttk.Label(btf, text="Current version: " + get_git_version())
        self.version_label.pack(side="left", padx=6)

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
        self._prev_selection = set()

        logframe = ttk.LabelFrame(paned, text="Log")
        logframe.pack(fill="both", expand=False, pady=0)
        self.log_box = ScrolledText(logframe, height=10, state='disabled', font=("Consolas", 9),
                                    background="#181818", foreground="#EEEEEE", insertbackground="#EEEEEE")
        self.log_box.pack(fill="both", expand=True)

        self.log_stream = _GuiStream(self.thread_safe_log)
        root_logger = logging.getLogger()
        # Replace any existing handlers (e.g. the initial stdout handler) so
        # log messages are routed to the GUI textbox once it exists.
        for h in list(root_logger.handlers):
            root_logger.removeHandler(h)
        handler = logging.StreamHandler(self.log_stream)
        handler.setLevel(LOG_LEVEL)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s | %(message)s"))
        root_logger.addHandler(handler)
        root_logger.setLevel(LOG_LEVEL)

        self.proxy_frame = ttk.LabelFrame(paned, text="Proxy Pool")
        self.proxy_frame.pack(fill="x", padx=10, pady=6)
        self.proxy_status = tk.StringVar()
        ttk.Label(self.proxy_frame, textvariable=self.proxy_status).pack(side="left", padx=4)
        self.proxy_listbox = tk.Listbox(self.proxy_frame, height=4, width=36, font=("Consolas", 8))
        self.proxy_listbox.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(self.proxy_frame, text="Refresh Proxies", command=self.manual_refresh_proxies).pack(side="right", padx=6)

        paned.add(treeframe, weight=3)
        paned.add(logframe, weight=1)
        paned.add(self.proxy_frame, weight=0)

        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", self.on_tree_doubleclick)
        self.tree.bind("<Button-1>", self.on_tree_click)

        self.start_proxy_status_updater()

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
        self._prev_selection.clear()
        for alb in albums:
            img_count = alb.get("image_count", "?")
            alb_id = self.tree.insert(
                "",
                "end",
                text=f"\U0001F4F7 {alb['name']} ({img_count})",
                open=False,
            )
            self.tree.set(alb_id, "sel", "\u25A1")
            self.item_to_album[alb_id] = (alb['name'], alb['url'], [alb['name']])

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

        if self.show_specials_var.get():
            for spec in node.get("specials", []):
                spec_id = self.tree.insert(node_id, "end", text=f"\u2605 {spec['name']}", open=False)
                self.tree.set(spec_id, "sel", "\u25A1")
                self.item_to_album[spec_id] = (spec['name'], spec['url'], node_path + [spec['name']])

        for alb in node.get("albums", []):
            img_count = alb.get("image_count", "?")
            alb_id = self.tree.insert(
                node_id,
                "end",
                text=f"\U0001F4F7 {alb['name']} ({img_count})",
                open=False,
            )
            self.tree.set(alb_id, "sel", "\u25A1")
            self.item_to_album[alb_id] = (alb['name'], alb['url'], node_path + [alb['name']])

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
        self.log(f"Starting download of {len(selected)} albums...")
        self.download_thread = threading.Thread(
            target=self.download_worker,
            args=(selected, output_dir, self.url_var.get().strip()),
            daemon=True,
        )
        self.download_thread.start()

    def stop_download(self):
        self.log("Stop requested by user. Attempting to stop current operation...")
        self.stop_flag.set()

    def manual_refresh_proxies(self):
        def do_refresh():
            self.thread_safe_log("Manual proxy pool refresh requested.")
            run_async(proxy_pool.refresh())
            self.after(0, self.update_proxy_status)
        threading.Thread(target=do_refresh, daemon=True).start()

    def on_use_proxies_toggle(self):
        """Handle toggling of the Use proxies checkbox."""
        global USE_PROXIES
        USE_PROXIES = self.use_proxies_var.get()
        settings = load_settings()
        settings["use_proxies"] = USE_PROXIES
        save_settings(settings)

    def _toggle_verbose(self):
        lvl = logging.DEBUG if self.verbose_var.get() else logging.INFO
        root = logging.getLogger()
        root.setLevel(lvl)
        for h in root.handlers:
            h.setLevel(lvl)

    def update_proxy_status(self):
        count = len(proxy_pool.pool)
        if count == 0:
            self.proxy_status.set("Searching for working proxies… (may take a minute)")
        else:
            ts = "N/A"
            if proxy_pool.last_checked:
                ts = time.strftime("%H:%M:%S", time.localtime(proxy_pool.last_checked))
            self.proxy_status.set(
                f"Proxies available: {count} | Last checked: {ts}"
            )
        if hasattr(self, "proxy_listbox"):
            self.proxy_listbox.delete(0, tk.END)
            for proxy in proxy_pool.pool[:15]:
                self.proxy_listbox.insert(tk.END, proxy)

    def start_proxy_status_updater(self):
        self.update_proxy_status()
        self.after(5000, self.start_proxy_status_updater)

    def download_worker(self, selected, output_dir, root_url):
        try:
            run_async(
                rip_galleries(
                    selected,
                    output_dir,
                    self.thread_safe_log,
                    root_url,
                    quick_scan=self.quick_scan_var.get(),
                    mimic_human=self.mimic_var.get(),
                    stop_flag=self.stop_flag,
                )
            )
            self.thread_safe_log("All downloads finished or stopped!")
        except Exception as e:
            self.thread_safe_log(f"Download error: {e}")
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
            update_requirements(log=self.log)
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
