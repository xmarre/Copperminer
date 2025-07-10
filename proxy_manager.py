import asyncio
import contextlib
from proxybroker import Broker
import aiohttp
import random

class ProxyPool:
    def __init__(self, min_proxies=10, max_proxies=30, test_url='https://httpbin.org/ip'):
        self.min_proxies = min_proxies
        self.max_proxies = max_proxies
        self.test_url = test_url
        self.pool = set()
        self.lock = asyncio.Lock()
        self.refresh_task = None

    async def validate_proxy(self, proxy):
        url = self.test_url
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                proxy_url = f"http://{proxy.host}:{proxy.port}"
                async with session.get(url, proxy=proxy_url, timeout=8) as resp:
                    if resp.status == 200:
                        return True
        except Exception:
            pass
        return False

    async def fill_pool(self):
        queue = asyncio.Queue()
        broker = Broker(queue)
        gather_task = asyncio.create_task(
            broker.find(types=['HTTP', 'HTTPS'], limit=self.max_proxies)
        )

        validated = set()
        while len(validated) < self.max_proxies:
            try:
                proxy = await asyncio.wait_for(queue.get(), timeout=10)
            except asyncio.TimeoutError:
                break
            if await self.validate_proxy(proxy):
                validated.add(f"{proxy.host}:{proxy.port}")
                print(f"[PROXY] Good: {proxy.host}:{proxy.port}")
            else:
                print(f"[PROXY] Bad: {proxy.host}:{proxy.port}")

        await broker.stop()
        with contextlib.suppress(asyncio.CancelledError):
            await gather_task
        return validated

    async def refresh(self):
        async with self.lock:
            print("[PROXY] Refreshing proxy pool...")
            self.pool = await self.fill_pool()
            print(f"[PROXY] Pool size: {len(self.pool)}")

    async def get_proxy(self):
        async with self.lock:
            if not self.pool:
                await self.refresh()
            if not self.pool:
                raise Exception("No proxies available.")
            return random.choice(list(self.pool))

    async def remove_proxy(self, proxy):
        async with self.lock:
            self.pool.discard(proxy)

    async def start_auto_refresh(self, interval=600):
        async def auto_refresh():
            while True:
                await asyncio.sleep(interval)
                await self.refresh()
        if not self.refresh_task:
            self.refresh_task = asyncio.create_task(auto_refresh())

