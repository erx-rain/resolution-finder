# Resolution Finder — Standalone Service

HTTP wrapper around the existing engine, built to run on a VM as the
backend for rain-admin's Resolution Finder screen. The engine proposes;
a human confirms or rejects; **nothing here settles a market**.

Owner decisions this implements (2026-10-06):
- Markets + their authoritative full descriptions come from the **team
  gist** (`GistMarketProvider`). Descriptions are passed verbatim.
- Scan scheduling is a **UI-settable tag** (e.g. `everyday at 9:00`),
  stored in the service DB and editable via `PUT /schedule`.
- Standalone deployment on a **VM** — not Vercel, not the admin app.
- Which markets to run against is decided later: the service scans
  whatever the configured gist file lists, so scoping = editing the gist.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
RF_GIST_ID=<gist id> \
RF_GIST_FILE=rf_markets.json \
RF_GITHUB_TOKEN=<token with gist read> \
RF_SERVICE_TOKEN=<random bearer secret> \
.venv/bin/python run_service.py
```

Defaults: `127.0.0.1:8700` (override `RF_HOST`/`RF_PORT`), DB at
`data/resolution_finder.db` (override `RF_DB_PATH`). First scan downloads
the two local models (~250 MB) into `~/.cache/huggingface`.

**Security:** without `RF_SERVICE_TOKEN` the API is unauthenticated —
acceptable only on loopback. Rain-admin must reach this service through
a server-side proxy route holding the token (same pattern as
`app/api/monitor/[...path]`); the token never reaches a browser.

## Gist market file

`RF_GIST_FILE` must contain either a JSON **list** of markets
(`data/markets.json` shape) or an **object keyed by market id**:

```json
[
  {
    "id": "pool-_id-or-slug",
    "title": "Will X happen?",
    "description": "FULL resolution rules, verbatim. This is the spec.",
    "options": [],
    "close_date": "2026-06-30"
  }
]
```

Field aliases accepted: `question` for `title`, `endDate`/`closeDate`
for `close_date`, option objects with `optionName`. A missing
`close_date` stays `null` (never guessed). `options: []` = binary.

## API

All routes except `/health` require `Authorization: Bearer $RF_SERVICE_TOKEN`
when the token is set.

| Method & path | What it does |
|---|---|
| `GET /health` | Liveness probe (no auth). |
| `GET /markets` | The gist market list, as the engine sees it. |
| `POST /eligibility` | Date-based availability. Empty body = all gist markets; `{"markets":[...]}` = just those. Fast, no models. |
| `POST /scans` `{"market_id": "..."}` | Enqueue a full scan (202 + job). Deduped per market. Manual scans run regardless of eligibility. |
| `GET /scans` / `GET /scans/<job_id>` | Job list / status (`queued → running → done|failed`). |
| `GET /findings` | Latest finding per (market, option). |
| `GET /markets/<id>/findings` | Full findings history + run log for one market. |
| `POST /findings/<id>/review` `{"decision":"confirm"\|"reject","by":"<admin>"}` | Record the human verdict review, with who and when. |
| `GET /schedule` / `PUT /schedule` `{"tag":"everyday at 9:00"}` | Read / set the scan schedule tag. Bad tags get 400 + supported forms. |

Schedule tag grammar: `everyday at 9:00`, `daily at 21:15`,
`everyday at 9am`, `every 6 hours`, `every 90 minutes` (min 15), `off`.
Scheduled runs scan **only markets whose resolution is available**
(date signals); manual `POST /scans` is the human override.

Outcomes: `YES` / `NO` / per-option verdicts are proposals with evidence;
`UNCLEAR` and `NO_EVIDENCE` are valid "no answer yet" states for the
reviewer — never errors, and never auto-converted to a default outcome.

## VM deployment (systemd)

```ini
# /etc/systemd/system/resolution-finder.service
[Unit]
Description=Resolution Finder service
After=network-online.target

[Service]
User=rf
WorkingDirectory=/opt/resolution-finder
EnvironmentFile=/opt/resolution-finder/.env
ExecStart=/opt/resolution-finder/.venv/bin/python run_service.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Sizing: 2 vCPU / 4 GB RAM / 5 GB disk. Needs outbound internet (news
retrieval + first-run model download). Keep the port loopback/firewalled;
expose via reverse proxy with TLS if rain-admin's proxy is remote.

## What this deliberately does NOT do

- No generative LLM in the decision path; no paid APIs (engine unchanged).
- No caching of scan evidence in production.
- No auto-settlement: `Confirmed` here only feeds the admin UI; signing
  stays in rain-admin's existing Resolution Center.
- No market-text rewriting anywhere between the gist and the engine.
