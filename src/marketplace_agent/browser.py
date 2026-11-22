from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from filelock import FileLock
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from marketplace_agent.agent import SelectorAgent
from marketplace_agent.config import Config, Profile, Selector
from marketplace_agent.leads import RawItem, extract_leads, stable_source_key
from marketplace_agent.models import Lead, Listing, Publication
from marketplace_agent.state import State


class BrowserError(RuntimeError):
    pass


class ManualInterventionRequired(BrowserError):
    pass


class SelectorFailure(BrowserError):
    pass


class PublishUncertain(BrowserError):
    pass


@asynccontextmanager
async def open_profile(config: Config, profile: Profile) -> AsyncIterator[Page]:
    from camoufox.async_api import AsyncCamoufox

    profile_dir = config.runtime.profiles_dir / profile.id
    profile_dir.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(profile_dir / ".browser.lock"))
    options: dict[str, object] = {
        "headless": config.runtime.headless,
        "os": config.runtime.operating_system,
        "humanize": True,
        "persistent_context": True,
        "user_data_dir": str(profile_dir),
        "locale": config.runtime.locale,
        "enable_cache": True,
        "window": (1365, 900),
    }
    if profile.proxy:
        options["proxy"] = profile.proxy.playwright()
        options["geoip"] = True
    with lock:
        async with AsyncCamoufox(**options) as context:  # type: ignore[arg-type]
            context.set_default_timeout(config.runtime.action_timeout_ms)
            context.set_default_navigation_timeout(config.runtime.navigation_timeout_ms)
            page = context.pages[0] if context.pages else await context.new_page()
            yield page


async def login(config: Config, profile: Profile) -> None:
    if config.runtime.headless:
        raise ValueError("initial login requires a visible browser (headless: false)")
    async with open_profile(config, profile) as page:
        await page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
        await asyncio.to_thread(
            input,
            f"Log in manually in profile {profile.id}, solve any requested verification, "
            "then press Enter here to save the session...",
        )
        if any(part in page.url.lower() for part in ("login", "checkpoint", "challenge")):
            raise ManualInterventionRequired("Facebook still shows a login or verification page")


async def publish_listing(
    config: Config,
    profile: Profile,
    listing: Listing,
    state: State,
    live: bool,
) -> Publication:
    async with open_profile(config, profile) as page:
        await page.goto(config.facebook.create_item_url, wait_until="domcontentloaded")
        await _ensure_no_interruption(page)
        for target, value in (
            ("title", listing.title),
            ("price", format(listing.price, "f")),
            ("description", listing.description),
        ):
            locator = await _find(config, page, profile.id, target, state)
            await locator.fill(value)
        for target, value in (("category", listing.category), ("city", listing.city)):
            locator = await _find(config, page, profile.id, target, state)
            await locator.fill(value)
            await locator.press("ArrowDown")
            await locator.press("Enter")
        photos = await _find(config, page, profile.id, "photos", state)
        await photos.set_input_files([str(path) for path in listing.photos])
        next_button = await _find(config, page, profile.id, "next", state)
        await next_button.click()
        publish_button = await _find(config, page, profile.id, "publish", state)
        if not live:
            return Publication(status="dry_run")
        await publish_button.click()
        try:
            await page.wait_for_url(
                re.compile(r"/marketplace/(item/|you/listings)"),
                timeout=config.runtime.navigation_timeout_ms,
            )
        except PlaywrightTimeoutError as exc:
            await _capture(page, config.runtime.artifacts_dir, profile.id, "publish-unknown")
            raise PublishUncertain(
                "Publish was clicked but no confirmation arrived; inspect Facebook before retrying"
            ) from exc
        return Publication(status="published", url=page.url if "/marketplace/item/" in page.url else None)


async def collect_profile_leads(config: Config, profile: Profile) -> list[Lead]:
    observed_at = datetime.now(UTC)
    leads: list[Lead] = []
    async with open_profile(config, profile) as page:
        messenger = await _collect_messenger(config, page, profile, observed_at)
        leads.extend(messenger)
        for url in profile.group_post_urls:
            await page.goto(url, wait_until="domcontentloaded")
            await _ensure_no_interruption(page)
            items = await _read_items(
                page,
                config.facebook.selectors.get("group_items", []),
                config.facebook.selectors.get("group_sender"),
                config.collection.max_items_per_surface,
                url,
            )
            leads.extend(
                extract_leads(
                    profile.id,
                    "group",
                    url,
                    items,
                    observed_at,
                    config.content.phone_region,
                    config.collection.store_message_text,
                )
            )
    return leads


