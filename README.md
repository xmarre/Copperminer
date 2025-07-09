# Copperminer – A Gallery Ripper

A hassle-free GUI tool to recursively download full-size images from any Coppermine-powered gallery—including multi-album and deeply nested sites. No thumbnails, no junk—just the real full-size images, organized in folders to match the site’s gallery structure. Perfect for backing up fan galleries before they disappear.

## Features

- Point-and-click GUI — No command line needed, always-on dark mode
- Intelligent discovery — Enter any gallery root or album URL (works with multi-level Coppermine sites)
- Album tree — Finds and displays all real albums for selection, ignoring “Last uploads”, “Most viewed”, and other virtual/special albums
- Preserves structure — Downloads images into folders/subfolders that match the gallery’s layout
- Download progress & log — See what’s happening at every step
- Adaptive scraping engine — Handles custom Coppermine themes, multi-page albums, custom anti-hotlinking, and referer requirements
- Quick scan caching — Saves HTML of each visited page. Subsequent runs hit the network only if a page has changed or the cache expired.
- “Mimic human behavior” — Optionally randomizes download order and timing to avoid hammering servers (toggle in the GUI)
- Windows double-click support — via `start_gallery_ripper.bat`
- One-click self-update from Git — pull new commits and restart automatically
- Compatible with Python 3.10+

## Limitations

- Coppermine only: Not intended for non-Coppermine galleries (results will likely be incomplete or fail)
- No thumbnails or junk: Only saves original/full-size images—not previews, icons, or other irrelevant files
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
