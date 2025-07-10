from proxylist import ProxyList
import asyncio
import random

class ProxyPool:
    def __init__(self):
        self.pool = []
        self.lock = asyncio.Lock()
        self.refresh_task = None

    async def refresh(self):
        async with self.lock:
            pl = ProxyList()
            await pl.load()
            self.pool = [f"{p.host}:{p.port}" for p in pl]
            print(f"[PROXY] Pool size: {len(self.pool)}")

    async def get_proxy(self):
        async with self.lock:
            if not self.pool:
                await self.refresh()
            return random.choice(self.pool)

    async def remove_proxy(self, proxy):
        async with self.lock:
            if proxy in self.pool:
                self.pool.remove(proxy)

    def __len__(self):
        return len(self.pool)

    async def start_auto_refresh(self, interval=600):
        async def auto_refresh():
            while True:
                await asyncio.sleep(interval)
                await self.refresh()
        if not self.refresh_task:
            self.refresh_task = asyncio.create_task(auto_refresh())
