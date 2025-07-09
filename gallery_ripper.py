import os
import threading
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
import ttkbootstrap as tb
from ttkbootstrap.constants import *
import re
import json
import time
import random

BASE_URL = ""  # Will be set from GUI
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
})

def get_soup(url):
    resp = session.get(url)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")

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

def discover_tree(root_url, parent_cat=None, parent_title=None, log=lambda msg: None, depth=0, visited=None):
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
    """
    if visited is None:
        visited = set()
    indent = "  " * depth
    if root_url in visited:
        log(f"{indent}↪ Already visited: {root_url}, skipping.")
        return None
    visited.add(root_url)
    log(f"{indent}→ Crawling: {root_url}")

    soup = get_soup(root_url)
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
        )
        if child:
            node['children'].append(child)

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
                node['albums'].append({"type": "album", "name": name, "url": album_url})
                log(f"{indent}     Found album: {name}")

    log(
        f"{indent}   -> {len(node['children'])} subcategories | {len(node['albums'])} albums"
    )

    return node

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


def get_main_image_from_displayimage(displayimage_url):
    """Return the main image URL from a displayimage.php page."""
    soup = get_soup(displayimage_url)
    img = soup.find("img", class_="image")
    if img and img.get("src"):
        return urljoin(displayimage_url, img["src"])

    imgs = soup.find_all("img")
    if not imgs:
        return None
    imgs = sorted(
        imgs,
        key=lambda i: int(i.get("width", 0)) * int(i.get("height", 0)),
        reverse=True,
    )
    if imgs[0].get("src"):
        return urljoin(displayimage_url, imgs[0]["src"])
    return None


def get_album_image_links(album_url, log=lambda msg: None, visited=None):
    """Adaptive extraction of all image URLs from a Coppermine album."""
    if visited is None:
        visited = set()
    if album_url in visited:
        return []
    visited.add(album_url)

    soup = get_soup(album_url)
    img_urls = set()

    # 1. JS variable fb_imagelist
    js_links = get_image_links_from_js(album_url)
    if js_links:
        log(f"Found {len(js_links)} images via fb_imagelist.")
        return js_links

    # 2. displayimage.php pages
    display_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "displayimage.php" in href and "album=" in href:
            display_links.append(urljoin(album_url, href))
    if display_links:
        log(f"Following {len(display_links)} displayimage.php links...")
        for link in display_links:
            try:
                img = get_main_image_from_displayimage(link)
                if img:
                    img_urls.add(img)
            except Exception as e:
                log(f"[DEBUG] Failed to get main image from {link}: {e}")
        if img_urls:
            return list(img_urls)

    # 3. Large <img> tags that are not thumbs
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
        img_urls.add(urljoin(album_url, src))
    if img_urls:
        log(f"Found {len(img_urls)} images via <img> tags.")
        return list(img_urls)

    # 4. Direct links to image files
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"\.(jpe?g|png|webp|gif)$", href, re.I):
            img_urls.add(urljoin(album_url, href))
    if img_urls:
        log(f"Found {len(img_urls)} images via direct <a> links.")
        return list(img_urls)

    # 5. Pagination recursion
    pagelinks = set()
    for a in soup.find_all("a", href=True):
        if "page=" in a["href"]:
            pagelinks.add(urljoin(album_url, a["href"]))
    for pl in pagelinks:
        img_urls.update(get_album_image_links(pl, log=log, visited=visited))
    if img_urls:
        log(f"Found {len(img_urls)} images after pagination.")
        return list(img_urls)

    log("No images found in album after all strategies.")
    return []

def download_image(img_url, output_dir, log, index=None, total=None, album_stats=None, max_retries=3):
    fname = os.path.basename(img_url.split("?")[0])
    fpath = os.path.join(output_dir, fname)
    if os.path.exists(fpath):
        log(f"Already downloaded: {fname}")
        if album_stats is not None:
            album_stats['downloaded'] += 1
        return
    for attempt in range(1, max_retries + 1):
        try:
            r = session.get(img_url, stream=True, timeout=20)
            r.raise_for_status()
            total_bytes = 0
            start_time = time.time()
            with open(fpath, "wb") as f:
                for chunk in r.iter_content(1024 * 16):
                    if chunk:
                        f.write(chunk)
                        total_bytes += len(chunk)
            elapsed = time.time() - start_time
            speed = total_bytes / 1024 / elapsed if elapsed > 0 else 0  # KB/s
            size_str = (
                f"{total_bytes / 1024 / 1024:.2f} MB"
                if total_bytes > 1024 * 1024
                else f"{total_bytes / 1024:.1f} KB"
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
            return
        except Exception as e:
            if attempt < max_retries:
                log(f"Error downloading {img_url}: {e} (retry {attempt}/{max_retries})")
                time.sleep(1.0)
            else:
                log(f"FAILED to download after {max_retries} tries: {img_url} ({e})")
                if album_stats is not None:
                    album_stats['errors'] += 1

def rip_galleries(selected_albums, output_root, log, mimic_human=True):
    """Download all images from the selected albums with batch-wide progress."""

    log(f"Will download {len(selected_albums)} album(s): {[a[0] for a in selected_albums]}")

    download_queue = []
    for album_name, album_url in selected_albums:
        log(f"\nScraping album: {album_name}")
        image_links = get_album_image_links(album_url, log=log)
        log(f"  Found {len(image_links)} images in {album_name}.")

        if not image_links:
            continue

        if mimic_human:
            image_links = image_links.copy()
            random.shuffle(image_links)

        for img_url in image_links:
            download_queue.append((album_name, album_url, img_url))

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

    for idx, (album_name, _, img_url) in enumerate(download_queue, 1):
        if stats['downloaded'] > 0:
            avg_time = stats['total_time'] / stats['downloaded']
            eta = avg_time * (total_images - idx + 1)
            eta_str = f" (ETA {int(eta)//60}:{int(eta)%60:02d})"
        else:
            eta_str = ""

        log(f"File {idx} of {total_images}{eta_str}... [{album_name}]")

        outdir = os.path.join(
            output_root,
            "".join([c for c in album_name if c.isalnum() or c in " _-"]).strip() or "unnamed"
        )
        os.makedirs(outdir, exist_ok=True)

        download_image(
            img_url,
            outdir,
            log,
            index=idx,
            total=total_images,
            album_stats=stats,
        )

        if mimic_human:
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

        self.url_var = tk.StringVar()
        self.path_var = tk.StringVar()

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        urlf = ttk.Frame(frm)
        urlf.pack(fill="x")
        ttk.Label(urlf, text="Gallery Root URL:").pack(side="left")
        url_entry = ttk.Entry(urlf, textvariable=self.url_var, width=60)
        url_entry.pack(side="left", padx=5, expand=True, fill="x")
        ttk.Button(urlf, text="Discover Galleries", command=self.discover_albums).pack(side="left")

        pathf = ttk.Frame(frm)
        pathf.pack(fill="x", pady=(8,0))
        ttk.Label(pathf, text="Download Folder:").pack(side="left")
        ttk.Entry(pathf, textvariable=self.path_var, width=50).pack(side="left", padx=5, expand=True, fill="x")
        ttk.Button(pathf, text="Browse...", command=self.select_folder).pack(side="left")

        self.mimic_var = tk.BooleanVar(value=True)
        mimic_chk = ttk.Checkbutton(pathf, text="Mimic human behavior", variable=self.mimic_var)
        mimic_chk.pack(side="left", padx=(10, 0))

        treeframe = ttk.LabelFrame(frm, text="Albums & Categories (expand/collapse and select leafs to download)")
        treeframe.pack(fill="both", expand=True, pady=10)

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

        btf = ttk.Frame(frm)
        btf.pack(fill="x")
        ttk.Button(btf, text="Select All", command=self.select_all_leaf_albums).pack(side="left")
        ttk.Button(btf, text="Unselect All", command=self.unselect_all_leaf_albums).pack(side="left")
        ttk.Button(btf, text="Start Download", command=self.start_download).pack(side="left", padx=8)

        self.log_box = ScrolledText(frm, height=10, state='disabled', font=("Consolas", 9),
                                    background="#181818", foreground="#EEEEEE", insertbackground="#EEEEEE")
        self.log_box.pack(fill="both", expand=False, pady=(10, 0))

        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", self.on_tree_doubleclick)

    def select_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.path_var.set(folder)

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
        self.tree.delete(*self.tree.get_children())
        self.insert_tree_node("", tree_data)

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
        self.discovery_thread = threading.Thread(target=self.do_discover, args=(url,), daemon=True)
        self.discovery_thread.start()

    def do_discover(self, url):
        try:
            tree_data = discover_tree(url, log=self.thread_safe_log, visited=set())
            self.albums_tree_data = tree_data
            self.after(0, self.insert_tree_root_safe, tree_data)
            self.after(0, lambda: self.log("Discovery complete! Expand nodes to explore and select albums to download."))
        except Exception as e:
            self.after(0, lambda: self.log(f"Discovery failed: {e}"))

    def insert_tree_node(self, parent, node):
        label = node["name"]
        is_cat = node["type"] == "category"
        node_icon = "\U0001F4C1" if is_cat else "\U0001F4F7"
        node_id = self.tree.insert(parent, "end", text=f"{node_icon} {label}", open=False)

        for spec in node.get("specials", []):
            spec_id = self.tree.insert(node_id, "end", text=f"\u2605 {spec['name']}", open=False)
            self.tree.set(spec_id, "sel", "\u25A1")
            self.item_to_album[spec_id] = (spec['name'], spec['url'])

        for alb in node.get("albums", []):
            alb_id = self.tree.insert(node_id, "end", text=f"\U0001F4F7 {alb['name']}", open=False)
            self.tree.set(alb_id, "sel", "\u25A1")
            self.item_to_album[alb_id] = (alb['name'], alb['url'])

        for child in node.get("children", []):
            self.insert_tree_node(node_id, child)

    def on_tree_select(self, event=None):
        for item in self.tree.selection():
            if item in self.item_to_album:
                if item not in self.selected_album_urls:
                    self.selected_album_urls.add(item)
                    self.tree.set(item, "sel", "\u2611")
            else:
                self.tree.selection_remove(item)
        for item in list(self.selected_album_urls):
            if item not in self.tree.selection():
                self.selected_album_urls.discard(item)
                self.tree.set(item, "sel", "\u25A1")

    def on_tree_doubleclick(self, event):
        item = self.tree.focus()
        if self.tree.get_children(item):
            self.tree.item(item, open=not self.tree.item(item, "open"))

    def select_all_leaf_albums(self):
        for item in self.item_to_album:
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
        self.download_thread = threading.Thread(target=self.download_worker, args=(selected, output_dir), daemon=True)
        self.download_thread.start()

    def download_worker(self, selected, output_dir):
        try:
            rip_galleries(selected, output_dir, self.log, mimic_human=self.mimic_var.get())
            self.log("All downloads finished!")
        except Exception as e:
            self.log(f"Download error: {e}")

if __name__ == "__main__":
    GalleryRipperApp().mainloop()
