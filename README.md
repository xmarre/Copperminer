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
- 4chan support — Browse live boards/threads, recover archived.moe original attachments into the corresponding 4chan folders, and import a browser-saved API JSON response if archive discovery is denied
- IMDb support — Download complete title/person photo galleries, individual media viewer photos, and combined title/person/image-type selections at original resolution
- Yandex Images support — Paste a keyword/search phrase, an image URL, or a Yandex Images search URL; preview thumbnail results, sort by resolution/size/domain/title, follow similar-image searches, download the original/highest-resolution candidate URLs Yandex exposes, and import saved Yandex HTML when direct requests hit a CAPTCHA
- Adaptive scraping engine — Handles custom Coppermine themes, multi-page albums, custom anti-hotlinking, and referer requirements
- Smart caching engine — Saves each page and image list with ETag/Last-Modified info. Quick scans use HEAD requests so only changed pages are re-scraped.
- History dropdown — Quickly reopen recently scanned galleries from cache
- Polite request pacing — Optional per-host pacing for discovery and downloads; atomic scheduling across workers, with adaptive cooldown after throttling
- Windows double-click support — via `start_gallery_ripper.bat`
- One-click self-update from Git — pull new commits and restart automatically
- Compatible with Python 3.10+

### 4chan and archived.moe Usage

Enter `4chan` by itself to browse live boards, or paste a live 4chan board/thread URL. Double-click a board to open it; use **Back** to navigate. Threads show a checkmark when fully downloaded or `[+n]` for new files. Selecting a thread saves attached media (images, webms, mp4s) under `4chan/<board>/<sanitized subject> <thread ID>` (Copperminer removes parentheses when sanitizing folder names).

To recover a thread that has disappeared from 4chan, paste an archived.moe **thread** link into **Gallery Root URL**, for example `https://archived.moe/b/thread/956262976/#956262976`. Click **Discover Galleries**, select the archived thread, then **Start Download**. Copperminer reads the archived FoolFuuka thread metadata and attempts every available original-media URL. If the matching `4chan/<board>/... (<thread id>)` folder already exists, new files go into that folder even if the archived subject differs. Existing files with matching original filenames are left untouched; if the thread has never been downloaded, Copperminer creates the normal 4chan folder.

**If archived.moe blocks API access with HTTP 403:** The thread URL is recognized, but automated metadata discovery is denied by the archive's server. Copperminer shows the blocked API URL and offers to open it in your normal browser. With the archived thread URL still in **Gallery Root URL**:

1. Click **Open thread API in browser**, and complete any verification the website requires. Confirm that the browser displays a JSON object containing the thread ID, not an HTML challenge/error page. If the browser itself cannot obtain JSON, this fallback cannot work.
2. Save the displayed API response as a `.json` file (Ctrl+S in browsers that support saving JSON; choose the original JSON text rather than rendered HTML).
3. Click **Import saved thread JSON** and select that file. Copperminer checks that it contains the specified thread, discovers its attachments without another metadata API call, and retains that snapshot for **Start Download**. This is an explicit one-time import for the current app session, not a persistent credential or cookie.

Imported data supplies **original-media URLs and integrity metadata**, not the media bytes. Media downloads may still fail if the archive or linked hosting service denies access. Copperminer logs these failures and continues through the remaining original candidates. It never treats a saved challenge page as valid JSON or silently substitutes thumbnails.

Archived.moe does **not** preserve every full-size original. Its metadata can contain thumbnails without a recoverable original, and some linked source URLs may return HTTP errors or anti-bot challenges. Copperminer does not use thumbnails as substitutes for originals, verifies file size and MD5 when archived metadata supplies them, logs unavailable files, and does not bypass access restrictions. The reported count reflects attachments with at least one candidate original URL, not guaranteed successful downloads. Recovery is available for individual archived.moe thread URLs; archived board browsing/search is not supported.

### Yandex Images Usage

Paste a keyword/search phrase such as `forest`, a direct image URL, or an existing `yandex.com/images/search?...` URL into **Gallery Root URL** and click **Yandex Search**. You can also type `yandex:forest` and click **Discover Galleries** directly. Copperminer detects whether the input is text search or reverse-image search, then opens a sortable Yandex result overview with thumbnails, known resolution, known file size, source domain, and result actions.

Use **Similar** on any card to run a new Yandex reverse-image search from that result, matching Yandex's similar-picture workflow. Use **Select for download**, **Select visible**, or the normal album tree selection to choose results, then start the download. Downloads try Yandex's original/direct image candidates first and fall back through lower-priority preview URLs only if the original candidate fails.

The **Probe sizes** button sends HEAD requests for visible cards that do not expose file-size metadata in Yandex's page data, then updates file-size sorting for those cards.

