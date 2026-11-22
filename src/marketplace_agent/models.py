from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class Listing:
    id: str
    title: str
    description: str
    price: Decimal
    city: str
    category: str
    photos: tuple[Path, Path]


@dataclass(frozen=True, slots=True)
class Lead:
    profile_id: str
    source: Literal["messenger", "group"]
    source_url: str
    source_key: str
    phone: str
    sender: str
    message: str | None
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class Publication:
    status: Literal["dry_run", "published"]
    url: str | None = None
