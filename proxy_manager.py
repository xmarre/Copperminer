import asyncio
import aiohttp
import random
import time
import warnings
import json
import os
import logging
from typing import Callable, Optional

log = logging.getLogger("ripper.proxy")
log.setLevel(logging.INFO)

warnings.filterwarnings("ignore", category=ResourceWarning)

# Proxy sources powered by https://github.com/vakhov/fresh-proxy-list
PROXY_SOURCES = [
    "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt",
]

# URLs used to validate that a proxy works with real target sites
VALIDATION_URLS = [
    "https://natalie-dormer.com/photos/index.php?cat=39",
    "https://watson-emma.org/gallery/index.php",
    "https://sophie-turner.net/gallery/",
    "https://kristen-stewart.org/index.php?cat=0",
    "https://theplace-2.com/photos",
]

def pick_random_url() -> str:
    return random.choice(VALIDATION_URLS)

MIN_PROXIES = 40
VALIDATION_CONCURRENCY = 30

PROXY_CACHE_FILE = "proxy_cache.json"
CACHE_TTL_GOOD = 6 * 60 * 60  # 6 hours
CACHE_TTL_BAD = 12 * 60 * 60  # 12 hours


class ProxyCache:
    def __init__(self, filename: str = PROXY_CACHE_FILE):
        self.filename = filename
        self.cache: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
            except Exception:
                self.cache = {}

    def save(self) -> None:
        try:
            with open(self.filename, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2)
        except Exception:
            pass

    def clear(self) -> None:
        """Remove all cached proxy entries and delete the cache file."""
        self.cache.clear()
        try:
            os.remove(self.filename)
        except OSError:
            pass

    def update(self, proxy: str, status: str) -> None:
        self.cache[proxy] = {
            "status": status,
            "last_test": time.time(),
        }

    def should_test(self, proxy: str) -> bool:
        entry = self.cache.get(proxy)
        now = time.time()
        if not entry:
            return True
        if entry["status"] == "good" and now - entry["last_test"] < CACHE_TTL_GOOD:
            return False
        if entry["status"] == "bad" and now - entry["last_test"] < CACHE_TTL_BAD:
            return False
        return True

    def get_good_proxies(self) -> list[str]:
        now = time.time()
        return [
            p
            for p, e in self.cache.items()
            if e.get("status") == "good" and now - e.get("last_test", 0) < CACHE_TTL_GOOD
        ]


