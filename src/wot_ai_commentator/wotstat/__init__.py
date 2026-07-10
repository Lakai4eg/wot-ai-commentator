"""Клиент и маппер протокола WotStat DataProvider (ws://localhost:38200)."""

from __future__ import annotations

from .client import DataProviderClient
from .mapper import EventMapper

__all__ = ["DataProviderClient", "EventMapper"]
