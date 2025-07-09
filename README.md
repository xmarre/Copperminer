# Gallery Ripper

A hassle-free GUI tool to recursively download full-size images from any Coppermine-powered gallery, including multi-album sites.  
No thumbnails—just full images, organized by album.

## Features

- Point-and-click GUI, no command line needed
- Enter any gallery root or album URL (works with multi-level Coppermine sites)
- Finds all albums/galleries, lists them for selection
- Download progress and info log
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
