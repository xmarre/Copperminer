# Changelog

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
