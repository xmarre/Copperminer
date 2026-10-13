"""IMDb public photo galleries and original image URLs.

IMDb's web gallery uses cursor connections, not numbered HTML pages. Keep
these requests separate from Coppermine's HTML cache and thumbnail heuristics.
"""

import hashlib
import re
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests


API_URL = "https://caching.graphql.imdb.com/"
CACHE_VERSION = 2
CACHE_SECONDS = 300
PAGE_SIZE = 100
IMAGE_FIELDS = (
    "id url width height caption { plainText } "
    "names(limit:null) { id nameText { text } } "
    "titles(limit:null) { id titleText { text } releaseYear { year endYear } titleType { id text } }"
)
HEADERS = {
    "Accept": "application/graphql+json, application/json",
    "Origin": "https://www.imdb.com",
    "Referer": "https://www.imdb.com/",
    "x-imdb-client-name": "imdb-web-next",
    "x-imdb-user-language": "en-US",
    "x-imdb-user-country": "US",
}


class IMDbError(RuntimeError):
    """An unavailable or incomplete IMDb gallery must not look successful."""


def is_imdb_url(url):
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "imdb.com", "www.imdb.com", "m.imdb.com",
    }


def original_image_url(url):
    """Remove Amazon's crop/resize/quality transforms; preserve asset and format."""
    if not isinstance(url, str):
        raise IMDbError("IMDb returned an image without a usable URL.")
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"m.media-amazon.com", "ia.media-imdb.com"}
        or not parsed.path.startswith("/images/M/")
    ):
        raise IMDbError("IMDb returned an unsupported image host or path.")
    path = re.sub(r"\._V\d+[^/]*\.(jpg|jpeg|png|webp|gif)$", r"._V1_.\1", parsed.path, flags=re.I)
    if not re.search(r"\.(jpg|jpeg|png|webp|gif)$", path, re.I):
        raise IMDbError("IMDb returned an unsupported image format.")
    return urlunparse(("https", parsed.hostname, path, "", "", ""))


def parse_gallery_url(url):
    """Map title/name links and gallery filters to IMDb's connection arguments."""
    if not is_imdb_url(url):
        raise IMDbError("Enter an IMDb title, person, photo gallery, or media viewer URL.")
    parsed = urlparse(url.strip())
    match = re.fullmatch(
        r"/(title|name)/((?:tt|nm)\d+)(?:/(mediaindex|mediaviewer)(?:/(rm\d+))?)?/?",
        parsed.path,
    )
    if not match:
        raise IMDbError("Unsupported IMDb page. Use a /title/tt…/ or /name/nm…/ photo gallery URL.")
    kind, entity_id, section, image_id = match.groups()
    if not entity_id.startswith("tt" if kind == "title" else "nm"):
        raise IMDbError("IMDb title/person ID does not match the URL path.")
    if (section == "mediaviewer") != bool(image_id):
        raise IMDbError("An IMDb media viewer URL must include its rm image ID.")
    params = parse_qs(parsed.query, keep_blank_values=True)
    supported = {
        "names", "titles", "types", "contentTypes", "refine", "allNames",
        "allTitles", "galleries", "allGalleries", "ref_", "ref", "page",
    }
    unknown = set(params) - supported
    if unknown:
        raise IMDbError("Unsupported IMDb gallery parameter(s): " + ", ".join(sorted(unknown)))
    values = {}
    for key in supported - {"ref_", "ref", "page"}:
        values[key] = sorted({v.strip() for raw in params.get(key, []) for v in raw.split(",") if v.strip()})
    for value in values.pop("refine"):
        target = "names" if re.fullmatch(r"nm\d+", value) else "titles" if re.fullmatch(r"tt\d+", value) else "types"
        values[target].append(value)
    values["types"] += values.pop("contentTypes")
    filters = {}
    canonical = {}
    for key, constraint, field, pattern in [
        ("names", "nameConstraints", "anyNameIds", r"nm\d+"),
        ("allNames", "nameConstraints", "allNameIds", r"nm\d+"),
        ("titles", "titleConstraints", "anyTitleIds", r"tt\d+"),
        ("allTitles", "titleConstraints", "allTitleIds", r"tt\d+"),
        ("galleries", "galleryConstraints", "anyGalleryIds", r"[A-Za-z0-9_-]+"),
        ("allGalleries", "galleryConstraints", "allGalleryIds", r"[A-Za-z0-9_-]+"),
    ]:
        items = sorted(set(values[key]))
        if any(not re.fullmatch(pattern, item) for item in items):
            raise IMDbError(f"Invalid IMDb {key} filter.")
        if items:
            filters.setdefault(constraint, {})[field] = items
            canonical[key] = ",".join(items)
    types = sorted(set(values["types"]))
    if any(not re.fullmatch(r"[a-z_]+", item) for item in types):
        raise IMDbError("Invalid IMDb image type filter.")
    if types:
        filters["types"] = types
        canonical["types"] = ",".join(types)
    base = f"https://www.imdb.com/{kind}/{entity_id}/"
    gallery_url = base + "mediaindex/"
    if canonical:
        gallery_url += "?" + urlencode(sorted(canonical.items()))
    return {"kind": kind, "id": entity_id, "image_id": image_id,
            "filter": filters, "url": gallery_url,
            "viewer_url": base + f"mediaviewer/{image_id}/" if image_id else None}