async def _collect_messenger(
    config: Config, page: Page, profile: Profile, observed_at: datetime
) -> list[Lead]:
    await page.goto(config.facebook.messenger_url, wait_until="domcontentloaded")
    await _ensure_no_interruption(page)
    thread_selectors = config.facebook.selectors.get("messenger_threads", [])
    message_selectors = config.facebook.selectors.get("messenger_messages", [])
    if not thread_selectors or not message_selectors:
        return []
    threads = await _all_matching(page, thread_selectors)
    if threads is None:
        return []
    leads: list[Lead] = []
    thread_count = min(await threads.count(), config.collection.max_threads_per_profile)
    for index in range(thread_count):
        thread = threads.nth(index)
        thread_text = await thread.inner_text()
        href = await thread.get_attribute("href")
        await thread.click()
        messages = await _all_matching(page, message_selectors)
        if messages is None:
            continue
        thread_url = page.url
        items = await _read_items(
            page,
            message_selectors,
            config.facebook.selectors.get("messenger_sender"),
            config.collection.max_items_per_surface,
            thread_url,
        )
        thread_key = href or stable_source_key(thread_url, "thread", thread_text)
        for item in items:
            item = RawItem(
                source_key=stable_source_key(thread_key, item.sender, item.text),
                sender=item.sender,
                text=item.text,
            )
            leads.extend(
                extract_leads(
                    profile.id,
                    "messenger",
                    thread_url,
                    [item],
                    observed_at,
                    config.content.phone_region,
                    config.collection.store_message_text,
                )
            )
    return leads


async def _read_items(
    page: Page,
    item_selectors: list[Selector],
    sender_selectors: list[Selector] | None,
    limit: int,
    source_url: str,
) -> list[RawItem]:
    items = await _all_matching(page, item_selectors)
    if items is None:
        return []
    result: list[RawItem] = []
    for index in range(min(await items.count(), limit)):
        item = items.nth(index)
        text = (await item.inner_text()).strip()
        if not text:
            continue
        sender = text.splitlines()[0][:200]
        if sender_selectors:
            sender_locator = await _all_matching(item, sender_selectors)
            if sender_locator is not None and await sender_locator.count():
                sender = (await sender_locator.first.inner_text()).strip()[:200]
        external_id = await item.get_attribute("id")
        source_key = external_id or stable_source_key(source_url, sender, text)
        result.append(RawItem(source_key=source_key, sender=sender, text=text))
    return result


async def _all_matching(parent: Page | Locator, specs: list[Selector]) -> Locator | None:
    for spec in specs:
        locator = _locator(parent, spec)
        try:
            if await locator.count():
                return locator
        except PlaywrightTimeoutError:
            continue
    return None


def _locator(parent: Page | Locator, spec: Selector) -> Locator:
    if spec.css is not None:
        return parent.locator(spec.css)
    if spec.role is not None:
        return parent.get_by_role(spec.role, name=spec.name, exact=spec.exact)  # type: ignore[arg-type]
    if spec.text is not None:
        return parent.get_by_text(spec.text, exact=spec.exact)
    if spec.label is not None:
        return parent.get_by_label(spec.label, exact=spec.exact)
    if spec.placeholder is not None:
        return parent.get_by_placeholder(spec.placeholder, exact=spec.exact)
    raise ValueError("selector has no strategy")


async def _find(
    config: Config, page: Page, profile_id: str, target: str, state: State
) -> Locator:
    for spec in config.facebook.selectors[target]:
        locator = _locator(page, spec)
        try:
            if await locator.count() != 1:
                continue
            await locator.wait_for(
                state="attached" if target == "photos" else "visible",
                timeout=config.runtime.action_timeout_ms,
            )
            return locator
        except PlaywrightTimeoutError:
            continue
    if config.agent.enabled:
        locator = await _heal(config, page, profile_id, target, state)
        if locator is not None:
            return locator
    await _capture(page, config.runtime.artifacts_dir, profile_id, target)
    raise SelectorFailure(f"no unique working selector for {target}")


async def _heal(
    config: Config, page: Page, profile_id: str, target: str, state: State
) -> Locator | None:
    proposal = await SelectorAgent(config.agent).propose_selectors(page, target)
    for candidate in proposal.candidates:
        if not candidate.selector or len(candidate.selector) > 300:
            continue
        try:
            locator = page.locator(candidate.selector)
            if await locator.count() != 1:
                continue
            await locator.wait_for(
                state="attached" if target == "photos" else "visible",
                timeout=config.runtime.action_timeout_ms,
            )
        except PlaywrightError:
            continue
        state.add_selector_proposal(
            profile_id, target, candidate.selector, candidate.rationale
        )
        return locator
    return None


async def _ensure_no_interruption(page: Page) -> None:
    path = urlparse(page.url).path.lower()
    if any(part in path for part in ("login", "checkpoint", "challenge", "two_step_verification")):
        raise ManualInterventionRequired(
            "Facebook requires login or verification; run the login command manually"
        )
    if await page.locator("iframe[src*='captcha' i]").count():
        raise ManualInterventionRequired("CAPTCHA detected; automation stopped for manual handling")


async def _capture(page: Page, root: Path, profile_id: str, target: str) -> None:
    directory = root / profile_id
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    try:
        await page.screenshot(path=directory / f"{stamp}-{target}.png", full_page=False)
    except PlaywrightError:
        pass
