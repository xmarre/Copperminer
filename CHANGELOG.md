# Changelog

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
