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

PROXY_SOURCES = [
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://proxyspace.pro/http.txt",
    "https://www.proxy-list.download/api/v1/get?type=http",
]

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
        self.last_checked: float = 0.0
        self.sema = asyncio.Semaphore(validation_concurrency)
        self.ready_event = asyncio.Event()
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
        async with self.sema:
            for test_url in ["http://github.com", "https://github.com"]:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            test_url, proxy=f"http://{proxy}", timeout=15
                        ) as resp:
                            if resp.status in {200, 301, 302}:
                                log.info("[PROXY] OK: %s on %s", proxy, test_url)
                                return True
                except Exception:
                    continue
        log.info("[PROXY] BAD: %s", proxy)
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

    async def replenish(self, fast_fill: int | None = None) -> None:
        if self.manual_proxies:
            async with self.lock:
                self.pool = list(dict.fromkeys(self.manual_proxies))
                self._signal_ready()
            return
        async with self.lock:
            log.info("[PROXY] Replenishing proxies...")

            fast_fill = fast_fill or self.fast_fill

            # Load cached good proxies first
            for p in self.cache.get_good_proxies():
                if p not in self.pool:
                    self.pool.append(p)
            if len(self.pool) >= fast_fill:
                self.last_checked = time.time()
                self.cache.save()
                log.info("[PROXY] Fast pool fill from cache: %d proxies.", len(self.pool))
                self._signal_ready()
                return

            raw = await self.fetch_proxies()
            to_test = [p for p in raw if self.cache.should_test(p)]

            tasks = []
            for p in to_test:
                t = asyncio.create_task(self.validate_proxy(p))
                t.proxy = p  # type: ignore[attr-defined]
                tasks.append(t)

            good_found = 0
            pending = set(tasks)

            while pending:
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
                if good_found >= fast_fill:
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

    async def refresh(self) -> None:
        await self.replenish()

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

    async def start_auto_refresh(self, interval: int = 600) -> None:
        async def auto_refresh():
            while True:
                await asyncio.sleep(interval)
                await self.replenish()

        if self.manual_proxies:
            return
        if not self.refresh_task:
            log.info("[PROXY] Auto refresh every %d seconds", interval)
            self.refresh_task = asyncio.create_task(auto_refresh())
