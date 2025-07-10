import asyncio
import aiohttp
import random

PROXY_SOURCES = [
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://www.proxy-list.download/api/v1/get?type=http",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
]

TEST_URL = "https://httpbin.org/ip"
MIN_PROXIES = 40


class ProxyPool:
    """Async proxy pool that keeps itself topped up."""

    def __init__(self, min_proxies: int = MIN_PROXIES):
        self.pool: list[str] = []
        self.min_proxies = min_proxies
        self.lock = asyncio.Lock()
        self.refresh_task: asyncio.Task | None = None

    async def fetch_proxies(self) -> set[str]:
        proxies: set[str] = set()
        async with aiohttp.ClientSession() as session:
            for url in PROXY_SOURCES:
                try:
                    async with session.get(url, timeout=15) as resp:
                        text = await resp.text()
                        for line in text.strip().splitlines():
                            line = line.strip()
                            if ":" in line and len(line) < 25:
                                proxies.add(line)
                except Exception:
                    continue
        return proxies

    async def validate_proxy(self, proxy: str) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(TEST_URL, proxy=f"http://{proxy}", timeout=8) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def replenish(self) -> None:
        async with self.lock:
            print("[PROXY] Replenishing proxies...")
            raw = await self.fetch_proxies()
            to_test = raw - set(self.pool)
            tasks = [self.validate_proxy(p) for p in to_test]
            results = await asyncio.gather(*tasks)
            for proxy, ok in zip(to_test, results):
                if ok:
                    self.pool.append(proxy)
            # deduplicate and keep pool small
            self.pool = list(dict.fromkeys(self.pool))
            if len(self.pool) > 100:
                self.pool = random.sample(self.pool, 100)
            print(f"[PROXY] Pool size: {len(self.pool)}")

    async def refresh(self) -> None:
        await self.replenish()

    async def get_proxy(self) -> str:
        async with self.lock:
            if len(self.pool) < self.min_proxies:
                await self.replenish()
            while not self.pool:
                await self.replenish()
                await asyncio.sleep(2)
            return random.choice(self.pool)

    async def remove_proxy(self, proxy: str) -> None:
        async with self.lock:
            if proxy in self.pool:
                self.pool.remove(proxy)

    def __len__(self) -> int:
        return len(self.pool)

    async def start_auto_refresh(self, interval: int = 600) -> None:
        async def auto_refresh():
            while True:
                await asyncio.sleep(interval)
                await self.replenish()

        if not self.refresh_task:
            self.refresh_task = asyncio.create_task(auto_refresh())
