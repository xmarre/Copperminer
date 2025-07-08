import os
import threading
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from ttkthemes import ThemedTk

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
        if 'thumbnails.php?album=' in href and href not in visited:
            full_url = urljoin(url, href)
            albums.append((a.text.strip(), full_url))
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

def get_displayimage_links(album_url):
    soup = get_soup(album_url)
    links = []
    for a in soup.find_all("a", href=True):
        href = a['href']
        if href.startswith('displayimage.php') and 'album=' in href:
            full_link = urljoin(album_url, href)
            if full_link not in links:
                links.append(full_link)
    return links

def get_full_image_url(displayimage_url):
    soup = get_soup(displayimage_url)
    img = soup.find('img', class_="image")
    if img:
        src = img.get('src')
        return urljoin(displayimage_url, src)
    # fallback: largest img
    imgs = soup.find_all('img')
    if imgs:
        imgs = sorted(imgs, key=lambda i: int(i.get('width', 0)) * int(i.get('height', 0)), reverse=True)
        return urljoin(displayimage_url, imgs[0]['src'])
    return None

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

def rip_galleries(selected_albums, output_root, log):
    for album_name, album_url in selected_albums:
        safe_name = "".join([c for c in album_name if c.isalnum() or c in " _-"]).strip() or "unnamed"
        outdir = os.path.join(output_root, safe_name)
        os.makedirs(outdir, exist_ok=True)
        log(f"Scraping album: {album_name}")
        display_links = get_displayimage_links(album_url)
        log(f"  Found {len(display_links)} images in {album_name}.")
        for link in display_links:
            img_url = get_full_image_url(link)
            if img_url:
                download_image(img_url, outdir, log)
            else:
                log(f"  [!] No image found at: {link}")
        log(f"Done with album: {album_name}")

# ---------- GUI ----------
class GalleryRipperApp(ThemedTk):
    def __init__(self, theme="arc"):
        super().__init__(theme=theme)
        # Theme selector
        dark_themes = [th for th in self.get_themes() if "dark" in th or th in ("arc", "black", "equilux", "plastik", "radiance")]
        if not dark_themes:
            dark_themes = self.get_themes()
        self.theme_var = tk.StringVar(value=self.current_theme)
        frm_theme = ttk.Frame(self)
        frm_theme.pack(fill="x", pady=5, padx=10)
        ttk.Label(frm_theme, text="Theme:").pack(side="left")
        theme_menu = ttk.Combobox(frm_theme, textvariable=self.theme_var, values=dark_themes, state="readonly", width=20)
        theme_menu.pack(side="left", padx=5)
        theme_menu.bind("<<ComboboxSelected>>", lambda e: self.set_theme(self.theme_var.get()))
        self.title("Coppermine Gallery Ripper")
        self.geometry("700x570")
        self.resizable(False, False)
        self.albums = []
        self.selected_vars = []
        self.download_thread = None

        # URL Entry
        frm_url = ttk.Frame(self)
        frm_url.pack(fill="x", pady=5, padx=10)
        ttk.Label(frm_url, text="Gallery Root URL:").pack(side="left")
        self.url_entry = ttk.Entry(frm_url, width=60)
        self.url_entry.pack(side="left", padx=5)
        ttk.Button(frm_url, text="Discover Galleries", command=self.discover_albums).pack(side="left")

        # Download Path
        frm_path = ttk.Frame(self)
        frm_path.pack(fill="x", pady=5, padx=10)
        ttk.Label(frm_path, text="Download Folder:").pack(side="left")
        self.path_var = tk.StringVar()
        ttk.Entry(frm_path, textvariable=self.path_var, width=50).pack(side="left", padx=5)
        ttk.Button(frm_path, text="Browse...", command=self.select_folder).pack(side="left")

        # Albums List with dark background
        self.chkfrm = ttk.LabelFrame(self, text="Select Albums to Download")
        self.chkfrm.pack(fill="both", expand=True, padx=10, pady=(5,0))
        # Listbox for albums
        style = ttk.Style()
        style.configure("Dark.TFrame", background="#222", foreground="#eee")
        self.chkfrm.configure(style="Dark.TFrame")

        # Select/Unselect All
        frm_sel = ttk.Frame(self)
        frm_sel.pack(fill="x", padx=10, pady=2)
        ttk.Button(frm_sel, text="Select All", command=lambda: self.set_all_checks(True)).pack(side="left")
        ttk.Button(frm_sel, text="Unselect All", command=lambda: self.set_all_checks(False)).pack(side="left")

        # Download Button
        ttk.Button(self, text="Start Download", command=self.start_download, style="Accent.TButton").pack(pady=8)

        # Info Log (dark bg/fg)
        self.log_box = ScrolledText(self, height=9, state='disabled', font=("Consolas", 9), background="#232323", foreground="#EEEEEE", insertbackground="#EEEEEE")
        self.log_box.pack(fill="both", padx=10, pady=(2,10))

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
        for widget in self.chkfrm.winfo_children():
            widget.destroy()
        self.selected_vars = []

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
                var = tk.BooleanVar(value=True)
                cb = ttk.Checkbutton(self.chkfrm, text=name, variable=var)
                cb.pack(fill="x", anchor="w")
                self.selected_vars.append(var)
            self.log(f"Found {len(albums)} albums.")
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
            rip_galleries(selected, output_dir, self.log)
            self.log("All downloads finished!")
        except Exception as e:
            self.log(f"Download error: {e}")

if __name__ == "__main__":
    GalleryRipperApp().mainloop()