def _request(session, query, variables, log, stop_flag=None):
    for attempt in range(3):
        if stop_flag and stop_flag.is_set():
            raise IMDbError("IMDb discovery stopped by user.")
        try:
            with session.post(API_URL, headers=HEADERS, json={"query": query, "variables": variables}, timeout=30) as response:
                if response.status_code in {202, 401, 403}:
                    raise IMDbError(f"IMDb refused gallery access (HTTP {response.status_code}). Try again later; no partial gallery was saved.")
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    response.raise_for_status()
                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise IMDbError("IMDb returned a challenge or non-JSON response instead of gallery data.") from exc
                if not isinstance(payload, dict):
                    raise IMDbError("IMDb returned an invalid gallery response.")
                if payload.get("errors"):
                    messages = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in payload["errors"])
                    raise IMDbError("IMDb gallery request failed: " + messages)
                if not isinstance(payload.get("data"), dict):
                    raise IMDbError("IMDb returned no gallery data.")
                return payload["data"]
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if attempt == 2 or (status is not None and status != 429 and status < 500):
                raise IMDbError(f"IMDb gallery request failed: {exc}") from exc
            retry_after = getattr(getattr(exc, "response", None), "headers", {}).get("Retry-After", "")
            try:
                delay = min(30, max(1, float(retry_after)))
            except ValueError:
                delay = 2 ** attempt
            log(f"IMDb request failed; retrying in {delay:g}s.")
            if stop_flag:
                if stop_flag.wait(delay):
                    raise IMDbError("IMDb discovery stopped by user.")
            else:
                time.sleep(delay)


def _image(node):
    if not isinstance(node, dict) or not re.fullmatch(r"rm\d+", str(node.get("id", ""))):
        raise IMDbError("IMDb returned an invalid image record.")
    return {"id": node["id"], "url": original_image_url(node.get("url")),
            "caption": (node.get("caption") or {}).get("plainText") or node["id"],
            "width": node.get("width"), "height": node.get("height"),
            "names": _tags(node.get("names"), "name"),
            "titles": _tags(node.get("titles"), "title")}


def _tags(values, kind):
    """Normalize optional IMDb tags without making image availability depend on them."""
    tags = {}
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, dict):
            continue
        tag_id = value.get("id")
        if not isinstance(tag_id, str) or not re.fullmatch(r"tt\d+" if kind == "title" else r"nm\d+", tag_id):
            continue
        text = value.get("titleText" if kind == "title" else "nameText")
        text = text.get("text") if isinstance(text, dict) else None
        tag = {"id": tag_id, "text": text if isinstance(text, str) and text else tag_id}
        if kind == "title":
            years = value.get("releaseYear") or {}
            title_type = value.get("titleType") or {}
            tag.update(year=years.get("year") if isinstance(years, dict) else None,
                       end_year=years.get("endYear") if isinstance(years, dict) else None,
                       title_type=title_type.get("text") if isinstance(title_type, dict) else None)
        tags[tag_id] = tag
    return list(tags.values())


def _group_url(gallery_url, kind, tag_id):
    """Identify a local tag subset without changing the parent gallery's filters."""
    return gallery_url + "#" + urlencode({"imdb_group": f"{kind}:{tag_id}"})


def _group_selection(url):
    """Validate the downloader-only subset carried in a gallery URL fragment."""
    fragment = parse_qs(urlparse(url).fragment, keep_blank_values=True)
    if "imdb_group" not in fragment:
        return None
    values = fragment["imdb_group"]
    match = re.fullmatch(r"(name|title):((?:nm|tt)\d+|untagged)", values[0]) if len(values) == 1 else None
    if not match:
        raise IMDbError("Invalid IMDb photo group selection.")
    kind, tag_id = match.groups()
    if tag_id != "untagged" and not tag_id.startswith("nm" if kind == "name" else "tt"):
        raise IMDbError("IMDb photo group ID does not match its group type.")
    return kind, tag_id


