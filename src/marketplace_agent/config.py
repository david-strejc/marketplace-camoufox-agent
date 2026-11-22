from __future__ import annotations

import os
import re
from datetime import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_PUBLISH_TARGETS = {
    "title",
    "price",
    "description",
    "category",
    "city",
    "photos",
    "next",
    "publish",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Proxy(StrictModel):
    server: str
    username: SecretStr | None = None
    password: SecretStr | None = None

    @field_validator("server")
    @classmethod
    def valid_server(cls, value: str) -> str:
        parsed = urlparse(value if "://" in value else f"http://{value}")
        if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname:
            raise ValueError("proxy must use http, https or socks5 and include a host")
        if parsed.username or parsed.password:
            raise ValueError("put proxy credentials in username/password, not in server")
        return value

    @model_validator(mode="after")
    def credentials_together(self) -> Proxy:
        if bool(self.username) != bool(self.password):
            raise ValueError("proxy username and password must be configured together")
        return self

    def playwright(self) -> dict[str, str]:
        result = {"server": self.server}
        if self.username and self.password:
            result["username"] = self.username.get_secret_value()
            result["password"] = self.password.get_secret_value()
        return result


class Profile(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,30}$")
    enabled: bool = True
    proxy: Proxy | None = None
    group_post_urls: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("group_post_urls")
    @classmethod
    def facebook_urls(cls, values: list[str]) -> list[str]:
        for value in values:
            parsed = urlparse(value)
            if parsed.scheme != "https" or parsed.hostname not in {
                "facebook.com",
                "www.facebook.com",
                "m.facebook.com",
            }:
                raise ValueError("group posts must use HTTPS Facebook URLs")
        return values


class PrimeWindow(StrictModel):
    start: time
    end: time

    @model_validator(mode="after")
    def ordered(self) -> PrimeWindow:
        if self.end <= self.start:
            raise ValueError("prime-time window end must be after start")
        return self


class Runtime(StrictModel):
    live: bool = False
    timezone: str = "Europe/Prague"
    locale: str = "cs-CZ"
    operating_system: Literal["windows", "macos", "linux"] = "windows"
    headless: bool = False
    daily_limit_per_profile: int = Field(default=3, ge=1, le=10)
    minimum_gap_minutes: int = Field(default=45, ge=0, le=240)
    max_concurrency: int = Field(default=2, ge=1, le=7)
    navigation_timeout_ms: int = Field(default=45_000, ge=5_000)
    action_timeout_ms: int = Field(default=15_000, ge=2_000)
    prime_time: list[PrimeWindow] = Field(default_factory=list)
    database_path: Path = Path("data/marketplace.db")
    profiles_dir: Path = Path("profiles")
    artifacts_dir: Path = Path("artifacts")

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

class Content(StrictModel):
    workbook: Path = Path("data/listings.xlsx")
    listings_sheet: str = "listings"
    cities_sheet: str = "cities"
    responses_sheet: str = "responses"
    default_category: str = "Furniture"
    phone_region: str = "CZ"


class Selector(StrictModel):
    role: str | None = None
    name: str | None = None
    css: str | None = None
    text: str | None = None
    label: str | None = None
    placeholder: str | None = None
    exact: bool = False

    @model_validator(mode="after")
    def one_strategy(self) -> Selector:
        strategies = [self.role, self.css, self.text, self.label, self.placeholder]
        if sum(value is not None for value in strategies) != 1:
            raise ValueError("selector needs exactly one of role, css, text, label, placeholder")
        if self.role is None and self.name is not None:
            raise ValueError("name is only valid with a role selector")
        return self


class Facebook(StrictModel):
    create_item_url: str = "https://www.facebook.com/marketplace/create/item"
    messenger_url: str = "https://www.facebook.com/messages/t"
    selectors: dict[str, list[Selector]]

    @model_validator(mode="after")
    def required_selectors(self) -> Facebook:
        missing = _PUBLISH_TARGETS - self.selectors.keys()
        if missing:
            raise ValueError(f"missing Marketplace selectors: {', '.join(sorted(missing))}")
        for target, selectors in self.selectors.items():
            if not selectors:
                raise ValueError(f"selector list cannot be empty: {target}")
        for url in (self.create_item_url, self.messenger_url):
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in {
                "facebook.com",
                "www.facebook.com",
                "m.facebook.com",
            }:
                raise ValueError("Facebook endpoints must use HTTPS Facebook URLs")
        return self


class Collection(StrictModel):
    enabled: bool = False
    max_threads_per_profile: int = Field(default=50, ge=1, le=200)
    max_items_per_surface: int = Field(default=100, ge=1, le=500)
    store_message_text: bool = False


class Webhook(StrictModel):
    enabled: bool = False
    url: str = "https://example.com/marketplace/leads"
    secret_env: str = Field(default="WEBHOOK_SECRET")
    timeout_seconds: float = Field(default=15, gt=0, le=120)
    max_attempts: int = Field(default=4, ge=1, le=10)

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        parsed = urlparse(value)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise ValueError("webhook must use HTTPS (HTTP is allowed only for localhost)")
        if parsed.username or parsed.password:
            raise ValueError("webhook credentials must not be embedded in the URL")
        return value


class Agent(StrictModel):
    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    model: str = "gpt-4o-mini"
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_candidates: int = Field(default=3, ge=1, le=5)

    @field_validator("base_url")
    @classmethod
    def https_api(cls, value: str) -> str:
        parsed = urlparse(value)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise ValueError("agent API must use HTTPS (HTTP is allowed only for localhost)")
        return value.rstrip("/")


class Config(StrictModel):
    runtime: Runtime
    content: Content
    facebook: Facebook
    collection: Collection = Field(default_factory=Collection)
    webhook: Webhook = Field(default_factory=Webhook)
    agent: Agent = Field(default_factory=Agent)
    profiles: list[Profile] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_profiles(self) -> Config:
        ids = [profile.id for profile in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("profile ids must be unique")
        return self

    def profile(self, profile_id: str) -> Profile:
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        raise KeyError(f"unknown profile: {profile_id}")

    def resolve_paths(self, base_dir: Path) -> None:
        for name in ("database_path", "profiles_dir", "artifacts_dir"):
            path = getattr(self.runtime, name)
            if not path.is_absolute():
                setattr(self.runtime, name, (base_dir / path).resolve())
        if not self.content.workbook.is_absolute():
            self.content.workbook = (base_dir / self.content.workbook).resolve()


def _expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in os.environ:
                raise ValueError(f"required environment variable is not set: {name}")
            return os.environ[name]

        return _ENV.sub(replace, value)
    return value


def load_config(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a mapping")
    config = Config.model_validate(_expand_env(raw))
    config.resolve_paths(path.resolve().parent)
    if config.webhook.enabled and not os.environ.get(config.webhook.secret_env):
        raise ValueError(f"required environment variable is not set: {config.webhook.secret_env}")
    return config
