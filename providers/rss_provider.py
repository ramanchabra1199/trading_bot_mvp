from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import feedparser

from providers.news_provider_base import UnifiedNewsItem

log = logging.getLogger(__name__)


@dataclass
class RSSFeed:
    name: str
    url: str


class RSSProvider:
    def __init__(self, feeds: Dict[str, str]) -> None:
        self.feeds = [RSSFeed(name=k, url=v) for k, v in (feeds or {}).items()]

    def fetch(self) -> List[UnifiedNewsItem]:
        items, _ = self.fetch_with_stats()
        return items

    def fetch_with_stats(self) -> tuple[List[UnifiedNewsItem], Dict[str, Any]]:
        items: List[UnifiedNewsItem] = []
        ok = 0
        ok_empty = 0
        failed = 0
        ok_feeds: List[str] = []
        empty_feeds: List[str] = []
        failed_feeds: List[str] = []
        failures: Dict[str, str] = {}
        for feed in self.feeds:
            try:
                parsed = feedparser.parse(feed.url)
                entries = getattr(parsed, "entries", []) or []
                if entries:
                    ok += 1
                    ok_feeds.append(feed.name)
                else:
                    ok_empty += 1
                    empty_feeds.append(feed.name)

                for e in entries:
                    title = (getattr(e, "title", "") or "").strip()
                    link = (getattr(e, "link", "") or "").strip()
                    summary = (getattr(e, "summary", "") or "").strip()

                    if not title or not link:
                        continue

                    published_at = self._parse_datetime(e)
                    host = urlparse(link).hostname or ""

                    items.append(
                        UnifiedNewsItem(
                            provider=feed.name,
                            title=title,
                            url=link,
                            summary=summary,
                            published_at=published_at,
                            source=host,
                        )
                    )
            except Exception as e:
                failed += 1
                failed_feeds.append(feed.name)
                failures[feed.name] = f"{type(e).__name__}: {e}"
                log.exception("RSSProvider failed for feed=%s", feed.name)

        stats = {
            "providers_ok": ok,
            "providers_ok_empty": ok_empty,
            "providers_failed": failed,
            "ok_feeds": ok_feeds,
            "empty_feeds": empty_feeds,
            "failed_feeds": failed_feeds,
            "failures": failures,
        }
        return items, stats

    def _parse_datetime(self, entry) -> Optional[datetime]:
        struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        if not struct:
            return None
        try:
            return datetime(*struct[:6], tzinfo=timezone.utc)
        except Exception:
            return None