class ProxyPool:
    """Async proxy pool that keeps itself topped up."""

    def __init__(
        self,
        min_proxies: int = MIN_PROXIES,
        cache_file: str = PROXY_CACHE_FILE,
        fast_fill: int = 10,
        ready_callback: Optional[Callable[[], None]] = None,
        validation_concurrency: int = VALIDATION_CONCURRENCY,
        manual_proxies: Optional[list[str]] = None,
    ) -> None:
        self.cache = ProxyCache(cache_file)
        self.manual_proxies = manual_proxies or []
        self.pool: list[str] = []
        if self.manual_proxies:
            self.pool = list(dict.fromkeys(self.manual_proxies))
        else:
            self.pool = list(dict.fromkeys(self.cache.get_good_proxies()))
        self.min_proxies = min_proxies
        self.fast_fill = fast_fill
        self.ready_callback = ready_callback
        self.pool_ready = False
        self.lock = asyncio.Lock()
        self.refresh_task: asyncio.Task | None = None
        self.harvest_task: asyncio.Task | None = None
        self.last_checked: float = 0.0
        self.sema = asyncio.Semaphore(validation_concurrency)
        self.ready_event = asyncio.Event()
        self.stop_requested = False
        if self.manual_proxies:
            self._signal_ready()
        
    def _signal_ready(self) -> None:
        if not self.pool_ready:
            self.pool_ready = True
            self.ready_event.set()
            log.info("[PROXY] Ready with %d proxies", len(self.pool))
            if self.ready_callback:
                try:
                    self.ready_callback()
                except Exception:
                    pass

    async def wait_until_ready(self) -> None:
        """Wait until the pool has at least *fast_fill* proxies."""
        await self.ready_event.wait()

    async def fetch_proxies(self) -> set[str]:
        proxies: set[str] = set()
        async with aiohttp.ClientSession() as session:
            for url in PROXY_SOURCES:
                try:
                    async with session.get(url, timeout=15) as resp:
                        text = await resp.text()
                        for line in text.strip().splitlines():
                            line = line.strip()
                            if line.count(":") == 1 and 7 < len(line) < 25:
                                proxies.add(line)
                except Exception:
                    continue
        return proxies

    async def validate_proxy(self, proxy: str) -> bool:
        """Fully test *proxy* by loading multiple gallery pages."""
        async with self.sema:
            test_url = pick_random_url()
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    headers = {
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/119.0.0.0 Safari/537.36"
                        )
                    }

                    # Step 1: Fetch the main gallery page
                    async with session.get(
                        test_url, proxy=f"http://{proxy}", ssl=False, headers=headers
                    ) as resp:
                        html = await resp.text()
                    if not ("gallery" in html or "Coppermine" in html):
                        log.info(
                            "[PROXY] BAD: %s (no gallery keywords on index)", proxy
                        )
                        return False

                    # Step 2: Extract an album/thumbnail link
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html, "html.parser")
                    thumb_link = None
                    for a in soup.find_all("a", href=True):
                        if "thumbnails.php?album=" in a["href"]:
                            thumb_link = a["href"]
                            break
                    if not thumb_link:
                        log.info(
                            "[PROXY] BAD: %s (no album links found)", proxy
                        )
                        return False

                    # Step 3: Follow the album link
                    from urllib.parse import urljoin
                    thumb_url = thumb_link
                    if not thumb_link.startswith("http"):
                        thumb_url = urljoin(test_url, thumb_link)
                    async with session.get(
                        thumb_url, proxy=f"http://{proxy}", ssl=False, headers=headers
                    ) as resp2:
                        page2 = await resp2.text()
                    if "thumbnails" not in page2 and "album" not in page2:
                        log.info(
                            "[PROXY] BAD: %s (no thumbnails/album on album page)", proxy
                        )
                        return False

                    # Step 4: Try fetching an image from the album
                    soup2 = BeautifulSoup(page2, "html.parser")
                    img = soup2.find("img")
                    if img and img.get("src"):
                        img_url = urljoin(thumb_url, img["src"])
                        async with session.get(
                            img_url,
                            proxy=f"http://{proxy}",
                            ssl=False,
                            headers=headers,
                        ) as resp3:
                            ctype = resp3.headers.get("Content-Type", "")
                            if resp3.status != 200 or not ctype.startswith("image"):
                                log.info(
                                    "[PROXY] BAD: %s (could not load image)", proxy
                                )
                                return False

                    log.info("[PROXY] FULLY OK: %s", proxy)
                    return True
            except Exception as e:
                log.info("[PROXY] BAD: %s (exc: %r)", proxy, e)
        return False

    async def _finish_tasks(self, tasks: list[asyncio.Task]) -> None:
        """Background task to finalize validation of remaining proxy checks."""
        for t in tasks:
            proxy = getattr(t, "proxy", None)
            if proxy is None:
                continue
            try:
                ok = await t
            except Exception:
                ok = False
            self.cache.update(proxy, "good" if ok else "bad")
            if ok and proxy not in self.pool:
                self.pool.append(proxy)
        self.cache.save()
        if len(self.pool) >= self.fast_fill:
            self._signal_ready()

    async def replenish(self, fast_fill: int | None = None, force: bool = False) -> None:
        if self.manual_proxies:
            async with self.lock:
                self.pool = list(dict.fromkeys(self.manual_proxies))
                self._signal_ready()
            return
        async with self.lock:
            if self.stop_requested:
                return
            log.info("[PROXY] Replenishing proxies%s...", " (full)" if force else "")

            fast_fill = fast_fill or self.fast_fill

            cached_good = self.cache.get_good_proxies()
            if force:
                self.pool = []
            else:
                for p in cached_good:
                    if p not in self.pool:
                        self.pool.append(p)
                if len(self.pool) >= fast_fill:
                    self.last_checked = time.time()
                    self.cache.save()
                    log.info("[PROXY] Fast pool fill from cache: %d proxies.", len(self.pool))
                    self._signal_ready()
                    return

            raw = await self.fetch_proxies()
            if force:
                candidate = list(dict.fromkeys(cached_good + list(raw)))
                to_test = candidate
            else:
                to_test = [p for p in raw if self.cache.should_test(p)]

            tasks = []
            for p in to_test:
                t = asyncio.create_task(self.validate_proxy(p))
                t.proxy = p  # type: ignore[attr-defined]
                tasks.append(t)

            good_found = 0
            pending = set(tasks)

            while pending and not self.stop_requested:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for t in done:
                    proxy = getattr(t, "proxy", None)
                    if proxy is None:
                        continue
                    try:
                        ok = t.result()
                    except Exception:
                        ok = False
                    self.cache.update(proxy, "good" if ok else "bad")
                    if ok and proxy not in self.pool:
                        self.pool.append(proxy)
                        good_found += 1
                        if good_found >= fast_fill:
                            pending = set()
                            break
                if good_found >= fast_fill and not force:
                    break

            # Continue validating remaining proxies in background
            if pending:
                asyncio.create_task(self._finish_tasks(list(pending)))

            # Remove duplicates, limit pool size
            self.pool = list(dict.fromkeys(self.pool))
            if len(self.pool) > 150:
                self.pool = random.sample(self.pool, 150)
            self.last_checked = time.time()
            self.cache.save()
            log.info("[PROXY] Pool size: %d", len(self.pool))

    async def refresh(self, force: bool = False) -> None:
        await self.replenish(force=force)

    async def get_proxy(self) -> str:
        async with self.lock:
            if not self.pool:
                await self.replenish()
            if len(self.pool) < self.min_proxies and not self.manual_proxies:
                await self.replenish()
            while not self.pool:
                await self.replenish()
                await asyncio.sleep(2)
            p = random.choice(self.pool)
            log.debug("[PROXY] → %s", p)
            return p

    async def remove_proxy(self, proxy: str) -> None:
        log.debug("[PROXY] ✗ %s", proxy)
        async with self.lock:
            if proxy in self.pool and not self.manual_proxies:
                self.pool.remove(proxy)
            if not self.manual_proxies:
                self.cache.update(proxy, "bad")
                self.cache.save()

    def __len__(self) -> int:
        return len(self.pool)

    async def prune_dead_proxies(self) -> None:
        """Re-check proxies in the pool and remove any that fail validation."""
        tasks = []
        for p in list(self.pool):
            t = asyncio.create_task(self.validate_proxy(p))
            t.proxy = p  # type: ignore[attr-defined]
            tasks.append(t)
        for t in asyncio.as_completed(tasks):
            proxy = getattr(t, "proxy", None)
            try:
                ok = await t
            except Exception:
                ok = False
            if not ok and proxy:
                await self.remove_proxy(proxy)

    async def start_auto_refresh(self, interval: int = 600) -> None:
        async def auto_refresh():
            while not self.stop_requested:
                await self.replenish()
                await self.prune_dead_proxies()
                await asyncio.sleep(interval)

        if self.manual_proxies:
            return
        if not self.refresh_task:
            self.stop_requested = False
            log.info("[PROXY] Auto refresh every %d seconds", interval)
            self.refresh_task = asyncio.create_task(auto_refresh())

    async def stop_auto_refresh(self) -> None:
        """Stop any ongoing automatic proxy harvesting."""
        self.stop_requested = True
        if self.refresh_task:
            self.refresh_task.cancel()
            try:
                await self.refresh_task
            except Exception:
                pass
            self.refresh_task = None
        log.info("[PROXY] Auto refresh stopped")

    def clear_cache(self) -> None:
        """Clear cached proxy information."""
        self.cache.clear()
