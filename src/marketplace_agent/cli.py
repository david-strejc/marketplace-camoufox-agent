from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from marketplace_agent.agent import SelectorAgent
from marketplace_agent.browser import BrowserError, collect_profile_leads, login
from marketplace_agent.config import Config, Profile, load_config
from marketplace_agent.content import load_content, strip_image_metadata
from marketplace_agent.scheduler import plan_day, run_once, run_watch
from marketplace_agent.state import State
from marketplace_agent.webhook import deliver_leads

logger = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marketplace-agent",
        description="Camoufox Marketplace publishing and consented lead intake",
    )
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate YAML and optionally the workbook")
    validate.add_argument("--content", action="store_true")

    login_parser = subparsers.add_parser("login", help="save a manual login in one profile")
    login_parser.add_argument("--profile", required=True)

    prepare = subparsers.add_parser("prepare-images", help="remove EXIF and convert images to JPEG")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)

    plan = subparsers.add_parser("plan", help="allocate unique listings into prime-time slots")
    plan.add_argument("--date", help="local date in YYYY-MM-DD format; defaults to today")

    run = subparsers.add_parser("run", help="run due jobs once, or watch in live mode")
    run.add_argument("--live", action="store_true", help="allow real publishing")
    run.add_argument("--watch", action="store_true", help="keep scheduling; live mode only")

    collect = subparsers.add_parser("collect", help="collect explicitly shared phone numbers")
    collect.add_argument("--profile", help="one profile; defaults to all enabled profiles")
    collect.add_argument("--live", action="store_true", help="allow browser access and webhook delivery")

    export = subparsers.add_parser("export", help="export stored leads to CSV")
    export.add_argument("--output", type=Path, default=Path("data/leads.csv"))

    report = subparsers.add_parser("report", help="show aggregate metrics and selector proposals")
    report.add_argument("--ai", action="store_true", help="add AI reliability recommendations")

    retry = subparsers.add_parser("retry-job", help="retry a job known to have failed before publishing")
    retry.add_argument("job_id", type=int)
    return parser


def _live(config: Config, requested: bool) -> bool:
    if requested and not config.runtime.live:
        raise ValueError("--live also requires runtime.live: true in the selected config")
    return requested


def _selected_profiles(config: Config, profile_id: str | None) -> list[Profile]:
    if profile_id:
        profile = config.profile(profile_id)
        if not profile.enabled:
            raise ValueError(f"profile is disabled: {profile.id}")
        return [profile]
    return [profile for profile in config.profiles if profile.enabled]


async def _collect(config: Config, state: State, profile_id: str | None) -> dict[str, int]:
    if not config.collection.enabled:
        raise ValueError("lead collection is disabled; set collection.enabled: true first")
    semaphore = asyncio.Semaphore(config.runtime.max_concurrency)

    async def collect_one(profile: Profile) -> int:
        async with semaphore:
            leads = await collect_profile_leads(config, profile)
            return state.add_leads(leads)

    inserted = sum(
        await asyncio.gather(*(collect_one(profile) for profile in _selected_profiles(config, profile_id)))
    )
    delivered = await deliver_leads(config.webhook, state)
    return {"new_leads": inserted, "delivered": delivered}


async def _dispatch(args: argparse.Namespace, config: Config) -> Any:
    if args.command == "validate":
        result: dict[str, Any] = {
            "valid": True,
            "profiles": len(config.profiles),
            "enabled_profiles": sum(profile.enabled for profile in config.profiles),
            "live": config.runtime.live,
        }
        if args.content:
            content = load_content(config.content)
            result.update(
                listings=len(content.listings),
                cities=len(content.cities),
                response_templates=len(content.responses),
            )
        return result
    if args.command == "prepare-images":
        converted, skipped = strip_image_metadata(args.source, args.output)
        return {"converted": converted, "skipped": skipped}
    if args.command == "login":
        await login(config, config.profile(args.profile))
        return {"profile": args.profile, "session": "saved"}
    if args.command == "export":
        output = args.output.resolve()
        with State(config.runtime.database_path) as state:
            count = state.export_leads(output)
        return {"exported": count, "output": str(output)}
    if args.command == "retry-job":
        with State(config.runtime.database_path) as state:
            state.retry_job(args.job_id)
        return {"job": args.job_id, "status": "scheduled"}
    if args.command == "report":
        with State(config.runtime.database_path) as state:
            metrics = state.metrics()
        if args.ai:
            if not config.agent.enabled:
                raise ValueError("AI reporting requires agent.enabled: true")
            recommendation = await SelectorAgent(config.agent).recommend(metrics)
            metrics["ai"] = recommendation.model_dump()
        return metrics

    content = load_content(config.content)
    with State(config.runtime.database_path) as state:
        if args.command == "plan":
            zone = ZoneInfo(config.runtime.timezone)
            day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now(zone).date()
            created = plan_day(config, state, content.listings, day)
            start = datetime.combine(day, datetime.min.time(), zone)
            end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
            return {"created": created, "jobs": state.jobs_for_day(start, end)}
        if args.command == "run":
            live = _live(config, live_requested=args.live)
            if args.watch:
                if not live:
                    raise ValueError("--watch requires --live and runtime.live: true")
                state.recover_interrupted()
                await run_watch(config, state, content.listings)
                return {}
            zone = ZoneInfo(config.runtime.timezone)
            plan_day(config, state, content.listings, datetime.now(zone).date())
            if live:
                state.recover_interrupted()
            return await run_once(config, state, content.listings, live)
        if args.command == "collect":
            if not _live(config, args.live):
                raise ValueError("collection requires --live and runtime.live: true")
            return await _collect(config, state, args.profile)
    raise ValueError(f"unsupported command: {args.command}")


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    args = _parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config(args.config)
        result = asyncio.run(_dispatch(args, config))
        if result:
            _print_json(result)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (ValueError, KeyError, BrowserError) as exc:
        logger.error("%s", exc)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
