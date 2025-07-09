import os
import threading
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from ttkthemes import ThemedTk
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

def find_albums(url, visited=None):
    """Recursively find all albums (gallery pages) starting from url."""
    if visited is None:
        visited = set()
    soup = get_soup(url)
    albums = []

    # Coppermine: Albums are usually "thumbnails.php?album=NN"
    for a in soup.find_all('a', href=True):
        href = a['href']
        name = a.text.strip()
        if 'thumbnails.php?album=' in href and href not in visited and name:
            full_url = urljoin(url, href)
            albums.append((name, full_url))
            visited.add(href)
    # Find deeper categories
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'index.php?cat=' in href:
            full_url = urljoin(url, href)
            if full_url not in visited:
                visited.add(full_url)
                albums.extend(find_albums(full_url, visited))
    return albums

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

def download_image(img_url, output_dir, log):
    fname = os.path.basename(img_url.split("?")[0])
    fpath = os.path.join(output_dir, fname)
    if os.path.exists(fpath):
        log(f"Already downloaded: {fname}")
        return
    try:
        r = session.get(img_url, stream=True)
        r.raise_for_status()
        with open(fpath, "wb") as f:
            for chunk in r.iter_content(1024):
                f.write(chunk)
        log(f"Downloaded: {fname}")
    except Exception as e:
        log(f"Error downloading {img_url}: {e}")

def rip_galleries(selected_albums, output_root, log, mimic_human=True):
    for album_name, album_url in selected_albums:
        safe_name = "".join([c for c in album_name if c.isalnum() or c in " _-"]).strip() or "unnamed"
        outdir = os.path.join(output_root, safe_name)
        os.makedirs(outdir, exist_ok=True)
        log(f"Scraping album: {album_name}")
        image_links = get_image_links_from_js(album_url)
        log(f"  Found {len(image_links)} images in {album_name}.")

        if mimic_human:
            image_links = image_links.copy()
            random.shuffle(image_links)

        for idx, img_url in enumerate(image_links):
            download_image(img_url, outdir, log)
            if mimic_human:
                time.sleep(random.uniform(0.7, 2.5))
                if (idx + 1) % random.randint(18, 28) == 0:
                    log("...taking a longer break to mimic human behavior...")
                    time.sleep(random.uniform(5, 8))
        log(f"Done with album: {album_name}")

