from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from marketplace_agent.browser import PublishUncertain, publish_listing
from marketplace_agent.config import Config, Profile
from marketplace_agent.models import Listing
from marketplace_agent.state import Job, State

logger = logging.getLogger(__name__)


def plan_day(config: Config, state: State, listings: list[Listing], day: date) -> int:
    zone = ZoneInfo(config.runtime.timezone)
    start = datetime.combine(day, time.min, zone)
    end = start + timedelta(days=1)
    existing = state.jobs_for_day(start, end)
    counts: dict[str, int] = {}
    for job in existing:
        counts[job["profile_id"]] = counts.get(job["profile_id"], 0) + 1
    used = state.scheduled_listing_ids()
    available = iter(listing for listing in listings if listing.id not in used)
    created = 0
    for profile in config.profiles:
        if not profile.enabled:
            continue
        missing = config.runtime.daily_limit_per_profile - counts.get(profile.id, 0)
        if missing <= 0:
            continue
        slots = _slots(config, profile, day, missing)
        for slot in slots:
            try:
                listing = next(available)
            except StopIteration:
                return created
            state.add_job(profile.id, listing.id, slot)
            created += 1
    return created


def _slots(config: Config, profile: Profile, day: date, count: int) -> list[datetime]:
    zone = ZoneInfo(config.runtime.timezone)
    candidates: list[datetime] = []
    for window in config.runtime.prime_time:
        current = datetime.combine(day, window.start, zone)
        end = datetime.combine(day, window.end, zone)
        while current <= end:
            candidates.append(current)
            current += timedelta(minutes=1)
    seed = f"{day.isoformat()}:{profile.id}"
    candidates.sort(
        key=lambda candidate: hashlib.sha256(
            f"{seed}:{candidate.isoformat()}".encode()
        ).digest()
    )
    selected: list[datetime] = []
    gap = timedelta(minutes=config.runtime.minimum_gap_minutes)
    for candidate in candidates:
        if all(abs(candidate - other) >= gap for other in selected):
            selected.append(candidate)
            if len(selected) == count:
                break
    if len(selected) != count:
        raise ValueError(
            f"prime-time windows cannot fit {count} jobs with the configured gap for {profile.id}"
        )
    return sorted(selected)


async def run_once(
    config: Config,
    state: State,
    listings: list[Listing],
    live: bool,
) -> dict[str, int]:
    now = datetime.now(ZoneInfo(config.runtime.timezone))
    jobs = state.claim_due(now) if live else state.preview_due(now)
    if not jobs:
        return {"published": 0, "dry_run": 0, "failed": 0, "unknown": 0}
    listing_map = {listing.id: listing for listing in listings}
    semaphore = asyncio.Semaphore(config.runtime.max_concurrency)

    async def execute(job: Job) -> str:
        async with semaphore:
            try:
                profile = config.profile(job.profile_id)
                if not profile.enabled:
                    raise ValueError(f"profile is disabled: {profile.id}")
                listing = listing_map[job.listing_id]
                result = await publish_listing(config, profile, listing, state, live)
                if live:
                    state.finish_job(job.id, result.status, result.url)
                logger.info("%s job=%s profile=%s", result.status, job.id, profile.id)
                return result.status
            except PublishUncertain as exc:
                state.finish_job(job.id, "unknown", error=str(exc))
                logger.error("unknown job=%s: %s", job.id, exc)
                return "unknown"
            except Exception as exc:
                if live:
                    state.finish_job(job.id, "failed", error=str(exc))
                logger.exception("failed job=%s", job.id)
                return "failed"

    results = await asyncio.gather(*(execute(job) for job in jobs))
    return {name: results.count(name) for name in ("published", "dry_run", "failed", "unknown")}


async def run_watch(config: Config, state: State, listings: list[Listing]) -> None:
    if not config.runtime.live:
        raise ValueError("watch mode requires live: true and the --live flag")
    while True:
        now = datetime.now(ZoneInfo(config.runtime.timezone))
        plan_day(config, state, listings, now.date())
        await run_once(config, state, listings, live=True)
        tomorrow = now + timedelta(days=1)
        if tomorrow.date() != now.date():
            plan_day(config, state, listings, tomorrow.date())
        await asyncio.sleep(30)
