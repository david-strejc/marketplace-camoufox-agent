# Marketplace Camoufox Agent

A Python 3.11+ reference implementation for profile-isolated Facebook Marketplace listing workflows, consented lead intake, signed JSON webhooks, and tightly scoped AI-assisted selector repair. It uses [Camoufox](https://camoufox.com/) with the Playwright API instead of Dolphin Anty.

> **Status:** the orchestration, storage, workbook processing, image pipeline and webhook are implemented. Facebook's live DOM and posting behavior must be calibrated and accepted on your own accounts before production. This repository does not claim a successful live Facebook end-to-end run.

## Important boundaries

Use this software only where you have authorization and where automation is permitted by Meta's current terms, Marketplace rules and applicable privacy law. The operator is responsible for that assessment. This project does not bypass CAPTCHA, checkpoints, login verification or platform restrictions: it stops for manual handling. Lead intake is for contact details people explicitly share in configured Messenger conversations or group-post comments, not indiscriminate profile scraping. No unsolicited auto-replies, fake engagement, account warm-up or automatic group joining are included.

The default is `live: false`. Real browser publishing or collection requires both `runtime.live: true` and `--live`.

## What is included

- Seven-profile example: one direct connection and six static proxy endpoints.
- A separate persistent Camoufox profile directory and process lock per identity.
- Deterministic prime-time scheduling: three unique listings per profile per day by default, with a configurable minimum gap and bounded concurrency.
- Global listing-id deduplication, durable SQLite state, crash recovery, and an `unknown` state that prevents blind reposting after an ambiguous publish attempt.
- Excel input for listings, allowed cities and response templates. Two existing photos are required per listing.
- A Pillow image command that normalizes orientation, strips EXIF and other source metadata, and writes clean JPEGs without overwriting existing output.
- Phone validation and E.164 normalization, source-level lead deduplication, CSV export, and optional raw-message storage (off by default).
- Retried JSON webhook delivery with HMAC-SHA256 and stable idempotency keys.
- A constrained AI agent for selector proposals and weekly reliability recommendations. It receives a redacted interactive-element inventory or aggregate metrics, not cookies, passwords, input values or full page HTML.
- Screenshots on selector failures, explicit CAPTCHA/checkpoint stops, manual session bootstrap, and no credential storage in YAML.

## Architecture

```text
Excel + two photos ──> validation ──> daily planner ──> SQLite job queue
                                             │
                                             v
                              Camoufox persistent profile
                              (one proxy / one identity)
                                             │
                              dry-run or guarded publication
                                             v
                           published / failed / unknown state

Messenger + configured group posts ──> explicit phone extraction
                                      ──> deduplicated SQLite leads
                                      ──> HMAC JSON webhook and CSV

Selector failure ──> redacted controls ──> structured LLM proposal
                   ──> browser validation ──> operator-reviewed YAML change
```

The AI cannot click arbitrarily, alter configuration, publish content or expand its own permissions. A proposed selector is used only after it resolves to exactly one visible/attached element in the current page and is stored as a proposal for review. Regular runs still prefer deterministic role/label/CSS selectors.

## Why this agent design

The research compared full autonomous browser agents with deterministic Playwright flows. General agents are useful for exploration but slower and less predictable for repeated business transactions. This project therefore uses the hybrid pattern popularized by tools such as [Stagehand](https://github.com/browserbase/stagehand): deterministic actions on the happy path, structured model assistance on failures, and validation before acting. The model API follows [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs).

Camoufox is used through its documented asynchronous `AsyncCamoufox` interface, `persistent_context=True`, `user_data_dir`, per-profile proxy and GeoIP support. See the official [Camoufox usage documentation](https://camoufox.com/python/usage/) and [Playwright persistent-context documentation](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context). Camoufox itself warns that it is under development and may not be suitable for stable production use; selector and browser upgrades need a staging run.

## Installation on Windows

Install Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). In PowerShell:

```powershell
git clone https://github.com/david-strejc/marketplace-camoufox-agent.git
cd marketplace-camoufox-agent
uv sync --all-extras
uv run camoufox fetch
Copy-Item config.example.yaml config.yaml
```

Do not use your normal Firefox profile. The application creates dedicated directories under `profiles/`.

## Workbook contract

Create `data/listings.xlsx` with three sheets. Paths are resolved relative to the workbook unless absolute.

### `listings`

| Column | Required | Meaning |
|---|---:|---|
| `id` | yes | Stable, globally unique listing identifier |
| `title` | yes | Listing title |
| `description` | yes | Listing body |
| `price` | yes | Positive decimal number |
| `city` | yes | Must match the `cities` sheet when that sheet is present |
| `category` | no | Overrides `default_category` |
| `photo_1` | yes | First prepared image path |
| `photo_2` | yes | Second prepared image path |

Empty rows are ignored. Duplicate IDs, missing images, unknown cities and invalid prices stop validation before a browser starts. A listing ID is allocated to only one profile, preventing accidental reuse across all seven accounts.

### `cities`

One `city` column. This is an allowlist for the listing city field.

### `responses`

Two columns: `key` and `text`. Keys must be unique and texts non-empty. These templates are validated and available as workbook input, but the application deliberately does not send automatic Messenger replies.

## Images

Convert a source tree while preserving its folder structure:

```powershell
uv run marketplace-agent --config config.yaml prepare-images `
  --source "D:\Marketplace\originals" --output "D:\Marketplace\clean"
```

The command accepts common image formats, applies EXIF orientation, creates a fresh RGB canvas and saves JPEG without source EXIF. Existing output files are skipped rather than overwritten. Source and output directories must differ. Reference exactly two cleaned files for each workbook row.

## Profiles and login

Edit `config.yaml`. Replace the six loopback proxy examples with your static CZ endpoints. Authenticated proxies use environment interpolation:

```yaml
proxy:
  server: "${FB_PROXY_CZ_01_SERVER}"
  username: "${FB_PROXY_CZ_01_USERNAME}"
  password: "${FB_PROXY_CZ_01_PASSWORD}"
```

Only HTTP, HTTPS and SOCKS5 proxy endpoints are accepted. GeoIP is enabled when a proxy is configured. The direct `home` profile has no proxy.

Log in once per profile with a visible browser:

```powershell
$env:FB_PROXY_CZ_01_SERVER="http://proxy-host:port"
$env:FB_PROXY_CZ_01_USERNAME="..."
$env:FB_PROXY_CZ_01_PASSWORD="..."
uv run marketplace-agent --config config.yaml login --profile home
uv run marketplace-agent --config config.yaml login --profile cz-01
```

Complete any Facebook verification manually, return to PowerShell and press Enter. Cookies remain in the profile directory. Credentials, cookies and session databases are gitignored. Never share that directory.

## Selector calibration

Facebook changes labels and DOM by account, language and rollout. Start with the supplied Czech and English role selectors, then calibrate them in a dry run. Selector strategies are `role` (optionally with `name`), `css`, `text`, `label`, and `placeholder`. Each entry uses exactly one strategy. A publishing selector must produce exactly one matching element.

For collection, add `messenger_threads`, `messenger_messages`, `group_items` and optionally sender selectors to the YAML. **The Messenger message selector must select incoming messages only.** Group URLs belong on individual profiles via `group_post_urls`. Keep collection disabled until this is reviewed. The collector reads loaded items; it does not auto-join groups or crawl unrelated profiles.

## Dry run

Validate configuration and content:

```powershell
uv run marketplace-agent --config config.yaml validate --content
uv run marketplace-agent --config config.yaml plan
uv run marketplace-agent --config config.yaml run
```

`plan` allocates listings into deterministic slots. `run` without `--live` opens due jobs and fills the form but does not click Publish. Dry-run jobs remain scheduled so the same jobs can later run live. If no slot is currently due, the summary contains zero jobs; use `plan` to inspect their timestamps.

## Live operation

After an accepted dry run, set `runtime.live: true` in `config.yaml`, then:

```powershell
uv run marketplace-agent --config config.yaml run --live
uv run marketplace-agent --config config.yaml run --live --watch
```

The first command processes due jobs once. The second keeps planning and processing, and is suitable for a supervised Windows Task Scheduler job. Concurrency defaults to two browsers. Do not run two application instances against the same profile; the lock prevents overlapping browser sessions but an external scheduler should still avoid overlapping runs.

Job states:

- `scheduled`: waiting for its slot.
- `running`: claimed by the current process.
- `published`: Facebook redirected to a recognized confirmation URL.
- `failed`: the run failed before confirmation; inspect it before retrying.
- `unknown`: Publish was clicked but confirmation timed out. **Inspect Facebook manually. Never blind-retry this state.**

After verifying that a failed job did not publish:

```powershell
uv run marketplace-agent --config config.yaml retry-job 42
```

Interrupted `running` jobs become `failed` on the next live start, with an instruction to inspect before retrying.

## Lead collection, webhook and CSV

Enable collection only after calibrating its selectors and confirming your legal basis:

```yaml
collection:
  enabled: true
  store_message_text: false
webhook:
  enabled: true
  url: "https://your-endpoint.example/marketplace/leads"
  secret_env: WEBHOOK_SECRET
```

```powershell
$env:WEBHOOK_SECRET="use-a-secret-manager-in-production"
uv run marketplace-agent --config config.yaml collect --live
uv run marketplace-agent --config config.yaml export --output data\leads.csv
```

The collector validates numbers with `phonenumbers` using the configured region, stores E.164 format, and deduplicates by profile, source, source item and number. By default it stores no message body. The CSV contains the number, sender, source URL, timestamps and delivery status.

Webhook request properties:

- `POST` with UTF-8 JSON and `Content-Type: application/json`.
- Event name `marketplace.leads`, schema version `1.0`, and a `leads` array.
- `Idempotency-Key` and `X-Marketplace-Event` contain the stable event ID.
- `X-Marketplace-Signature-256` is `sha256=` plus hex HMAC-SHA256 of the exact raw request body using `WEBHOOK_SECRET`.
- HTTP 429, 5xx and network failures use exponential retries. Other 4xx responses fail immediately. Undelivered leads remain queued.

Example payload:

```json
{
  "schema_version": "1.0",
  "event": "marketplace.leads",
  "event_id": "…",
  "generated_at": "2026-01-01T12:00:00+00:00",
  "leads": [{
    "id": 1,
    "profile_id": "home",
    "source": "messenger",
    "source_url": "https://www.facebook.com/messages/t/…",
    "source_key": "…",
    "phone": "+420777111222",
    "sender": "Example sender",
    "message": null,
    "observed_at": "2026-01-01T11:59:00+00:00"
  }]
}
```

A receiver must verify the signature with a constant-time comparison and deduplicate the event ID before inserting rows. Treat the exported CSV and webhook payload as personal data.

## AI selector repair and weekly review

Set an OpenAI-compatible Responses API endpoint and key, then enable the agent:

```yaml
agent:
  enabled: true
  base_url: https://api.openai.com/v1
  api_key_env: OPENAI_API_KEY
  model: gpt-4o-mini
```

```powershell
$env:OPENAI_API_KEY="..."
uv run marketplace-agent --config config.yaml run
uv run marketplace-agent --config config.yaml report --ai
```

On a missing selector, the agent receives only tag names and allowlisted attributes from interactive controls. Input and textarea values are excluded. It returns schema-validated CSS candidates. A candidate must resolve to one element before use. Accepted candidates are recorded in `selector_proposals`; review `report` and manually promote a resilient rule to YAML. No full DOM, cookies, proxy credentials, phone lists or message bodies are sent by the selector agent.

`report --ai` sends aggregate job/lead counts, top error strings and selector proposals for operational recommendations. Review error strings for sensitive page text before using this optional mode. AI output is advisory and cannot rewrite schedules or configuration.

For a weekly review, run `report --ai` from Windows Task Scheduler on a separate schedule. This is deliberately a review step, not autonomous production optimization.

## Command summary

```text
validate [--content]       Validate YAML, profiles and optionally Excel/photos
login --profile ID         Manual login and persistent-session bootstrap
prepare-images             Strip metadata and normalize images
plan [--date YYYY-MM-DD]   Allocate unique listings to prime-time slots
run [--live] [--watch]     Dry-run or publish due jobs
collect --live             Collect consented contact numbers and deliver webhook
export --output PATH       Export all stored leads to CSV
retry-job ID               Reschedule a verified failed job
report [--ai]              Metrics, selector proposals, optional AI review
```

The global `--config PATH` option precedes the command, as shown in the examples.

## Operations and maintenance

- Pin and stage Camoufox/browser updates. Re-run workbook validation and one supervised dry run after each browser or selector change.
- Keep `data/`, `profiles/` and `artifacts/` on an encrypted disk with restricted ACLs and a retention policy.
- Back up the SQLite database and workbook without putting session cookies in backups you do not need.
- Check screenshots before sharing them; they may contain account or personal data.
- Use one stable static endpoint per profile. Do not rotate identities to bypass a restriction.
- A challenge, CAPTCHA or checkpoint is a stop signal, not a retry opportunity.
- Keep the provided daily cap conservative; changing configuration does not make otherwise prohibited automation permitted.

## Verification and honest limitations

GitHub Actions installs the package, runs Ruff and strict mypy, builds the distribution and validates the example configuration. Live Facebook behavior cannot be tested in CI without accounts and would be unsafe to claim from static checks. Before relying on the system, perform a manual-login dry run for each profile, inspect the completed form, approve one live listing, test a webhook receiver signature/idempotency implementation, and verify lead deduplication on your own consented sources.

Known variables include Facebook A/B layouts, locale-specific labels, session expiry, proxy quality, and Marketplace category-specific fields. Category-specific required fields beyond title, price, description, category, city and photos may require a small adapter extension. There is no CAPTCHA solver, auto-warm-up, automatic group recruitment or bulk messaging.

## License

MIT. See `LICENSE`. Camoufox, Playwright and other dependencies retain their own licenses. Facebook and Meta are trademarks of their respective owners; this project is unaffiliated with Meta.
