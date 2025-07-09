import os
import threading
import requests
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
        "pagination_selector": "div.pagination a[href]",
        "thumb_selector": "a[href^='pic-']",
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
    """Return all pagination URLs for a gallery using *rules*."""
    html, _ = fetch_html_cached(album_url, page_cache, log=log, quick_scan=quick_scan)
    soup = BeautifulSoup(html, "html.parser")
    pages = [album_url]
    selector = rules.get("pagination_selector")
    if selector:
        for a in soup.select(selector):
            purl = urljoin(album_url, a.get("href", ""))
            if purl not in pages:
                pages.append(purl)
    return pages, soup


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
    album_sel = rules.get("root_album_selector")
    for a in soup.select(album_sel or ""):
        href = a.get("href")
        if not href:
            continue
        alb_url = urljoin(root_url, href)
        name = a.text.strip() or a.get("title", "").strip()
        if not name:
            continue
        if alb_url.endswith("/"):
            alb_url = alb_url.rstrip("/")
        if any(x["url"] == alb_url for x in albums):
            continue
        img_count = universal_get_album_image_count(alb_url, rules, page_cache)
        albums.append({
            "type": "album",
            "name": name,
            "url": alb_url,
            "image_count": img_count,
        })
        log(f"Found gallery: {name} ({img_count} images)")

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
    detail_sel = rules.get("detail_image_selector")
    for idx, page in enumerate(pages):
        if idx == 0:
            current_soup = soup
        else:
            html, _ = fetch_html_cached(page, page_cache, log=log, quick_scan=quick_scan)
            current_soup = BeautifulSoup(html, "html.parser")
        for a in current_soup.select(thumb_sel or ""):
            detail_url = urljoin(page, a.get("href", ""))
            det_html, _ = fetch_html_cached(detail_url, page_cache, log=log, quick_scan=quick_scan)
            det_soup = BeautifulSoup(det_html, "html.parser")
            big = det_soup.select_one(detail_sel or "")
            if big:
                img_url = urljoin(detail_url, big.get("href", ""))
                if img_url and img_url not in seen:
                    seen.add(img_url)
                    image_entries.append((os.path.basename(img_url), [img_url], detail_url))
    entry_urls = [url for _, [url], _ in image_entries]
    img_hash = compute_hash_from_list(entry_urls)
    if album_url in page_cache:
        page_cache[album_url]["images"] = image_entries
        page_cache[album_url]["image_hash"] = img_hash
    return image_entries


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
        if time.time() - entry.get("timestamp", 0) < CACHE_EXPIRY:
            log(f"{indent}Using cached page (not expired): {url}")
            return entry["html"], False

    if entry and not quick_scan and time.time() - entry.get("timestamp", 0) < CACHE_EXPIRY:
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
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
})

CACHE_DIR = ".coppermine_cache"
CACHE_EXPIRY = 60 * 60 * 24 * 7  # 1 week

def get_soup(url):
    resp = session.get(url)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")

def sanitize_name(name: str) -> str:
    """Return a filesystem-safe version of *name*."""
    cleaned = "".join(c for c in name if c.isalnum() or c in " _-").strip()
    return cleaned or "unnamed"

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
    path = site_cache_path(root_url)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pages = data.get("pages", {})
        tree = data.get("tree")
        timestamp = data.get("timestamp", 0)
        if time.time() - timestamp < CACHE_EXPIRY:
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
        if resp.headers.get("Content-Type", "").startswith("image"):
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

    entry_urls = []
    for _, candidates, _ in image_entries:
        entry_urls.extend(candidates)
    img_hash = compute_hash_from_list(entry_urls)
    if album_url in page_cache:
        page_cache[album_url]["images"] = image_entries
        page_cache[album_url]["image_hash"] = img_hash

    return image_entries

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
                r = session.get(candidate, headers=headers, stream=True, timeout=20)
                r.raise_for_status()
                if not r.headers.get("Content-Type", "").startswith("image"):
                    raise Exception(f"URL does not return image: {candidate} (Content-Type: {r.headers.get('Content-Type')})")
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
                return True
            except Exception as e:
                log(f"Error downloading {candidate}: {e}")
        if block_attempt < max_attempts:
            log(f"All candidate URLs failed for this image (attempt {block_attempt}/{max_attempts}), retrying all methods.")
            time.sleep(1.0)
        else:
            log(f"FAILED to download after {max_attempts} attempts: {candidate_urls}")
            if album_stats is not None:
                album_stats['errors'] += 1
    return False

def rip_galleries(selected_albums, output_root, log, root_url, quick_scan=True, mimic_human=True, stop_flag=None):
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

    for idx, (album_name, album_path, candidate_urls, referer) in enumerate(download_queue, 1):
        if stop_flag and stop_flag.is_set():
            log("Download stopped by user.")
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

        self.mimic_var = tk.BooleanVar(value=True)
        mimic_chk = ttk.Checkbutton(pathf, text="Mimic human behavior", variable=self.mimic_var)
        mimic_chk.pack(side="left", padx=(10, 0))

        self.show_specials_var = tk.BooleanVar(value=False)
        specials_chk = ttk.Checkbutton(pathf, text="Show special galleries", variable=self.show_specials_var, command=self.refresh_tree)
        specials_chk.pack(side="left", padx=(10, 0))

        self.quick_scan_var = tk.BooleanVar(value=True)
        quick_chk = ttk.Checkbutton(pathf, text="Quick scan", variable=self.quick_scan_var)
        quick_chk.pack(side="left", padx=(10, 0))
        btf = ttk.Frame(control_frame)
        btf.pack(fill="x", pady=(8, 0))
        ttk.Button(btf, text="Select All", command=self.select_all_leaf_albums).pack(side="left")
        ttk.Button(btf, text="Unselect All", command=self.unselect_all_leaf_albums).pack(side="left")
        ttk.Button(btf, text="Start Download", command=self.start_download).pack(side="left", padx=8)
        ttk.Button(btf, text="Stop", command=self.stop_download).pack(side="left", padx=8)
        ttk.Button(btf, text="Update from Git", command=self.start_git_update).pack(side="left", padx=8)
        self.version_label = ttk.Label(btf, text="Current version: " + get_git_version())
        self.version_label.pack(side="left", padx=6)

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

    def log(self, msg):
        self.log_box.configure(state='normal')
        self.log_box.insert(tk.END, msg+'\n')
        self.log_box.see(tk.END)
        self.log_box.configure(state='disabled')
        self.update_idletasks()

    def thread_safe_log(self, msg):
        """Log from worker threads without touching tkinter from outside"""
        self.after(0, lambda m=msg: self.log(m))

    def insert_tree_root_safe(self, tree_data):
        # Clear any previous items and related state before inserting a new tree
        # to avoid stale item IDs causing TclError callbacks
        self.tree.selection_remove(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        self.selected_album_urls.clear()
        self.item_to_album.clear()
        self._prev_selection.clear()
        self.insert_tree_node("", tree_data, [])

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