def _group_images(images, kind, tag_id):
    """Select matching records; one multi-tagged image may belong to several groups."""
    key = "names" if kind == "name" else "titles"
    if tag_id == "untagged":
        return [image for image in images if not image.get(key)]
    return [image for image in images if any(tag["id"] == tag_id for tag in image.get(key, []))]


def _title_label(tag):
    """Display year and title type while keeping same-name titles distinguishable."""
    year = str(tag["year"]) if type(tag.get("year")) is int else "Year unknown"
    if type(tag.get("end_year")) is int and tag["end_year"] != tag.get("year"):
        year += f"–{tag['end_year']}"
    title_type = f" — {tag['title_type']}" if tag.get("title_type") else ""
    return f"{year} — {tag['text']}{title_type} ({tag['id']})"


def _group_node(catalog, spec, path):
    """Build counterpart tag albums from a complete parent catalog, including untagged records."""
    kind = "title" if spec["kind"] == "name" else "name"
    key = "titles" if kind == "title" else "names"
    heading = "By title" if kind == "title" else "By person"
    tags = {}
    counts = {}
    untagged = set()
    for image in catalog["images"]:
        values = image.get(key, [])
        if not values:
            untagged.add(image["url"])
        for tag in values:
            tags.setdefault(tag["id"], tag)
            counts.setdefault(tag["id"], set()).add(image["url"])
    albums = []
    if kind == "title":
        ordered = sorted(tags.values(), key=lambda tag: (tag.get("year") if type(tag.get("year")) is int else 9999, tag["text"].casefold(), tag["id"]))
    else:
        ordered = sorted(tags.values(), key=lambda tag: (tag["text"].casefold(), tag["id"]))
    for tag in ordered:
        label = _title_label(tag) if kind == "title" else f"{tag['text']} ({tag['id']})"
        albums.append({"type": "album", "name": label,
                       "url": _group_url(spec["url"], kind, tag["id"]),
                       "image_count": len(counts[tag["id"]]),
                       "path": path + [heading, label], "adapter": "imdb"})
    if untagged:
        label = "No title tags" if kind == "title" else "No person tags"
        albums.append({"type": "album", "name": label,
                       "url": _group_url(spec["url"], kind, "untagged"),
                       "image_count": len(untagged), "path": path + [heading, label],
                       "adapter": "imdb"})
    return {"type": "category", "name": heading, "url": spec["url"],
            "children": [], "specials": [], "albums": albums, "adapter": "imdb"}


def _query(kind):
    filter_type = "ImagesFilter" if kind == "title" else "NameImagesFilter"
    label = "titleText" if kind == "title" else "nameText"
    return (
        f"query CopperminerImages($id:ID!, $after:ID, $filter:{filter_type}, $first:Int!) {{ "
        f"{kind}(id:$id) {{ id {label} {{ text }} "
        "images(first:$first, after:$after, filter:$filter) { total isPaginationDirty "
        f"edges {{ node {{ {IMAGE_FIELDS} }} }} "
        "pageInfo { endCursor hasNextPage } restriction { reasons unrestrictedTotal } } } }"
    )


def load_gallery(url, session, page_cache, log=lambda msg: None, quick_scan=True, stop_flag=None):
    """Fetch every cursor page atomically; cache only a verified complete catalog."""
    spec = parse_gallery_url(url)
    cached = page_cache.get(spec["url"], {}).get("imdb")
    if quick_scan and cached and cached.get("version") == CACHE_VERSION and time.time() - cached.get("timestamp", 0) <= CACHE_SECONDS:
        return cached
    query = _query(spec["kind"])
    cursor = None
    seen_cursors = set()
    images = {}
    expected_total = None
    label = spec["id"]
    while True:
        data = _request(session, query, {"id": spec["id"], "first": PAGE_SIZE,
                        "after": cursor, "filter": spec["filter"] or None}, log, stop_flag)
        entity = data.get(spec["kind"])
        if not isinstance(entity, dict) or entity.get("id") != spec["id"]:
            raise IMDbError("IMDb could not resolve the requested title/person.")
        label = (entity.get("titleText" if spec["kind"] == "title" else "nameText") or {}).get("text") or label
        connection = entity.get("images")
        if not isinstance(connection, dict):
            raise IMDbError("IMDb returned no image connection for this gallery.")
        total = connection.get("total")
        if type(total) is not int or total < 0:
            raise IMDbError("IMDb returned an invalid gallery total.")
        if connection.get("isPaginationDirty") or (expected_total is not None and expected_total != total):
            raise IMDbError("IMDb gallery changed during pagination. Discover it again for a complete snapshot.")
        expected_total = total
        restriction = connection.get("restriction") or {}
        if restriction.get("reasons") or (restriction.get("unrestrictedTotal") or total) > total:
            raise IMDbError("IMDb restricted this gallery; a complete public image list is unavailable.")
        edges = connection.get("edges")
        if not isinstance(edges, list):
            raise IMDbError("IMDb returned an invalid gallery page.")
        previous_count = len(images)
        for edge in edges:
            image = _image(edge.get("node") if isinstance(edge, dict) else None)
            images[image["id"]] = image
        if len(images) > total:
            raise IMDbError("IMDb returned more images than its reported gallery total.")
        log(f"IMDb: collected {len(images)} / {total} photos of {label}.")
        info = connection.get("pageInfo") or {}
        if type(info.get("hasNextPage")) is not bool:
            raise IMDbError("IMDb returned no pagination status.")
        if not info["hasNextPage"]:
            break
        cursor = info.get("endCursor")
        if len(images) == previous_count or not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
            raise IMDbError("IMDb pagination did not advance; refusing an incomplete gallery.")
        seen_cursors.add(cursor)
    if len(images) != expected_total:
        raise IMDbError(f"IMDb returned {len(images)} unique images but reports {expected_total}; refusing an incomplete gallery.")
    catalog = {"version": CACHE_VERSION, "timestamp": time.time(), "label": label,
               "images": list(images.values())}
    page_cache[spec["url"]] = {"imdb": catalog}
    return catalog


