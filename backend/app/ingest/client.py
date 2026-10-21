"""Клиент OWS v3: GraphQL (основные сущности) + REST (справочники).

Токен передаётся только в заголовке Authorization и никогда не логируется.
"""
import asyncio
import logging
from typing import AsyncIterator

import httpx

from app.config import settings

log = logging.getLogger("ows")


class OwsError(RuntimeError):
    pass


class OwsClient:
    def __init__(self, concurrency: int | None = None):
        if not settings.ows_token:
            raise OwsError("OWS_TOKEN не задан")
        self._http = httpx.AsyncClient(
            base_url=settings.ows_base_url,
            headers={"Authorization": f"Bearer {settings.ows_token}"},
            timeout=httpx.Timeout(120, connect=15),
        )
        self._sem = asyncio.Semaphore(concurrency or settings.ows_concurrency)

    async def close(self):
        await self._http.aclose()

    async def _request(self, method: str, url: str, **kw) -> dict:
        delay = 2.0
        for attempt in range(6):
            async with self._sem:
                try:
                    r = await self._http.request(method, url, **kw)
                except httpx.TransportError as e:
                    err = f"transport: {e!r}"
                else:
                    if r.status_code == 200:
                        return r.json()
                    if r.status_code in (401, 403):
                        raise OwsError(f"доступ запрещён ({r.status_code}) — проверьте OWS_TOKEN")
                    if r.status_code == 404:
                        return {}
                    err = f"HTTP {r.status_code}: {r.text[:200]}"
            log.warning("OWS %s %s попытка %d: %s", method, url, attempt + 1, err)
            await asyncio.sleep(delay)
            delay *= 2
        raise OwsError(f"OWS недоступен: {url}")

    async def get(self, path: str) -> dict:
        return await self._request("GET", path)

    async def graphql(self, query: str) -> dict:
        d = await self._request("POST", "/v3/graphql", json={"query": query})
        if d.get("errors"):
            raise OwsError(f"GraphQL error: {str(d['errors'])[:300]}")
        return d

    async def gql_paginate(self, entity: str, filter_: str, fields: str,
                           limit: int | None = None) -> AsyncIterator[list[dict]]:
        """Курсорная пагинация GraphQL по lastId."""
        limit = limit or settings.ows_page_size
        after = None
        while True:
            after_arg = f", after: {after}" if after else ""
            q = f"{{ {entity}(filter: {{{filter_}}}, limit: {limit}{after_arg}) {{ {fields} }} }}"
            d = await self.graphql(q)
            items = d["data"].get(entity) or []
            if items:
                yield items
            page = d.get("extensions", {}).get("pageInfo", {})
            if not page.get("hasNextPage") or not items:
                break
            after = page["lastId"]

    async def rest_paginate(self, path: str) -> AsyncIterator[list[dict]]:
        url = f"{path}?limit={settings.ows_page_size}"
        while url:
            d = await self._request("GET", url)
            items = d.get("items") or []
            if items:
                yield items
            nxt = d.get("next_page")
            url = nxt if nxt and items else None