If Yandex redirects automated requests to a CAPTCHA page, Copperminer now treats that as a blocked search instead of a fake zero-result discovery. It retries alternate Yandex hosts by default. If you explicitly enable **Reuse Yandex browser cookies**, and `browser-cookie3` is installed, Copperminer can also make a best-effort retry with local Yandex cookies from Chrome, Edge, Firefox, Brave, Chromium, or Opera. If Yandex still blocks the request, open the logged request URL in your browser, solve the challenge, save the result page as HTML, then use **Yandex HTML** to import that saved page. The imported page uses the same parser, preview grid, sorting, and download pipeline.

### Coppermine discovery interrupted by access restrictions

Some galleries deny requests after a crawl has begun. If a Coppermine category
returns HTTP 403 or 429, Copperminer stops requesting further pages from that
host during the same discovery. Previously found categories and albums are
retained; categories visible in their parent listing but not yet accessible are
kept as **[incomplete]** placeholders, rather than silently disappearing.
An incomplete discovery is explicitly reported in the log and is not treated
as a fully refreshed cache. Subsequent discoveries retry incomplete branches.
A successful future crawl replaces stale placeholders with real albums.

This does not bypass a site's access policy or reveal albums that could not be
retrieved. If access has already been denied, do not repeatedly rerun discovery.
Wait for the restriction to expire or contact the site's administrator.

### Minimum image resolution

Enable **Skip undersized images** and configure **Minimum width** and
**Minimum height** (pixels) before starting a download. The default thresholds
are 512 × 512 pixels and the filter is disabled until selected. An image must
meet **both** thresholds. For example, 419 × 594 fails the default minimum
because its width is below 512. Copperminer checks the *actual decoded image
dimensions after download*, before saving the file, and tries alternative
candidates when available. A candidate rejected for size is removed without
leaving a partial file. Resolution filtering applies to new image downloads
across the supported adapters, including 4chan archives; videos are not
filtered. Existing downloaded files are never removed or replaced.

Pixel dimensions do not measure sharpness, compression artifacts, lighting or
aesthetic quality. A separate manual review is still needed for curated datasets.

### Request pacing

**Coppermine safety defaults:** Coppermine galleries always enforce a shared
minimum of **3 seconds** between requests to the same host, including category
pages, album pages, and image downloads. A small amount of jitter helps avoid
bursts from different download workers. Coppermine downloads use **one worker**
regardless of the general Workers slider, so image transfers are not initiated
in parallel by a single download job. This reduces load; it does not guarantee
that a site's anti-bot policy will allow automated downloads.

During discovery, Copperminer reads album image counts from category listings
or verified cached values, rather than requesting every album merely to count
its images. Missing counts appear as `?` until the album is visited for
downloading. Cached pages are revalidated with a single conditional GET, never
a HEAD request followed by a GET. The **Stop** button cancels pending
Coppermine requests and pacing waits without visiting additional categories.
A cached page that receives HTTP 403/429
does not mask the access failure. Downloading also stops scanning additional
Coppermine albums or attempting alternative image URLs after HTTP 403/429,
preventing an access-denied response from producing a retry storm.
Logs include limited response diagnostics
such as Retry-After or CF-Mitigated, when provided, without including cookies
or challenge-response content.

**Polite request pacing** remains optional for other gallery adapters and is
off by default. When enabled, Copperminer
spaces outgoing gallery-discovery requests and downloads on a **per-host** basis,
including IMDb GraphQL pagination. Requests to independent hosts are scheduled
independently. Downloads retain baseline per-host spacing even when the option is off:
0.35 seconds between image request starts and 4 seconds after video/GIF starts.
With the option enabled, discovery and image requests use at least 0.7 seconds
plus up to 0.3 seconds of jitter; video/GIF requests retain their higher minimum.
Concurrent workers cannot reserve the same request-start slot. Redirected requests are paced at their destination hosts.

HTTP 429 and transient server errors trigger an adaptive cooldown. Valid
`Retry-After` values (seconds or HTTP dates) are respected. The scheduler does
not change browser fingerprints, bypass authentication/CAPTCHAs, or guarantee
that a site's scraping restrictions will allow automated access. Lower worker
counts reduce concurrent transfers; pacing controls **request starts**, not how
many transfers remain open. The toggle is captured when each discovery or download job starts, so concurrent jobs cannot alter one another's pacing mode.

### IMDb Usage

Paste an IMDb movie, series, episode, or person URL into **Gallery Root URL**, then click **Discover Galleries**. Select **All photos** and start the download. `/mediaindex/` gallery links work too. Discovery follows every cursor page and checks the number of unique image records against IMDb's total before offering the gallery for download. **Stop** cancels IMDb discovery between requests and during retry waits.

