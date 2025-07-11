# Copperminer – A Gallery Ripper

A hassle-free GUI tool to recursively download full-size images from any Coppermine-powered gallery—plus other sites using a simple rules engine (ThePlace2 included). No thumbnails, no junk—just the real full-size images, organized in folders to match the site’s gallery structure. Perfect for backing up fan galleries before they disappear.

## Features

- Point-and-click GUI — No command line needed, always-on dark mode
- Intelligent discovery — Enter any gallery root or album URL (supports Coppermine and rule-based sites like ThePlace2)
- Album tree — Finds and displays all real albums for selection, ignoring “Last uploads”, “Most viewed”, and other virtual/special albums
- Optional special galleries toggle — Include “Last uploads”, “Most viewed”, etc. only when you want them
- Preserves structure — Downloads images into folders/subfolders that match the gallery’s layout
- Download progress & log — See what’s happening at every step
- Select/Unselect all and Stop buttons — Quickly manage or cancel downloads
- Resizable log panel — Drag to change how much space the log uses
- Adaptive scraping engine — Handles custom Coppermine themes, multi-page albums, custom anti-hotlinking, and referer requirements
- Smart caching engine — Saves each page and image list with ETag/Last-Modified info. Quick scans use HEAD requests so only changed pages are re-scraped.
- History dropdown — Quickly reopen recently scanned galleries from cache
- “Mimic human behavior” — Adds random pauses between downloads to avoid hammering servers (toggle in the GUI)
- Windows double-click support — via `start_gallery_ripper.bat`
- One-click self-update from Git — pull new commits and restart automatically
- Compatible with Python 3.10+
- Dynamic proxy pool with caching — Harvests and validates free proxies automatically, caches results to skip dead proxies, and fast-fills the pool for quick startup; UI shows last check time
- Proxy checks hit a random gallery URL from `proxy_manager.py` so only proxies that work on real targets are kept. Edit the `VALIDATION_URLS` list there to customize.
- Verbose checkbox — Toggle DEBUG-level logging on demand while the app runs

## Limitations

- Supports Coppermine and a small set of rule-based sites (initially ThePlace2): other galleries may fail
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

### Command-line options

The ripper now supports a few optional flags to control proxy usage and
download concurrency:

```bash
python gallery_ripper.py --min-proxies 50 --validation-concurrency 20 --download-workers 4
```

- `--min-proxies` – minimum number of working proxies to keep in the pool
- `--validation-concurrency` – how many proxies to validate in parallel
- `--download-workers` – number of concurrent image download tasks
- `--proxy` – use a specific HTTP proxy for all requests
- `--debug` – enable verbose DEBUG-level logging to help diagnose issues

## License

Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)  
See [LICENSE](LICENSE) for details.
