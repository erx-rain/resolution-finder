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

In the team's own gist (`rtap_history_2026.json`), `description` is only
source metadata (source URL/venue/category) — it carries no resolution
rules. `GET /markets` and `POST /eligibility` still read straight from
the gist, unchanged. A **scan** (`POST /scans`, manual or scheduled)
reads the real rules from Rain's own pool record instead (owner decision
2026-10-13, `resolution_finder/rain_pool_client.py`):
`GET https://prod2-api.rain.one/pools/pool/<id>` (public, no token) —
`poolDescription` becomes the market's description, and 2+ `subPools`
become multi-outcome options (exactly 1 `subPool` is Rain's own
binary-market shape, not a real option). If Rain has no description for
that id, **the scan is not run**: the run and its job end `failed` with
the reason, never a guessed verdict against the gist's placeholder text.

**A run that ends without a finding is always `failed`, with the reason
as its error** (2026-10-13) — no rules on Rain, everything already
resolved on Rain, or a scan that ran and saved nothing. `done` means at
least one finding was written.

**The market list hides what Rain has resolved** (2026-10-13). On every
`GET /markets` the service reads Rain's public pool listing once (one
call, every pool with its sub-pools' settlement; reading each market's
own record instead took 16–42 s, past rain-admin's proxy timeout) and:

- drops a market whose **every** sub-pool Rain has settled
  (`poolFinalized` or a recorded `winnerOption`) — for a Yes/No market,
  its one sub-pool;
- adds to each listed market `sub_pools: [{name, resolved}]`, Rain's
  breakdown, and `rain_status_known` (false when the market was not in
  the listing: nothing is known, and it is shown, not dropped);
- if the listing cannot be read, shows every gist market with status
  unknown and sets `rain_listing_error` at the top level. Nothing is ever
  dropped as "resolved" on no information.

The scheduler's own repeating scan uses the same list. `POST /eligibility`
with an empty body still covers every gist market.

What a scan covers is also read from Rain's pool record (2026-10-13,
`rain_pool_client.plan_scan`):

- **Sub-pools Rain has already resolved are not scanned** (`poolFinalized`
  or a recorded `winnerOption` on the sub-pool). If none is left, the run
  fails with "every sub-pool of this market is already resolved on Rain".
  The pool's own `isFinalized` is ignored: it is true on Live markets
  with nothing settled.
- **Sub-pools named by dates** ("August 18", "December 31") are
  thresholds of one event, not rivals. Each open one is scanned as its
  own Yes/No market: Rain's `mergedQuestion` as the title, the sub-pool's
  own `endDate` as its deadline. Its finding is filed under the sub-pool's
  name.
- Any other set of sub-pools is one contest whose options are the open
  sub-pools' names.

A date-named sub-pool is the one shape that may be proposed `NO` because
nothing was found (owner decision 2026-10-13): its own deadline has
passed, the rules say "otherwise No", coverage dated after the deadline
was read from two or more sources, and no sentence in it reads as a
report that the event happened. The finding says in words that it rests
on absence, and carries 0 confidence. If something does read as such a
report, the finding is `UNCLEAR` and quotes it.

## API

All routes except `/health` require `Authorization: Bearer $RF_SERVICE_TOKEN`
when the token is set.

| Method & path | What it does |
|---|---|
| `GET /health` | Liveness probe (no auth). |
| `GET /markets` | The gist market list, minus markets Rain has resolved, each with Rain's per-sub-pool status (see below). |
| `POST /eligibility` | Date-based availability. Empty body = all gist markets; `{"markets":[...]}` = just those. Fast, no models. |
| `POST /scans` `{"market_id": "..."}` | Enqueue a full scan (202 + job). Deduped per market. Manual scans run regardless of eligibility. The job ends `failed`, with the reason, whenever it produces no finding — see "Gist market file" above. |
| `GET /scans` / `GET /scans/<job_id>` | Job list / status (`queued → running → done|failed`). |
| `GET /findings` | The findings of each market's most recent run that produced any. |
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

A market with listed options (Rain sub-pools) gets **one finding per
option on every run** (2026-10-13) — each sub-pool is its own Yes/No
question. An option nothing was found about is `NO_EVIDENCE`, never left
out and never guessed. Findings carry the option as both `option` and
`option_name` (the name rain-admin reads).

Two numbers on a finding, never to be confused (2026-10-13):

- `confidence` — how sure the service is that the proposed outcome (YES
  or NO, for that option) is correct, 0 to 1, **or `null`**. It is `null`
  on every finding today: the engine does not compute such a figure, and
  no default, similarity or constant is sent in its place. `UNCLEAR` and
  `NO_EVIDENCE` propose nothing and are always `null`.
- `evidence_match` — how closely the quoted article matches the market's
  own text (embedding similarity, 0 to 1), or `null` when no matched
  article is quoted. Options settled by the same article share it. This
  is the number that used to be served as `confidence`.

Findings stored before this change are served unchanged (the old number
still under `confidence`, `evidence_match` null).

A finding whose resolution is not yet available carries `available_from`:
the date it becomes determinable (the sub-pool's own deadline for a
date-named sub-pool, the market's close date otherwise).

When a market's own rules say non-winning options resolve "No" and two
independent sources state, with the market's own year, that someone
**not listed** won, every listed option is proposed `NO` (the evidence
names who won and that they are not listed). Anything short of that —
one source, no year in the sentence, a listed option with a winner claim
of its own — leaves the options unresolved.

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
