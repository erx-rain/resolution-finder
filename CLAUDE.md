# Resolution Finder

Scans RainTrade prediction markets and proposes, for a human reviewer,
(1) whether a resolution is available yet and (2) what the market resolves
to, with the evidence sentence and source. It never settles a market.

**Full context: `docs/HANDOFF.md`.** Read it before planning any change:
architecture, every module, the dataset, eval method, legal/source register,
strengths, weaknesses, roadmap, and the owner's preferences. This file is
only the essentials.

## Hard rules

1. **Wrong verdicts are the hard constraint.** Maximize correct and
   resolved, but never at the cost of more wrong. When unsure → `UNCLEAR`.
   Any new wrong verdict is investigated before anything else.
2. **Description beats title.** Read a market's full description before
   deciding its shape, threshold or source.
3. **Data purity.** Every field in `data/markets.json` and every test
   fixture comes verbatim from the source (API response or saved page).
   Never write, paraphrase, merge, fix or invent market text or ids.
4. **No generative LLM in the decision path; $0 marginal cost.** Local
   models only (MiniLM embeddings, DeBERTa-v3-xsmall NLI), regex, public data.
5. **Caching only inside the eval harness** (`run_eval.py` /
   `eval_cache.py`), never in the production pipeline.
6. **"Found nothing" is not "didn't happen."** Never emit a market's default
   outcome as a verdict from absence of evidence.
7. **Sources:** robots.txt is not legal clearance; read the ToS. Never fetch
   X/Instagram pages. Never bypass a block, WAF challenge or broken SSL cert.
   Verify any new domain live before whitelisting. See HANDOFF section 10.
8. **Keywords select candidates; NLI verifies; ≥ 2 independent domains
   corroborate.** Don't fix false positives by adding keywords.
9. Fix real bugs immediately (TDD). Log unsupported market *shapes* in
   `docs/superpowers/plans/2026-08-25-unsupported-market-types.md` instead of
   force-fitting them. Never overfit a fix to one event.
10. Prioritize **sports and politics**.

## Environment

The original machine runs Python only inside **WSL2 (Ubuntu)**: Windows 11
Smart App Control blocks unsigned native Python binaries (numpy), and the
owner keeps it on. Any normal Linux/macOS/Windows Python works if that
doesn't apply to you. Built on Python 3.14.4 (versions in HANDOFF section 3).

```bash
python3 -m venv .venv-wsl && .venv-wsl/bin/pip install -r requirements.txt
PYTHONPATH=. .venv-wsl/bin/python -m pytest tests/ -q      # 360 passed, ~3 min
```

From Windows: `wsl -- bash -c "cd '/mnt/c/.../resolution-finder' && PYTHONPATH=. .venv-wsl/bin/python ..."`.
Quote paths (they may contain spaces); pass many args via a small script;
use `python -u` when logging to a file.

## Commands

- Tests: `python -m pytest tests/ -q`
- Scan (live web → `data/resolution_finder.db`): `python run_scan.py`
- Dashboard: `flask --app 'resolution_finder.dashboard:create_app("data/resolution_finder.db")' run`
- Eval, replay cached evidence: `python run_eval.py [<market_id> ...]`
- Eval, refresh evidence: `python run_eval.py --live [<market_id> ...]`
- Pull markets (review file, never auto-merged): `python pull_test_batch.py "<query>" ...`

Measure every behavior change with `run_eval.py` before and after, on the
cached replay. Results append to `data/eval_history.jsonl` with the commit.

## Map

- `resolution_finder/pipeline.py` — per market: availability → structured
  resolver → peer check (disabled) → news path → save.
- `structured_resolvers.py` — official-source resolvers; a claimed market
  never falls through to news. Built: Fed decisions (`fed_decision.py`,
  `fed_pages.py`, `page_fetch.py`).
- `resolution_spec.py` — deterministic description parsing + availability.
- `evidence_retriever.py` (Bing RSS articles, Google RSS headlines/archive),
  `article_extractor.py` (robots.txt, curl_cffi, trafilatura),
  `relevance_ranker.py` (MiniLM ≥ 0.35).
- `verdict_engine.py` — `decide()` → binary / multi-outcome / date-threshold /
  numeric paths. 2,400 lines of calibrated guards; **read the comment on a
  guard before changing it** — each records the real wrong verdict it fixed.
- `storage.py` (SQLite), `dashboard.py` (Flask review queue).
- `data/markets.json` — 165 real resolved markets with `resolved_to` (eval set).

## Where the work is

Prioritized roadmap: HANDOFF section 13. Next up: Congress/Senate resolver
(`docs/superpowers/plans/2026-09-17-congress-resolver-handoff.md`, needs a
free api.data.gov key). Crypto/price markets are blocked on data licensing
(`2026-09-17-price-markets-handoff.md`).

## Working style the owner expects

- Plan/design on Opus, implement on Sonnet; say when to switch.
- Don't spawn subagents to save tokens; work inline.
- In an agreed test-find-fix loop, don't ask before each fix; summarize on
  request. Ask before anything outward-facing or hard to reverse.
