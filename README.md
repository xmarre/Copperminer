# Copperminer – A Gallery Ripper

A hassle-free GUI tool to recursively download full-size images from any Coppermine-powered gallery—plus other sites using a simple rules engine (ThePlace2 and LiveJournal photos included). No thumbnails, no junk—just the real full-size images, organized in folders to match the site’s gallery structure. Perfect for backing up fan galleries before they disappear.

## Features

- Point-and-click GUI — No command line needed, always-on dark mode
- Intelligent discovery — Enter any gallery root or album URL (supports Coppermine and rule-based sites like ThePlace2 or LiveJournal photos)
- Album tree — Finds and displays all real albums for selection, ignoring “Last uploads”, “Most viewed”, and other virtual/special albums
- Optional special galleries toggle — Include “Last uploads”, “Most viewed”, etc. only when you want them
- Preserves structure — Downloads images into folders/subfolders that match the gallery’s layout
- Download progress & log — See what’s happening at every step
- Select/Unselect all and Stop buttons — Quickly manage or cancel downloads
- Resizable log panel — Drag to change how much space the log uses
- 4chan support — Browse boards and threads to bulk download all attached media
- Yandex Images reverse-search support — Paste an image URL or Yandex Images search URL, preview thumbnail results, sort by resolution/size/domain/title, follow similar-image searches, and download the original/highest-resolution candidate URLs Yandex exposes
- Adaptive scraping engine — Handles custom Coppermine themes, multi-page albums, custom anti-hotlinking, and referer requirements
- Smart caching engine — Saves each page and image list with ETag/Last-Modified info. Quick scans use HEAD requests so only changed pages are re-scraped.
- History dropdown — Quickly reopen recently scanned galleries from cache
- “Mimic human behavior” — Adds random pauses between downloads to avoid hammering servers (toggle in the GUI)
- Windows double-click support — via `start_gallery_ripper.bat`
- One-click self-update from Git — pull new commits and restart automatically
- Compatible with Python 3.10+

### 4chan Usage

Enter `4chan` by itself to browse all boards, or paste any 4chan board or thread URL. Double-click a board to open it. Use the **Back** button to walk back up the navigation stack. Threads show a checkmark when fully downloaded or `[+n]` for new files. After a download finishes, the tree refreshes to reflect the new status. Selecting a thread downloads all attached files (images, webms, mp4s) into a `4chan/<board>/<subject> (id)` folder structure.

### Yandex Images Usage

Paste a direct image URL into **Gallery Root URL** and click **Yandex Search**, or paste an existing `yandex.com/images/search?...` reverse-image result URL and click **Discover Galleries**. Copperminer opens a sortable Yandex result overview with thumbnails, known resolution, known file size, source domain, and result actions.

Use **Similar** on any card to run a new Yandex reverse-image search from that result, matching Yandex's similar-picture workflow. Use **Select for download**, **Select visible**, or the normal album tree selection to choose results, then start the download. Downloads try Yandex's original/direct image candidates first and fall back through lower-priority preview URLs only if the original candidate fails.

The **Probe sizes** button sends HEAD requests for visible cards that do not expose file-size metadata in Yandex's page data, then updates file-size sorting for those cards.

## Limitations

- Supports Coppermine, 4chan, Yandex Images reverse search, and a small set of rule-based sites (initially ThePlace2 and LiveJournal photos): other galleries may fail
- Yandex support depends on Yandex's public HTML. If Yandex changes its result markup, extraction may need parser updates. Some result file sizes are only available after probing.
- No thumbnails or junk: heuristically skips thumbnails and UI icons to save only the original images
- Not for commercial use: See license below

## Installation

1. Install Python 3.10 or newer. [Download here.](https://www.python.org/downloads/)
2. Clone or download this repository.
3. On Windows, run `setup_gallery_ripper.bat` to set up everything. Launch with `start_gallery_ripper.bat`.
   To update later, either run `update_gallery_ripper.bat` or click **Update from Git** inside the app.
4. On other platforms, follow these manual steps:

   Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   # Activate with:
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   ```

   Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

5. Run the application:

   On Windows, double-click `start_gallery_ripper.bat`

   On any platform:

   ```bash
   python gallery_ripper.py
   ```

## License

Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)  
See [LICENSE](LICENSE) for details.
