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
- Yandex Images support — Paste a keyword/search phrase, an image URL, or a Yandex Images search URL; preview thumbnail results, sort by resolution/size/domain/title, follow similar-image searches, download the original/highest-resolution candidate URLs Yandex exposes, and import saved Yandex HTML when direct requests hit a CAPTCHA
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

Paste a keyword/search phrase such as `forest`, a direct image URL, or an existing `yandex.com/images/search?...` URL into **Gallery Root URL** and click **Yandex Search**. You can also type `yandex:forest` and click **Discover Galleries** directly. Copperminer detects whether the input is text search or reverse-image search, then opens a sortable Yandex result overview with thumbnails, known resolution, known file size, source domain, and result actions.

Use **Similar** on any card to run a new Yandex reverse-image search from that result, matching Yandex's similar-picture workflow. Use **Select for download**, **Select visible**, or the normal album tree selection to choose results, then start the download. Downloads try Yandex's original/direct image candidates first and fall back through lower-priority preview URLs only if the original candidate fails.

The **Probe sizes** button sends HEAD requests for visible cards that do not expose file-size metadata in Yandex's page data, then updates file-size sorting for those cards.

If Yandex redirects automated requests to a CAPTCHA page, Copperminer now treats that as a blocked search instead of a fake zero-result discovery. It retries alternate Yandex hosts by default. If you explicitly enable **Reuse Yandex browser cookies**, and `browser-cookie3` is installed, Copperminer can also make a best-effort retry with local Yandex cookies from Chrome, Edge, Firefox, Brave, Chromium, or Opera. If Yandex still blocks the request, open the logged request URL in your browser, solve the challenge, save the result page as HTML, then use **Yandex HTML** to import that saved page. The imported page uses the same parser, preview grid, sorting, and download pipeline.

## Limitations

- Supports Coppermine, 4chan, Yandex Images text/reverse search, and a small set of rule-based sites (initially ThePlace2 and LiveJournal photos): other galleries may fail
- Yandex support depends on Yandex's public HTML and can be rate-limited or CAPTCHA-gated by Yandex. Copperminer detects CAPTCHA/block pages, retries with alternate hosts, supports explicit opt-in local browser cookie reuse, and supports saved-HTML import as a manual fallback. Some result file sizes are only available after probing.
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
