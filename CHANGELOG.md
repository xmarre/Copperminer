# Changelog

## 0.4.4 — 2026-11-12

### Fixed

- Distinguish an expired 4chan thread (HTTP 404/410) from a successful thread with no attachments. Expired live-thread URLs now initiate archive recovery instead of presenting a misleading completed empty album.
- Fetch each live thread's API JSON once for both its subject and media metadata. Live access errors other than 404/410 propagate instead of being suppressed.
- Automatically try the archived.moe FoolFuuka thread API, followed by TheBArchive's /b/ API when needed. Missing or blocked archive metadata surfaces as an explicit failure rather than a fabricated zero-image result.
- Preserve a validated archive metadata snapshot from discovery through the download step; do not request the same archive API again when the user starts downloading an expired thread.
- Accept live 4chan thread URLs in the archive JSON browser import and archive API helper buttons. Accept direct /b/ links from TheBArchive while retaining the existing 4chan folder-matching and original-media verification behavior.
- Clarify when a server-side block prevents archive recovery and expose the alternative /b/ API for browser-assisted imports.
- Resolve relative media links against the successful archive provider, including imported TheBArchive browser JSON, and preserve the resolved URLs in the in-memory snapshot.
- Enforce 4chan's documented minimum one-second spacing between requests to the official metadata API and prevent those requests from inheriting a LiveJournal Referer header. Add regression tests for provider-relative original URLs and missing redirect metadata.

### Validation and limitations

- Regression coverage checks one-fetch live discovery, expired-thread fallback, alternate archive response handling, snapshot reuse across discovery/download, URL routing and archive imports.
- A live HTTP 404/410 does not establish that the thread is archived. Third-party metadata and original-media availability are independent. HTTP 403 access restrictions and Cloudflare challenges cannot be bypassed by this change. Real-site archive access and Windows GUI behavior require independent testing.

## 0.4.3 — 2026-11-12

### Fixed

- Coppermine galleries now receive proactive per-host pacing of at least 3 seconds between page and media requests, regardless of the generic optional pacing control. Coppermine downloads run with one worker to avoid parallel fetch bursts.
- Category discovery no longer opens each album merely to calculate its image count. Read counts from category listings or prior cache; show unknown counts until the album is opened.
- Cached HTML revalidation uses one conditional GET instead of HEAD followed by GET. Unchanged pages retain their extracted image-list and album metadata, avoiding unnecessary follow-on fetches.
- HTTP 403/429 during cached-page revalidation propagates to the existing incomplete-discovery handling rather than being silently treated as a successful cached page.
- Coppermine discovery honors the Stop flag before each request and during host pacing/retry waits. Cancellation is not mistaken for an incomplete or successfully cached scan.
- Blocked album pagination and image downloads stop further Coppermine requests instead of continuing through additional albums or retrying image variants.

### Diagnostics and validation

- Access-denied discovery logs report a limited allowlist of server-provided response headers (when present), without logging cookies or response bodies.
- Mocked gallery and request-scheduler regression tests verify reduced request counts, retained caches, pacing before any error, host isolation, and access-denial behavior.

### Limitations

These safeguards reduce request load and the risk of automated access restrictions; they cannot guarantee access to hosts that independently reject scraper traffic. Existing server-side blocks are not bypassed or removed. Live compatibility with site-specific defenses and Windows GUI rendering require independent verification.

## 0.4.2 — 2026-11-12

### Fixed

- Coppermine discovery keeps previously crawled categories and albums when category requests fail. Unavailable category links remain as visible incomplete placeholders instead of disappearing from the tree.
- Discovery stops attempting additional pages on the blocked host after HTTP 403/429, marks partially refreshed trees as incomplete, and revisits those branches on subsequent discovery. Prior successful page-cache entries survive interrupted forced refreshes.
- A new tree-cache schema invalidates legacy trees that could incorrectly represent incomplete scans as complete.

### Added

- Opt-in image-resolution filter with configurable minimum width and height, remembered in settings. Inspect actual image dimensions before publishing downloaded files and try remaining candidates when one is too small.
- Apply resolution filtering to Coppermine, rule-based galleries, IMDb, Yandex and 4chan/archive image downloads; preserve videos and already downloaded files.
- Regression tests for access-denied recovery, cached subtree retention, incomplete-cache traversal and undersized candidate handling.

### Limitations

A blocked site does not expose missing album data; incomplete placeholders are resolved only after access returns. The filter measures pixel dimensions, not blur or compression quality. Tests use mocked server responses and sample images; live site behavior and Windows GUI usability still require verification.

## 0.4.1 — 2026-11-11

### Fixed

