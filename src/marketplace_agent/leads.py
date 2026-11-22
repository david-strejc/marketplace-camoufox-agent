from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import phonenumbers
from phonenumbers import PhoneNumberMatcher

from marketplace_agent.models import Lead


@dataclass(frozen=True, slots=True)
class RawItem:
    source_key: str
    sender: str
    text: str


def stable_source_key(url: str, sender: str, text: str) -> str:
    return hashlib.sha256(f"{url}\0{sender}\0{text}".encode()).hexdigest()[:32]


def extract_leads(
    profile_id: str,
    source: Literal["messenger", "group"],
    source_url: str,
    items: list[RawItem],
    observed_at: datetime,
    region: str,
    store_message_text: bool,
) -> list[Lead]:
    leads: list[Lead] = []
    for item in items:
        for match in PhoneNumberMatcher(item.text, region):
            number = match.number
            if not phonenumbers.is_valid_number(number):
                continue
            phone = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
            leads.append(
                Lead(
                    profile_id=profile_id,
                    source=source,
                    source_url=source_url,
                    source_key=item.source_key or stable_source_key(source_url, item.sender, item.text),
                    phone=phone,
                    sender=item.sender[:200],
                    message=item.text[:2000] if store_message_text else None,
                    observed_at=observed_at,
                )
            )
    return leads