The tree also shows **By title** for a person gallery, with titles sorted by release year and labeled with year, title type, and IMDb ID. A movie, series, or episode gallery shows **By person**, sorted by name. Select individual albums to download those photos into separate folders, or select the grouping folder to download its albums. These are subsets of the gallery's actual photo tags; a person's tree lists titles represented in their photos, rather than their entire filmography. Person tags may include crew and other credited people as well as actors.

**All photos** remains visible at the root and includes every original exposed by the selected gallery, regardless of its tags. Group downloads use the same complete catalog, preserving any existing filters. **No title tags** or **No person tags** collects images without usable grouping tags. Missing or incorrect IMDb tags can affect categorization; Copperminer does not infer identities from image content. One image can belong to multiple albums, so group counts can overlap. Choose **All photos** alone for one complete pack; choosing it together with the grouped albums also creates separate copies in the group folders.

IMDb can expose different image sets on person and title pages. An image tagged with an actor in a movie gallery may be absent from that actor's public person gallery; IMDb documents [restrictions on displaying additional name tags](https://help.imdb.com/article/contribution/images-videos/images-guidelines/G27LPBQZA7EAZGJW). For a particular title's actor photos, use the title gallery's **By person** branch. **All photos** covers the selected gallery, and cannot guarantee every photo of a person across IMDb.

A `/mediaviewer/rm…/` link offers both **Selected photo** and the complete gallery. Select the desired album; the selected photo has its own subfolder. Photos are stored under `IMDb/<title or person> (<IMDb ID>)/` using the original CDN filenames. Existing files are skipped on repeat downloads.

Use **IMDb Filters…** to configure people, titles, gallery IDs, and image types in the GUI, then click **Apply and Discover**. Gallery URLs can also combine filters. Values within `names`, `titles`, or `types` match **any** listed value; different filter groups are combined. `allNames` and `allTitles` require **every** listed person or title. Comma-separated and repeated parameters are accepted. Legacy `refine` links are supported for person IDs, title IDs, and image types. `contentTypes` is an alias for `types`. `galleries` and `allGalleries` accept IMDb gallery IDs. Unsupported parameters produce an explicit error so the selection is never silently widened. Tracking parameters (`ref_`, `ref`) and numbered `page` parameters are ignored: downloads always traverse the full selected gallery.

| Selection | Example URL |
| --- | --- |
| Movie gallery | `https://www.imdb.com/title/tt0107290/` |
| Person gallery | `https://www.imdb.com/name/nm0000554/` |
| Series gallery | `https://www.imdb.com/title/tt0944947/mediaindex/` |
| One photo | `https://www.imdb.com/title/tt0107290/mediaviewer/rm1102560512/` |
| Sam Neill in Jurassic Park, still frames | `https://www.imdb.com/title/tt0107290/mediaindex/?names=nm0000554&types=still_frame` |
| Photos tagged with two people | `https://www.imdb.com/title/tt0107290/mediaindex/?allNames=nm0000554,nm0000156` |

Filtered selections use a separate stable subfolder. IMDb's gallery API supplies original image URLs and dimensions. Copperminer removes CDN resize/crop/quality modifiers while preserving the asset ID and file format, and downloads the resulting bytes without re-encoding or upscaling. A failed original download is reported as a failure; it does not fall back to a thumbnail. Original resolution is limited by the source image uploaded to IMDb.

**Quick scan** reuses a verified complete catalog for up to five minutes; turn it off for a fresh discovery. Blocked, restricted, malformed, changing, or incomplete gallery responses fail discovery instead of being presented as a complete gallery. This integration uses IMDb's web gallery API and requires no API key or extra dependency; access and schema availability can change.

## Limitations

- Supports Coppermine, IMDb title/person photo galleries, 4chan, Yandex Images text/reverse search, and a small set of rule-based sites (initially ThePlace2 and LiveJournal photos): other galleries may fail
- IMDb support downloads the images exposed by the selected public gallery. A series gallery does not recursively enumerate separate episode galleries, and a person's gallery does not recursively enumerate every film in their filmography. Use the corresponding title/episode URLs to download those galleries. IMDb may block or restrict API access; Copperminer reports that condition explicitly.
- Yandex support depends on Yandex's public HTML and can be rate-limited or CAPTCHA-gated by Yandex. Copperminer detects CAPTCHA/block pages, retries with alternate hosts, supports explicit opt-in local browser cookie reuse, and supports saved-HTML import as a manual fallback. Some result file sizes are only available after probing.
- No thumbnails or junk: heuristically skips thumbnails and UI icons to save only the original images
- Not for commercial use: See license below

## Installation

Download the versioned `Copperminer-v<version>.zip` from [Releases](https://github.com/xmarre/Copperminer/releases), or use a repository checkout. Extract the whole ZIP before launching; `gallery_ripper.py`, `imdb_gallery.py`, and `request_pacing.py` must remain in the same folder.

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