- Archived.moe may reject Python API requests with HTTP 403 even when the thread is viewable in a browser. Show a dedicated, actionable message and open the exact thread API URL in the user's browser on request.
- Add **Import saved thread JSON** to read a browser-saved FoolFuuka API response. Validate its thread ID, board (when present), JSON structure and input size; reject HTML challenge/error pages and mismatched threads.
- Pass the validated in-memory archive snapshot through both discovery **and** download, preventing the second API fetch that would otherwise fail with another 403.
- Keep existing folder matching, original-only candidates, size/hash verification, request pacing and file-preserving downloads unchanged.

### Limitations

Browser access to the API and successful original-media downloads still depend on archived.moe and linked services. This fallback is manual if normal API discovery is blocked; it does not bypass Cloudflare or grant access to unavailable originals. Automated tests cover the import flow; live browser export and Windows GUI behavior still require independent verification.

## 0.4.0 — 2026-11-11

### Added

- Paste an `archived.moe/<board>/thread/<id>` link to discover and recover original-media attachments from an expired 4chan thread.
- Archived threads reuse the matching live 4chan download directory by board and thread ID, even when the archive's subject differs. Existing filenames are skipped; missing files are added without replacing them.
- Retry archived original-media candidates, including linked external full-image sources and still-available 4chan CDN URLs. Validate original byte count and MD5 when available; treat unavailable originals and thumbnail/HTML responses as failures, not completed downloads.
- Focused regression coverage for archive metadata, source routing, folder matching, duplicate filenames, fallback downloads, and unavailable originals.

### Limitations

- Archived.moe archives thumbnails for many attachments; their originals may no longer exist. Candidate counts do not guarantee recoverable bytes.
- Archived.moe or linked hosts can impose Cloudflare challenges, access restrictions, or disappear. Browsing archived board indexes and live recovery automation are not included. Live compatibility and Windows GUI behavior still require real-world validation.

## 0.3.0 — 2026-10-13

### Added

- **Polite request pacing**: optional atomic per-host request-start spacing during gallery discovery and media downloads, including IMDb GraphQL and 4chan media.
- Shared adaptive cooldown on HTTP 429 and transient 5xx responses, with support for numeric and HTTP-date `Retry-After`.
- Redirected requests are paced at the destination host, not solely the initiating host.
- Cancellation-aware waits, per-job download-worker configuration, and independent pacing options for simultaneous discovery and download jobs.

### Fixed

- Parallel workers can no longer reserve the same host request-start slot.
- 4chan downloads now write temporary files and atomically finalize completed media.
- Worker failures acknowledge queue entries to prevent stalled batches.

### Limitations

Pacing spaces request starts, not simultaneous in-flight transfers, and does not bypass access restrictions or CAPTCHAs. Optional polite pacing remains off by default; baseline media pacing stays active. Site-dependent anti-bot behavior and Windows GUI rendering require independent runtime verification.

## 0.2.0 — 2026-10-13

### Added

- Actor/person photo galleries now show a **By title** tree with release years, title types, and separate downloadable albums.
- Movie, series, and episode galleries now show a **By person** tree with separate downloadable albums for tagged people.
- **All photos** remains visible at the expanded gallery root and preserves every original exposed by the selected gallery.
- **No title tags** and **No person tags** albums retain photos without usable grouping metadata. Photos with multiple tags appear in every matching group.
- Groups reuse the complete gallery catalog and preserve any existing filters. Older caches are refreshed to obtain tag metadata.
- Release notes now include only the published version's changelog entry.

### Coverage

IMDb person and title galleries can expose different photo sets, and photo tags can be absent or inaccurate. **All photos** covers the selected gallery. For a particular title's actor photos, use that title gallery's **By person** branch. Title groups list titles represented in a person's photos. Group counts can overlap, and selecting the whole gallery together with individual groups creates copies in their separate folders.

## 0.1.0 — 2026-10-13

First versioned release of Copperminer, including Coppermine, rule-based galleries, 4chan, Yandex Images, and IMDb photo galleries.

### Added

- Complete IMDb movie, series, episode, and person photo galleries, with cursor pagination and image-count validation.
- Individual IMDb media viewer photos, with a choice between the selected photo and its full gallery.
- IMDb Filters controls for combining people, titles, image types, and gallery IDs, including any/all matching.
- Original-resolution IMDb downloads without re-encoding or thumbnail fallback; complete catalogs integrate with cache and history.
- Explicit errors for blocked, restricted, changing, malformed, or incomplete IMDb galleries.
- Versioned source ZIP releases published after tests pass on `master`.

### Fixed

- Interrupted or empty downloads no longer leave a completed filename that subsequent attempts would skip.
- An unavailable IMDb selection no longer aborts downloads from other selected albums.
- Cancellation during IMDb album scraping stops the batch before creating the download queue.

### Limitations

- IMDb downloads cover the selected public gallery. Separate episode and filmography galleries require their own URLs.
- IMDb's web API availability and schema may change.
- Windows GUI rendering has not been verified for this release.