def load_image(url, session, page_cache, log=lambda msg: None, quick_scan=True, stop_flag=None):
    spec = parse_gallery_url(url)
    key = spec["viewer_url"]
    cached = page_cache.get(key, {}).get("imdb_image")
    if quick_scan and cached and cached.get("version") == CACHE_VERSION and time.time() - cached.get("timestamp", 0) <= CACHE_SECONDS:
        return cached["image"]
    query = f"query CopperminerImage($id:ID!) {{ image(id:$id) {{ {IMAGE_FIELDS} }} }}"
    data = _request(session, query, {"id": spec["image_id"]}, log, stop_flag)
    image = _image(data.get("image"))
    if image["id"] != spec["image_id"]:
        raise IMDbError("IMDb returned a different image than the requested media viewer photo.")
    page_cache[key] = {"imdb_image": {"version": CACHE_VERSION, "timestamp": time.time(), "image": image}}
    return image


def discover_tree(url, session, page_cache, log=lambda msg: None, quick_scan=True, stop_flag=None):
    spec = parse_gallery_url(url)
    catalog = load_gallery(url, session, page_cache, log, quick_scan, stop_flag)
    label = f"{catalog['label']} ({spec['id']})"
    path = ["IMDb", label]
    filter_label = spec["url"].split("?", 1)[-1] if spec["filter"] else ""
    if filter_label:
        # Different selections must not be mistaken for completed downloads
        # merely because another selection has the same file count.
        path.append("Filtered " + hashlib.sha1(filter_label.encode()).hexdigest()[:12])
    albums = [{"type": "album", "name": "Filtered photos" if spec["filter"] else "All photos",
               "url": spec["url"], "image_count": len({image["url"] for image in catalog["images"]}), "path": path,
               "adapter": "imdb"}]
    if spec["image_id"]:
        image = load_image(url, session, page_cache, log, quick_scan, stop_flag)
        albums.insert(0, {"type": "album", "name": f"Selected photo ({image['id']})",
                         "url": spec["viewer_url"], "image_count": 1,
                         "path": path + [f"Selected photo {image['id']}"],
                         "adapter": "imdb"})
    groups = _group_node(catalog, spec, path)
    log(f"IMDb: {len(groups['albums'])} photo group(s); All photos retains the complete gallery independently of tags.")
    return {"type": "category", "name": label, "url": url, "children": [groups],
            "specials": [], "albums": albums, "adapter": "imdb"}


def image_entries(url, session, page_cache, log=lambda msg: None, quick_scan=True, stop_flag=None):
    spec = parse_gallery_url(url)
    selection = _group_selection(url)
    if spec["image_id"]:
        if selection:
            raise IMDbError("IMDb photo groups apply to galleries, not individual viewer photos.")
        images = [load_image(url, session, page_cache, log, quick_scan, stop_flag)]
        referer = spec["viewer_url"]
    else:
        images = load_gallery(url, session, page_cache, log, quick_scan, stop_flag)["images"]
        referer = spec["url"]
        if selection:
            images = _group_images(images, *selection)
    # Only original URLs are offered: a failed original must never silently
    # turn into a successful thumbnail download.
    unique = {}
    for image in images:
        unique.setdefault(image["url"], (image["caption"], [image["url"]], referer))
    return list(unique.values())
