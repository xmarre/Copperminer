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

Follow the steps below if you are new to Python or virtual environments.

1. **Install Python 3.10 or newer.**
   Download it from [python.org](https://www.python.org/downloads/) and
   follow the instructions for your operating system.

2. **Clone or download this repository.**

   Windows users can run `setup_gallery_ripper.bat` in the repository root to
   automatically create a virtual environment and install all dependencies.
   The rest of this section explains the manual steps for other platforms.

3. **Create and activate a virtual environment (recommended):**

   ```bash
   python3 -m venv .venv
   ```

   Activate the environment so `pip` and `python` use the local copy:

   - **Windows**

     ```bash
     .venv\Scripts\activate
     ```

   - **macOS/Linux**

     ```bash
     source .venv/bin/activate
     ```

4. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

5. **Run the application:**

   After running `setup_gallery_ripper.bat` (or completing the steps above
   manually), Windows users can simply double-click
   `start_gallery_ripper.bat` to launch the tool. The batch script
   automatically activates `.venv` if it exists. On any platform you can run
   the script directly:

   ```bash
   python gallery_ripper.py
   ```

## License

This project is licensed under the Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0) license.

See [LICENSE](LICENSE) for details.
