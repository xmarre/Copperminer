# Gallery Ripper

A hassle-free GUI tool to recursively download full-size images from any Coppermine-powered gallery, including multi-album sites.  
No thumbnails—just full images, organized by album.

## Features

- Point-and-click GUI, no command line needed
- Enter any gallery root or album URL (works with multi-level Coppermine sites)
- Finds all albums/galleries, lists them for selection
- Download progress and info log
- Adaptive scraping engine handles custom Coppermine themes and plugins
- Windows double-click support via `start_gallery_ripper.bat`
- Compatible with Python 3.10+
- Always-on Equilux dark theme
- Optional "Mimic human behavior" setting randomizes download order and timing

## Installation

1. **Clone or download this repository.**
2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Run the application:**

   On Windows, simply double-click `start_gallery_ripper.bat` to launch
   the tool (the batch script activates the virtual environment if present).
   On other platforms run:

   ```bash
   python gallery_ripper.py
   ```

## License

This project is licensed under the Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0) license.

See [LICENSE](LICENSE) for details.

## Why Your Ripper May Fail for Some Galleries

Great, this HTML snapshot of a real `displayimage.php` page from
maisie-williams.org makes everything clear. Here’s what’s going on, why the
ripper can fail, and how to fix and generalize the code for Coppermine galleries
using this style.

### Why It Fails

1. **Wrong base URL assumption** – the ripper joins relative image paths
   directly to the domain root which leads to 404 errors. The page uses paths
   like `albums/Photoshoots/001/001.jpg` that should resolve relative to the
   `/photos/` subdirectory.
2. **What actually happens** – browsers resolve those relative URLs against the
   directory containing `displayimage.php`, e.g.
   `https://maisie-williams.org/photos/`.

### Step-by-step Fix

Extract the correct base from the current page URL (strip off
`displayimage.php?...`). When joining any `<img>` or `<a>` relative URL, use that
base. The code snippet below illustrates the approach:

```python
from urllib.parse import urljoin, urlparse

def get_base_for_relative_images(page_url):
    # e.g. https://example.com/photos/displayimage.php?id=1 -> https://example.com/photos/
    return page_url.rsplit('/', 1)[0] + '/'

def extract_all_displayimage_candidates(displayimage_url, log=lambda msg: None):
    soup = get_soup(displayimage_url)
    candidates = []
    base = get_base_for_relative_images(displayimage_url)
    img = soup.find("img", class_="image")
    if img and img.get("src"):
        candidates.append(urljoin(base, img["src"]))
    # ... handle other links the same way
```

Always resolve image sources against the base directory of the gallery, not the
domain root. This works for any Coppermine gallery installed in a subdirectory
such as `/photos/` or `/gallery/`.

### Extra: How to Detect and Fix for Other Galleries

Look for image `src` or `href` values that start with `albums/` (without a
leading slash). When you see this pattern, resolve the path relative to the
directory containing `displayimage.php` or `thumbnails.php`.

### Summary: Why Your Code Failed, What To Do

The ripper assumed images were rooted at the domain, so relative paths like
`albums/foo/001.jpg` resolved incorrectly and produced 404 errors. Build the
base URL from the current page and join all relative paths against that base.
This approach works for any Coppermine installation located inside a
subdirectory.

### Next Step: Quick Patch

Replace any line such as:

```python
urljoin(displayimage_url, img["src"])
```

with:

```python
urljoin(get_base_for_relative_images(displayimage_url), img["src"])
```

and apply the same change when handling `<a href>` attributes.