# ---------- GUI ----------
class GalleryRipperApp(ThemedTk):
    def __init__(self):
        super().__init__(theme="equilux")
        self.title("Coppermine Gallery Ripper")
        self.geometry("900x700")
        self.minsize(600, 450)
        self.resizable(True, True)
        self.albums = []
        self.selected_vars = []
        self.download_thread = None

        dark_bg = "#292929"
        dark_fg = "#EEEEEE"
        accent_fg = "#CCCCCC"

        style = ttk.Style(self)
        style.theme_use("equilux")
        style.configure("TFrame", background=dark_bg)
        style.configure("TLabel", background=dark_bg, foreground=dark_fg)
        style.configure("TLabelFrame", background=dark_bg, foreground=accent_fg)
        style.configure("TCheckbutton", background=dark_bg, foreground=dark_fg)
        style.configure("TButton", background="#323232", foreground=dark_fg)
        style.configure("Accent.TButton", background="#404060", foreground=dark_fg)
        style.configure("TLabelframe.Label", background=dark_bg, foreground=accent_fg)
        self['background'] = dark_bg

        # Layout structure: use a parent frame for spacing and padding
        content = ttk.Frame(self)
        content.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        # URL Entry
        frm_url = ttk.Frame(content)
        frm_url.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        frm_url.columnconfigure(1, weight=1)
        ttk.Label(frm_url, text="Gallery Root URL:").grid(row=0, column=0, sticky="w")
        self.url_entry = ttk.Entry(frm_url)
        self.url_entry.grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(frm_url, text="Discover Galleries", command=self.discover_albums).grid(row=0, column=2, sticky="e")

        # Download Path
        frm_path = ttk.Frame(content)
        frm_path.grid(row=1, column=0, sticky="ew", pady=(0, 5))
        frm_path.columnconfigure(1, weight=1)
        ttk.Label(frm_path, text="Download Folder:").grid(row=0, column=0, sticky="w")
        self.path_var = tk.StringVar()
        ttk.Entry(frm_path, textvariable=self.path_var).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(frm_path, text="Browse...", command=self.select_folder).grid(row=0, column=2, sticky="e")

        # Human-like option with tooltip
        self.mimic_var = tk.BooleanVar(value=True)
        mimic_chk = ttk.Checkbutton(frm_path, text="Mimic human behavior", variable=self.mimic_var)
        mimic_chk.grid(row=0, column=3, sticky="w", padx=(10, 0))

        def show_tip(event):
            x, y, cx, cy = mimic_chk.bbox("insert")
            x += mimic_chk.winfo_rootx() + 25
            y += mimic_chk.winfo_rooty() + 20
            self.tipwindow = tw = tk.Toplevel(mimic_chk)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            label = tk.Label(
                tw, text="Slows downloads, randomizes timing/order, and\nadds pauses to look like a real visitor.\nPrevents bans/rate limits.",
                justify='left', background="#232323", fg="#eee", relief='solid', borderwidth=1, font=("Consolas", 9)
            )
            label.pack(ipadx=1)

        def hide_tip(event):
            if hasattr(self, "tipwindow") and self.tipwindow:
                self.tipwindow.destroy()
                self.tipwindow = None

        mimic_chk.bind("<Enter>", show_tip)
        mimic_chk.bind("<Leave>", hide_tip)

        # Albums List (Scrollable & fully resizable)
        self.chkfrm = ttk.LabelFrame(content, text="Select Albums to Download")
        self.chkfrm.grid(row=2, column=0, sticky="nsew", pady=(0, 5))
        content.rowconfigure(2, weight=4)
        content.columnconfigure(0, weight=1)
        # Top "Select All" checkbox
        self.top_select_all_var = tk.BooleanVar(value=True)
        top_cb = ttk.Checkbutton(self.chkfrm, text="Select All", variable=self.top_select_all_var, command=self.top_select_all_toggle)
        top_cb.grid(row=0, column=0, sticky="w", padx=(2, 2), pady=(0, 2))
        self.chkfrm.rowconfigure(1, weight=1)
        self.chkfrm.columnconfigure(0, weight=1)

        # Canvas for scrolling
        self.canvas = tk.Canvas(self.chkfrm, borderwidth=0, highlightthickness=0, bg=dark_bg)
        self.scrollbar = ttk.Scrollbar(self.chkfrm, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.scrollbar.grid(row=1, column=1, sticky="ns")
        self.chkfrm.rowconfigure(1, weight=1)
        self.chkfrm.columnconfigure(0, weight=1)

        # Select/Unselect All
        frm_sel = ttk.Frame(content)
        frm_sel.grid(row=3, column=0, sticky="ew", pady=(0, 5))
        ttk.Button(frm_sel, text="Select All", command=lambda: self.set_all_checks(True)).pack(side="left")
        ttk.Button(frm_sel, text="Unselect All", command=lambda: self.set_all_checks(False)).pack(side="left")

        # Download Button (sticks to bottom but above log)
        ttk.Button(content, text="Start Download", command=self.start_download, style="Accent.TButton").grid(row=4, column=0, pady=5, sticky="ew")

        # Info Log (fully resizable with window)
        self.log_box = ScrolledText(content, height=7, state='disabled', font=("Consolas", 9),
                                    background="#181818", foreground="#EEEEEE", insertbackground="#EEEEEE")
        self.log_box.grid(row=5, column=0, sticky="nsew")
        content.rowconfigure(5, weight=2)

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

    def clear_album_checks(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.selected_vars = []
        self.top_select_all_var.set(True)

    def top_select_all_toggle(self):
        val = self.top_select_all_var.get()
        for var in self.selected_vars:
            var.set(val)

    def discover_albums(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please enter the gallery URL.")
            return
        self.log(f"Discovering albums from: {url}")
        self.clear_album_checks()
        try:
            global BASE_URL
            BASE_URL = url.split('/index.php')[0] + '/'
            albums = find_albums(url)
            if not albums:
                self.log("No albums found.")
                return
            self.albums = albums
            for i, (name, _) in enumerate(albums):
                if not name:
                    continue
                var = tk.BooleanVar(value=True)
                cb = ttk.Checkbutton(self.scrollable_frame, text=name, variable=var)
                cb.pack(fill="x", anchor="w", padx=2, pady=0)
                self.selected_vars.append(var)
            self.log(f"Found {len([a for a in albums if a[0]])} albums.")
            self.top_select_all_var.set(True)
        except Exception as e:
            self.log(f"Failed to discover albums: {e}")

    def set_all_checks(self, value):
        for var in self.selected_vars:
            var.set(value)

    def start_download(self):
        if self.download_thread and self.download_thread.is_alive():
            messagebox.showinfo("Download running", "Please wait for the current download to finish.")
            return
        output_dir = self.path_var.get().strip()
        if not output_dir:
            messagebox.showwarning("Missing folder", "Please select a download folder.")
            return
        selected = [(name, url) for (name, url), var in zip(self.albums, self.selected_vars) if var.get()]
        if not selected:
            messagebox.showwarning("No albums selected", "Select at least one album to download.")
            return
        self.log("Starting download...")
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
