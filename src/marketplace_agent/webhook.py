from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime

import httpx

from marketplace_agent.config import Webhook
from marketplace_agent.state import State, StoredLead


async def deliver_leads(config: Webhook, state: State) -> int:
    if not config.enabled:
        return 0
    leads = state.undelivered_leads()
    if not leads:
        return 0
    secret = os.environ.get(config.secret_env)
    if not secret:
        raise ValueError(f"required environment variable is not set: {config.secret_env}")
    ids = [lead.id for lead in leads]
    event_id = hashlib.sha256(",".join(map(str, ids)).encode()).hexdigest()
    payload = {
        "schema_version": "1.0",
        "event": "marketplace.leads",
        "event_id": event_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "leads": [_payload_lead(lead) for lead in leads],
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": event_id,
        "X-Marketplace-Event": event_id,
        "X-Marketplace-Signature-256": f"sha256={signature}",
    }
    try:
        await _post_with_retries(config, body, headers)
    except Exception as exc:
        state.mark_lead_delivery_failed(ids, str(exc))
        raise
    state.mark_leads_delivered(ids)
    return len(leads)


def _payload_lead(stored: StoredLead) -> dict[str, str | int | None]:
    lead = stored.lead
    return {
        "id": stored.id,
        "profile_id": lead.profile_id,
        "source": lead.source,
        "source_url": lead.source_url,
        "source_key": lead.source_key,
        "phone": lead.phone,
        "sender": lead.sender,
        "message": lead.message,
        "observed_at": lead.observed_at.isoformat(),
    }


async def _post_with_retries(config: Webhook, body: bytes, headers: dict[str, str]) -> None:
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        for attempt in range(config.max_attempts):
            try:
                response = await client.post(config.url, content=body, headers=headers)
                if response.status_code < 400:
                    return
                error = RuntimeError(f"webhook returned HTTP {response.status_code}")
                if response.status_code < 500 and response.status_code != 429:
                    raise error
                last_error = error
            except (httpx.HTTPError, RuntimeError) as exc:
                last_error = exc
                if isinstance(exc, RuntimeError) and "HTTP 4" in str(exc) and "HTTP 429" not in str(exc):
                    raise
            if attempt + 1 < config.max_attempts:
                await asyncio.sleep(2**attempt)
    raise RuntimeError(f"webhook delivery failed after {config.max_attempts} attempts: {last_error}")
