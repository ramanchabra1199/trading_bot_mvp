from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class UnifiedNewsItem:
    provider: str                 # e.g. "Reuters_Top"
    title: str
    url: str
    summary: str = ""
    published_at: Optional[datetime] = None  # timezone-aware (UTC recommended)
    source: str = ""              # hostname if available
