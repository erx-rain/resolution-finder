# Resolution Finder — Complete Handoff

Written 2026-09-17 at the end of the original build (Aug 10 – Sep 17, 2026),
for the next engineer **and their Claude**. It is meant to be the single
source of context: what this is, why it's built the way it is, how every
part works, how to run and measure it, what's strong, what's weak, and
what's next. Where a topic has a deeper document, this file links to it.

`CLAUDE.md` at the repo root is the short, always-loaded version. This file
is the full reference; read it once end to end before planning any change.

---

## 0. How to use this document (for Claude)

- **Before planning anything**, read sections 1, 9 (principles), 12
  (weaknesses) and 13 (roadmap). They encode decisions the owner already
  made; don't re-litigate them without a reason.
- **Before touching `verdict_engine.py`**, read section 6. It's 2,400 lines
  of calibrated guards, each added after a real wrong verdict. The comments
  in the code carry the evidence; read them before changing a guard.
- **Before adding any data source**, read section 10 (sources & legal).
- **Before claiming a change helped**, run the eval (section 8) before and
  after. "Tests pass" is necessary, not sufficient.
- All dates in this repo are absolute (YYYY-MM-DD). "Today" at handoff was
  2026-09-17.

---

## 1. What this is

**RainTrade** (rain.trade) runs prediction markets, binary ("Will X happen
by date?") and multi-outcome ("Who wins X?"). Every market must be settled
once its real-world outcome is known. Today a human researches each one.

**Resolution Finder automates the research step.** For each market it:

1. Flags whether a resolution is **available** yet (deadline passed, event
   happened) — cheap, date-based.
2. Proposes **what it resolves to** (YES / NO / which option), with the
   evidence sentence and source link — expensive, evidence-based.
3. Stores both in a local SQLite DB and shows them in a small review
   dashboard where a human **Confirms** or **Rejects**.

**It never settles a market itself.** It's decision support.

### The owner's priority order (hard rule)

> Maximize correct and resolved — but **wrong is the hard constraint.**
> Never accept more wrong verdicts to get more resolved ones. When in doubt,
> the answer is UNCLEAR.

A wrong verdict is worse than it looks: it usually means the system
confidently asserted from low-quality evidence instead of abstaining.
Every wrong verdict found during the build was treated as a signal to
tighten a gate, not as bad luck.

### Target use case

- The eventual target is **niche markets** with low news coverage (e.g.
  player-level stats like yellow cards, narrower framings like "will a
  *Canadian* team win the Stanley Cup"). A separate existing system already
  closes rain.trade markets that mirror a Polymarket market when Polymarket
  closes, so this tool isn't meant to duplicate that.
- Current phase: prove coverage across **market shapes** on broad,
  well-covered markets (the eval set is mostly mainstream Polymarket
  markets). The owner's view: cover every market *type*, and niche markets
  follow, because a niche market is usually a mainstream shape with less
  coverage.
- Priority verticals: **sports and politics**. Crypto, pharma, box office,
  weather are regression coverage, lower priority.

### Original constraints (from the 2026-08-10 design spec)

- **$0 marginal cost:** no paid APIs, no generative LLM in the decision path.
  Only free, local models (sentence embeddings + an NLI zero-shot model),
  regex, and public web data.
- Two clean extension points kept open: a real market-data provider (instead
  of the JSON file) and an alternative verdict engine (the pipeline takes
  `verdict_engine=` as an injectable callable).

---

## 2. Status at handoff

- **161 commits** in total. All real work is on branch
  `resolution-finder-scanner` (in the zip, it's the checked-out branch);
  `master` holds only the first 4 commits (design spec + plan). The branch
  was never merged — merging it into `master` is a fine first step.
- **360 tests passing** (~3 minutes; loads the real ML models).
- **Two resolution tiers exist:**
  1. **Structured resolvers** — read an official source directly. One is
     built: **Fed interest-rate decisions** (FOMC statement + Fed rate
     table). **14/14 correct, 0 wrong** on the real Fed markets, live.
  2. **News path** — search news, fetch articles, rank, then a rule-based
     + NLI verdict engine with corroboration. Handles everything else.

### Eval numbers (all on real markets with known answers)

| Run | Markets | Correct | Wrong | Unresolved | Notes |
|---|---|---|---|---|---|
| 2026-09-07 full batch | 97 | 18 | 1 | 74 | Before the Bing User-Agent fix (headline-only evidence) |
| 2026-09-16 subset | 46 | 15 | 0 | 28 | After elimination corroboration + Bing fix |
| 2026-09-17 fresh batch | 68 | 12 | 2 → **0** | 54 → 56 | Both wrongs were a sentence-splitter bug ("vs."), fixed same day |
| 2026-09-17 Fed markets | 14 | **14** | 0 | 0 | Structured resolver |
| Composite (latest result per market, across runs) | 161 with truth | **47** | **0** | 114 | Mixed commits; **no single full 165-market run exists** |

The headline picture: **the system rarely gets things wrong, but leaves
most markets unresolved.** That is by design (see priority order), and the
roadmap (section 13) is mostly about resolving more without adding wrongs.

---

## 3. Environment: why Linux (WSL2), and setup

### Why WSL2 — this is important

The project owner's Windows 11 machine has **Smart App Control** enabled.
It blocks any unsigned native binary from loading. Almost no PyPI package
ships signed `.pyd`/`.dll` files, so on 2026-08-18 **numpy itself was
blocked** (Windows Code Integrity event 3077: `bit_generator.cp314-win_amd64.pyd
... did not meet the Enterprise signing level requirements`). numpy,
torch, sentence-transformers and transformers are unusable on native Windows
Python on that machine.

Smart App Control is all-or-nothing (no per-app allowlist) and can't be
turned back on without reinstalling Windows. The owner chose to **keep it
on**. So all Python runs inside **WSL2 (Ubuntu)**, where Windows Code
Integrity doesn't apply. This changed nothing about the code; it's purely
where it runs.

If your machine doesn't have Smart App Control, native Linux, macOS, or
Windows Python will all work; WSL is not a code requirement.

### Versions it was built and tested on

- WSL2, Ubuntu 26.04 LTS
- Python 3.14.4 (a `torch.jit.script` deprecation warning on 3.14 is
  expected and harmless)
- torch 2.13.0, transformers 5.15.0, sentence-transformers 5.7.0,
  curl_cffi 0.16.0, feedparser 6.0.14, trafilatura 2.2.0, Flask 3.1.3,
  requests 2.34.2, pytest 9.1.1
- `requirements.txt` is unpinned (names only). If something breaks after a
  fresh install, pin to the versions above first.

### Setup (WSL / Linux)

```bash
cd "/mnt/c/path/to/resolution-finder"      # or wherever you unzipped it
python3 -m venv .venv-wsl
.venv-wsl/bin/pip install -r requirements.txt
PYTHONPATH=. .venv-wsl/bin/python -m pytest tests/ -q     # expect 360 passed
```

- **First run downloads two models** from Hugging Face into
  `~/.cache/huggingface` (internet needed once): `all-MiniLM-L6-v2` (~90 MB)
  and `MoritzLaurer/deberta-v3-xsmall-zeroshot-v1.1-all-33` (~142 MB). An
  "unauthenticated requests to the HF Hub" warning is harmless.
- The original venv was named `.venv-wsl` and lived at the repo root. It is
  not in the zip (platform-specific, large); recreate it as above.

### Running from Windows (how the original sessions did it)

Claude Code ran on Windows and called into WSL for every Python command:

```bash
wsl -- bash -c "cd '/mnt/c/Users/<you>/.../resolution-finder' && PYTHONPATH=. .venv-wsl/bin/python -m pytest tests/ -q"
```

WSL gotchas learned the hard way:
- **Paths contain spaces** ("Resolution Finder"): always single-quote inside
  the `bash -c "..."` string.
- **Nested quoting breaks with space-separated arguments** (e.g. passing
  several market IDs to `run_eval.py`): the command silently fails. Write a
  tiny Python script that calls `run_eval({...})` instead (examples in
  `scratchpad/`).
- **`/tmp` differs** between Git Bash, Windows Python and WSL. Write
  temporary files inside the repo (e.g. `scratchpad/`).
- **Output redirected to a file is block-buffered**; use `python -u` to see
  progress live.
- **CPU time vs wall time:** `ps` CPU time is summed across torch's threads
  (~8×). "38 CPU-minutes" was ~5 real minutes. Don't mistake it for a hang.
- **Git in a worktree:** the original work lived in a git worktree whose
  `.git` pointer was written by Windows git, which WSL's git can't read.
  `run_eval.py._git_state` tries `git` then `git.exe` for that reason. In a
  normal clone (like the zip) WSL git works fine.

---

## 4. How to run things

All commands from the repo root, inside the venv (prefix with
`PYTHONPATH=. .venv-wsl/bin/` or activate the venv).

| Task | Command | Notes |
|---|---|---|
| Tests | `python -m pytest tests/ -q` | ~3 min, 360 tests. Network is mocked; ML models are real. |
| Scan all markets | `python run_scan.py` | Live web. Reads `data/markets.json`, writes `data/resolution_finder.db`. Slow: minutes per multi-outcome market. |
| Review dashboard | `flask --app 'resolution_finder.dashboard:create_app("data/resolution_finder.db")' run` | http://127.0.0.1:5000. There is no launcher script; this is the command. |
| Eval, replay from cache | `python run_eval.py` (all) or `python run_eval.py <id> <id>` | Scores vs `resolved_to`; appends to `data/eval_history.jsonl`. |
| Eval, refresh retrieval | `python run_eval.py --live [<id> ...]` | Re-fetches news; slow (~90 min cold for ~100 markets). |
| Pull markets from Polymarket | `python pull_test_batch.py "<query1>" "<query2>" ...` | Verbatim-only puller. Writes new resolved markets (skipping ids already in `markets.json`) to `scratchpad/pulled_batch_preview.json` **for review — it never merges automatically**. Merge by copying whole entries verbatim. See section 7.1. |
| NLI diagnostic report | `python nli_report.py` | Raw entailment scores on curated real cases, pass/fail. |
| NLI-only experiment | `python nli_only_eval.py [<id> ...]` | Experimental: verdicts from NLI alone, no keyword gates. Not production. |

**Scheduling:** the design called for Windows Task Scheduler to run
`run_scan.py` every few hours. **It was never set up.** On the owner's
machine it would have to invoke WSL, e.g.
`wsl -- bash -c "cd '/mnt/c/...' && .venv-wsl/bin/python run_scan.py"`.

---

## 5. Architecture

```
data/markets.json
   │  JsonFileMarketProvider.get_unresolved_markets()      (market_provider.py)
   ▼
pipeline._scan_market(market)                                (pipeline.py)
   │
   ├─ check_availability(market)          → "is a resolution available?"   (resolution_spec.py)
   │     computed first, attached to every saved finding, never gates anything
   │
   ├─ structured_resolver(market)          → official-source answer       (structured_resolvers.py)
   │     Fed decisions today (fed_decision.py + fed_pages.py + page_fetch.py)
   │     returns None = not my type → continue;  list = saved, STOP (never falls through)
   │
   ├─ peer_checker(market)                 → similar resolved Polymarket market  (peer_market.py)
   │     DISABLED by config (PEER_MARKET_ENABLED = False); binary markets only
   │
   └─ news path
        build_queries(market)                                        (query_builder.py)
        retrieve_evidence(market, queries)   Bing News RSS + Google News archive + social  (evidence_retriever.py)
        extract_article_text(url)            robots.txt-checked, curl_cffi fetch, trafilatura  (article_extractor.py)
        rank_by_relevance(market, articles)  MiniLM cosine ≥ 0.35                (relevance_ranker.py)
        decide(market, ranked)               rule-based + NLI + corroboration    (verdict_engine.py)
        _promote_best_below_threshold        surface closest text as UNCLEAR if nothing ranked
   ▼
save_finding(...)  → data/resolution_finder.db                        (storage.py)
   ▼
Flask dashboard: review queue, Confirm/Reject                        (dashboard.py + templates/)
```

`run_eval.py` runs the same sequence (structured resolver → retrieval →
rank → decide) but caches retrieval in `data/eval_cache.json`, scores
against ground truth, and records a full trace per market.

### Core data models (`models.py`)

- `Market(id, title, description, options: list[str], close_date: Optional[date])`
  — `options == []` means binary.
- `ArticleRef(url, title, source_type, published_date, summary, source_domain)`
  — `source_type` ∈ `primary`, `credible_backup`, `credible_backup_secondary`,
  `official_social`, `peer_market`, `general`. **`source_type` is display-only;
  the verdict engine never reads it.**
- `RankedArticle(article, text, similarity)`
- `Verdict(outcome, confidence, evidence_snippet, source_url, source_type, option=None)`
  — `outcome` ∈ `YES`, `NO`, `UNCLEAR`, `NO_EVIDENCE`. Multi-outcome markets
  return a list: one verdict per option when decided, or one whole-market
  verdict with `option=None` when not.

### Two separate questions, two separate bars

Availability ("is it worth a look?") and outcome ("what does it resolve
to?") are deliberately separate (design:
`docs/superpowers/plans/2026-09-08-resolution-availability-design.md`). A
false "available" costs a human one wasted glance; a wrong outcome settles
a market incorrectly. So availability is cheap and permissive; outcome is
strict.

---

## 6. Module reference

### `resolution_finder/config.py`
Paths and calibrated thresholds: `SIMILARITY_THRESHOLD = 0.35` (relevance;
**never empirically calibrated**, flagged), `PEER_MARKET_SIMILARITY_THRESHOLD = 0.75`,
`PEER_MARKET_ENABLED = False`, model names, `REQUEST_DELAY_SECONDS = 1`,
`MAX_RESULTS_PER_QUERY = 5`.

### `market_provider.py`
`MarketProvider` protocol + `JsonFileMarketProvider`. The protocol is the
seam for a future real rain.trade API client. Handles `close_date: null`.

### `resolution_spec.py` — deterministic description parsing, no models
- `parse_resolution_spec(market)` → `backstop_deadline` ("by / no later
  than / on or before / prior to <Month D, YYYY>"), `default_outcome` (the
  "otherwise resolves to X" clause, deadline-anchored variant preferred),
  `scheduled_event_date` ("scheduled for <date>"), `names_primary_source`.
- `check_availability(market, spec, today)` → `AvailabilityResult(available,
  reason, trigger_date)`. Signals checked in order of **precision**:
  `scheduled_event_passed` > `close_date_passed` > `backstop_deadline_passed`.
  Measured coverage on the 97-market batch: close_date 96%, backstop
  deadline 44%, scheduled event 17%.
- Never guesses a year for "Month D" with no year.

### `structured_resolvers.py`, `fed_decision.py`, `fed_pages.py`, `page_fetch.py`
- `resolve_structured(market, fetch=http_fetch)` runs `STRUCTURED_RESOLVERS`
  in order; first non-None wins. **A claimed market never falls through to
  peer-check or news.** This is the plug-in point for future official-source
  resolvers (price markets, Congress, SCOTUS).
- `page_fetch.http_fetch` — plain `requests` with the project User-Agent,
  raises `FetchError`; decodes UTF-8 when the server sends no charset
  (federalreserve.gov does, and ISO-8859-1 fallback breaks U+2011 hyphens
  in "3‑3/4"). **No caching in production.**
- `fed_pages.py` — pure parsers over real HTML: FOMC calendar
  (`parse_calendar`), statement decision sentence (`parse_decision`, anchored
  on "the Committee decided to" so dissents are never read), rate table
  (`parse_rate_table`). Raise `FedPageParseError` on anything unexpected.
- `fed_decision.py` — strict recognition (title "Fed decision/interest
  rates…", options all rate brackets), meeting identification (explicit
  dates in description > "Month YYYY" > title month + close_date year),
  **statement and rate table must agree** (direction, stated amount, new
  upper bound, table row within 2 days after the decision), rounding only
  when the description states it, exactly one option must match. Statement
  not yet published → `NO_EVIDENCE`. Everything else → `UNCLEAR` with the
  reason. Spec: `docs/superpowers/specs/2026-09-17-fed-decision-resolver-design.md`.

### `peer_market.py` (disabled)
Searches Polymarket Gamma `public-search` for an already-resolved similar
binary market and proposes its outcome. Four hard-reject checks carry the
safety (similarity alone proved unsafe): resolution window ±7 days, bill
numbers, numeric thresholds, entity terms; then cosine ≥ 0.75. Real false
matches it rejects are documented in its comments. Disabled by the owner's
choice, not because it's broken; flip `PEER_MARKET_ENABLED` to re-enable.

### `query_builder.py`
`build_queries(market)`: the title; title + up to 3 regex-extracted entities
from the description (title entities as fallback); one query per option.
`extract_entities` is a regex proper-noun heuristic (spaCy was replaced),
with stopword trimming for real garbled-query bugs.

### `evidence_retriever.py`
- **Bing News RSS** (`search_bing_news_rss`) is the main source. Its wrapper
  links carry the real article URL as a `url=` parameter, so articles are
  fetchable. Results kept only if the domain is in a credible tier, or
  promoted to `primary` if it matches the market's named source.
- **Google News RSS**: its links are unfetchable JS redirects, so Google
  results only ever contribute **headlines**. Used for (a) the **archive
  pass** — only for markets whose close date passed — with `after:`/`before:`
  operators scoped to close_date −30/+21 days (Bing ignores those operators),
  capped at 8 queries; and (b) best-effort site-scoped X/Instagram searches
  for a few official handles. **X/Instagram pages are never fetched.**
- **The single most consequential bug of the project (2026-09-08):**
  feedparser sends no User-Agent, and Bing answers that with HTTP 200 and an
  empty feed. For weeks every Bing search returned nothing and the engine
  decided markets from Google headlines alone. Fixed with an honest
  self-identifying `FEED_USER_AGENT`. If retrieval ever silently drops to
  zero, check this first.

### `article_extractor.py`
`extract_article_text(url)`: refuses `BLOCKED_HOSTS` (x.com, twitter.com,
instagram.com); skips `UNRESOLVABLE_HOSTS` (news.google.com — 400+ attempts,
0 successes); checks **robots.txt** (404 = allowed, any other failure =
**fail closed**); fetches with **curl_cffi Chrome TLS impersonation**
(needed for Akamai-protected news sites that fingerprint TLS); retries only
timeouts/connection errors (not SSL errors, not HTTP rejections); re-checks
blocked/robots after redirects; extracts with trafilatura.

### `relevance_ranker.py`
MiniLM embedding cosine between "title + description" and article text;
keep ≥ 0.35, sorted. `best_below_threshold` is for display only (never fed
to the verdict engine).

### `verdict_engine.py` — the core (2,411 lines)

`decide(market, ranked)` dispatch:
1. options present + cumulative-date options ("by Aug 1 / by Sep 1") → `_decide_date_thresholds`
2. options present → `_decide_multi_outcome`
3. binary + a numeric condition parsed from the description → `_decide_numeric_threshold`
4. binary otherwise → `_decide_binary`

**Shared building blocks**
- `_split_sentences` — `SENTENCE_SPLIT_PATTERN` guards abbreviations like
  "U.S." and **"vs."** ("Team A vs. Team B" split into a one-team fragment
  caused 2 real wrong verdicts). "Sen."/"Rep."/"Jan." are a known,
  accepted residual risk.
- Sentence filters: `_sentence_has_hedge` (speculation/negation words — for
  YES claims), `_sentence_has_hedge_for_negative_claim` (same minus negation
  — for NO/elimination claims, where "has not won" *is* the signal),
  `_sentence_is_interrogative`, `_sentence_is_vague_reference`,
  `_sentence_describes_a_different_metric`.
- **Wrong-subject veto:** `_sentence_mentions_other_entity` drops sentences
  about a different entity than the market's subject.
- **Context-conflict vetoes** (multi-outcome): conflicting year (sentence or
  the article's lead sentence), relative season ("last season" vs
  publish date), "last year's", playoff vs regular season, relative recency
  ("the past two years"), a different UEFA competition. Each exists because
  of a real wrong verdict (e.g. crowning a champion from a different season).
- **NLI verification** (`_classify_scores` → HF `zero-shot-classification`
  pipeline): winner hypotheses `"{option} has won {title}."` threshold
  **0.85**; head-to-head **0.65** (2-option markets only — beating one
  opponent in an 8-team market is not winning it); elimination hypotheses;
  binary-YES and "consequence" checks; threshold-not-met **0.7**.
- **Corroboration:** any YES/NO/winner/elimination assertion needs
  confirmations from **≥ 2 distinct domains** (`CORROBORATION_MIN_DOMAINS`);
  wire-service copies (full-text embedding similarity ≥ 0.95) count once.
  One source only → UNCLEAR with a note. Capped at 3 confirmations per
  option to bound NLI cost.

**`_decide_binary`:** candidates from `BINARY_YES_KEYWORDS` (legislative,
confirmation, FDA vocabulary) verified by NLI; per-article semantic fallback
(MiniLM templates, 0.72) when no keyword matched; a NO path only for titles
shaped "Will X win/beat/advance…?" (elimination hypotheses). YES and NO both
corroborated → UNCLEAR (disagreement). **The description's stated default
("otherwise resolves No") is never emitted as a verdict** — removed
2026-08-26 after it produced 2 wrong verdicts: "our search found nothing" is
not "it didn't happen". It's shown to the reviewer as context only.

**`_decide_multi_outcome`:** for every sentence × option that mentions the
option: winner verification (with other mentioned options compared), head-
to-head (2-option only), elimination (negation-permissive gate). Winners
and eliminations both need corroboration. More than one corroborated winner
→ UNCLEAR. Confirmed winner → YES for it, NO for every other option.

**`_decide_date_thresholds`:** per date option, the same winner/elimination
evidence scan; an option whose own date has passed with no evidence → NO;
future dates → UNCLEAR.

**`_decide_numeric_threshold`:** parses the condition (direction + number)
from the description, extracts numbers from evidence, requires corroboration
on both sides. Titles marked "(HIGH)"/"(LOW)" (price-window markets) are
refused outright — news can't answer them.

### `pipeline.py`
`run_pipeline(market_provider, db_path, verdict_engine=decide,
peer_checker=_default_peer_checker, structured_resolver=resolve_structured)`.
Everything is injectable for tests. One failing market is logged and
skipped, never aborts the run.

### `storage.py`
SQLite. `findings(id, market_id, option, run_timestamp, outcome, confidence,
evidence_snippet, source_url, source_type, review_status['Pending'|
'Confirmed'|'Rejected'], resolution_available, availability_reason)` and
`settings(key, value)`. `_migrate()` adds columns to older DBs.
`get_latest_findings` returns the newest row per (market, option).

### `dashboard.py` + `templates/`
Review queue table (market, option, availability, outcome, confidence,
evidence, source link with reliability labels, status, Confirm/Reject).
**`/settings` page for a "Currents News API" key is dead UI** — Currents was
never wired into retrieval.

### `eval_cache.py`
Retrieval snapshot per market (queries, candidate refs, extracted text or
`None` for failures) so evals replay deterministically; `CachedPageFetcher`
caches structured-resolver pages under the reserved key
`__structured_pages__`. **Eval harness only** — standing rule: no caching in
the production pipeline.

### Root scripts
- `run_scan.py` — production entry point.
- `run_eval.py` — eval harness (section 8).
- `pull_test_batch.py` — verbatim Polymarket puller (section 7.1).
- `nli_report.py` — NLI score diagnostics on curated real cases.
- `nli_only_eval.py` — experiment: NLI-only verdicts, no keyword scaffolding.
- `conftest.py` — puts the repo root on `sys.path` for pytest.

### Tests (`tests/`)
One file per module; 360 tests. Network is mocked; the real embedding and
NLI models load (that's the 3 minutes). `tests/fixtures/fed/` holds six real
federalreserve.gov pages saved byte-for-byte (`.gitattributes` marks them
`-text`); negative tests mutate a real fixture inside the test. Test market
text is copied verbatim from `data/markets.json`, never invented.

---

## 7. Data

### 7.1 `data/markets.json` — the eval set (165 markets)

**What it is:** real, already-resolved prediction markets with their real
outcome, used to measure the pipeline. It is also what `run_scan.py` reads
(the JSON provider stands in for a future rain.trade API).

**Schema (one object per market):**

| Field | Type | Meaning |
|---|---|---|
| `id` | str | The source's own slug, verbatim (some end in a long numeric timestamp — that's the source's slug, not generated) |
| `title` | str | Verbatim market title |
| `description` | str | Verbatim resolution rules — **the most important field** (section 9) |
| `options` | list[str] | `[]` = binary. Otherwise the option names verbatim, in source order |
| `close_date` | "YYYY-MM-DD" or null | Source end date. **6 are null** (3 March Madness, 3 NBA games) |
| `resolved_to` | str or null | Ground truth: "Yes"/"No" for binary, the option string for multi-outcome |

**Composition:** 165 markets — 65 binary, 100 multi-outcome (max 32 options,
median 4). 161 have ground truth; 4 don't (`clarity-act-2026`,
`international-2026-champion`, `nobel-peace-2026`, `anthropic-ipo-by` —
still open when pulled). Close dates span 2022–2027 (2023: 17, 2024: 45,
2025: 50, 2026: 43, 2027: 3). Approximate mix: ~70 sports (single NFL/NBA/
MLB/NHL/soccer games, series, tournaments, F1, golf, tennis, esports), ~50
politics & government (US and international elections, Congress, SCOTUS,
nominations, tariffs), 14 Fed rate decisions, 17 crypto/gold/stock price
markets, and a handful of FDA approvals, box office, weather, awards, IPO,
earnings.

**Where it came from:** mostly pulled verbatim from Polymarket's public
Gamma API (`gamma-api.polymarket.com/public-search`) with
`pull_test_batch.py`, plus a curated 68-market Polymarket batch added
2026-09-16 (selected for new *shapes*: Fed decisions, crypto brackets,
international elections, a multi-winner market, margin brackets, large
fields, earnings). A few of the very first entries came from scenarios the
owner supplied at project start. rain.trade markets weren't pulled in bulk:
at the time everything open on rain.trade was mirrored on Polymarket, so
Polymarket gave the same markets with resolved outcomes.

**How `resolved_to` is derived:** the API doesn't return a "winner" field.
The puller takes the outcome whose own settled price is ≥ 0.9
(`RESOLVED_PRICE_THRESHOLD`) — a numeric selection over source data, not
authored text. Markets are included only if `closed` and
`umaResolutionStatus == "resolved"`.

**HARD RULE — data purity:** every field must come verbatim from the source
API. **Never write, paraphrase, merge, fix, or invent** any field, including
ids. Reason: the whole point is to measure the pipeline against real-world
market phrasing; if Claude writes the text, the eval measures Claude's
writing. Real incident (2026-08-18): Claude invented 8 ids and merged three
per-candidate descriptions into new prose; the owner flagged it as a
violation. If a pull is missing something, fix the puller and re-pull. If
sub-markets need combining into one multi-outcome market, ask the owner.
The same rule applies to test fixtures.

**Known quirks (real, left verbatim on purpose):**
- `will-50-senators-vote-yea-for-todd-blanche-...`: title reads binary,
  `options` is empty, but the description is a **vote-count bracket** spec
  ("resolve to the number of senators who vote Yea… highest bracket").
- `will-the-price-of-ethereum-be-less-than-1400-on-august-17-2026`: binary
  title, but bracket-style boilerplate in the description ("falls exactly
  between two brackets").
- `premier-league-top-4-finishers` and `epl-team-to-qualify-for-uefa-champions-league`:
  multi-**winner** markets (4 teams qualify) forced into single-winner shape.
- Some descriptions are one-liners with no rules ("This is a market on the
  anticipated Federal Reserve interest rate decision for December 2024.").

**Scoring conventions** (`run_eval._score`): binary → correct if any
YES/NO verdict matches `resolved_to` (case-insensitive); multi-outcome →
correct if the ground-truth option got YES, wrong if it got NO or another
option got YES; otherwise unresolved.

### 7.2 `data/eval_history.jsonl` — every eval run (83 runs at handoff)

One JSON object per run: `timestamp`, `git_commit`, `git_dirty`,
`market_ids`, `summary {correct, wrong, unresolved, no_ground_truth}`, and
`results[]` with per-market `ground_truth`, `from_cache`, all `verdicts`
(outcome, option, confidence, evidence_snippet, source_url, source_type),
`verdict_class`, and a `trace` (queries, candidate URLs by type, extraction
count, fetch failures, top-5 ranked snippets up to 1,000 chars; or
`structured_resolver` + fetched URLs). This is the project's labeled
history — the natural dataset if anyone ever fine-tunes the NLI model
(an idea on the backlog: ~31 labeled cases were judged enough for few-shot
tuning per a paper found during research).

### 7.3 `data/eval_cache.json` (gitignored, ~6 MB, included in the zip)

Retrieval snapshots for replay. Contains extracted third-party article
text, which is why it's not in git. Delete it or run `--live` to refresh.
Retrieval is **non-deterministic** (search results change hour to hour), so
compare code changes on the **cached** replay, not on fresh live runs.

### 7.4 `data/resolution_finder.db` (gitignored)

Local SQLite from `run_scan.py`. The copy on the original machine is stale
(2026-08-18) and not included; `run_scan.py` recreates it.

---

## 8. Evaluation methodology

- **The loop the owner uses:** test → find bugs on real markets → fix (TDD)
  → re-run the eval → confirm wrong didn't rise → repeat.
- **Measure before and after** on the same market set using the cache:
  `python run_eval.py <ids...>` (replay). Use `--live` only to deliberately
  refresh evidence.
- Every run appends to `eval_history.jsonl` with the git commit, so results
  are traceable.
- **Wrong is investigated immediately**, before anything else. Every wrong
  so far traced to a real bug or an over-trusting gate.
- **Runtime:** replay is mostly model time; heavy multi-outcome markets
  (8+ options, 25+ articles) take ~5 minutes each (hundreds of NLI calls at
  0.3–2.5 s). A cold `--live` run of ~100 markets took ~90 minutes. The
  cache saves after every market, so interrupted runs resume.
- **Caveats:** the "composite" number in section 2 mixes runs from different
  commits. There has never been one full 165-market run — worth doing once
  as a baseline.

---

## 9. Design principles and hard rules (with the why)

1. **Wrong is the hard constraint.** Abstain rather than guess. Thresholds
   were deliberately set toward the strict side.
2. **Description beats title.** Titles are display text and often misleading;
   every structural gap discovered came from reading the full description.
   Exception discussed but not built: for crypto/price markets the title may
   state instrument and threshold most plainly.
3. **Description-first strategy.** rain.trade descriptions ship a full spec:
   exact condition with carve-outs, enumerated membership sets, tiebreak
   cascades, a named primary source, a deadline and a default. They name
   news consensus as the **backup**. The news pipeline was built as the
   primary; the structured-resolver tier is the correction. Don't solve set
   membership with NLP when the description enumerates the set. See
   `docs/superpowers/plans/2026-09-08-description-first-strategy.md`.
4. **Keywords are candidates, a model verifies.** After repeated keyword
   false positives, the owner rejected "add more keywords" as unscalable.
   Cheap gates only select candidates; NLI decides.
5. **Corroboration:** one source is never enough to assert.
6. **"Found nothing" ≠ "didn't happen."** Never emit a default outcome as a
   verdict from absence of evidence.
7. **No generative LLM in the decision path**, $0 marginal cost. The owner
   has said nuanced cases (e.g. "is a Canadian-born player on another
   country's team 'Canadian'?") may eventually justify a local LLM as a final
   gate — explicitly deferred, not rejected.
8. **Never overfit to one event.** Fixes found on the World Cup or one bill
   must generalize to the vertical.
9. **Data purity** (section 7.1).
10. **Caching only in the eval harness**, never in production.
11. **Verify live, don't assume.** Every whitelisted domain, API behavior and
    threshold in this repo was checked against real requests or real text;
    comments record the evidence.
12. **Real bugs get fixed immediately; missing market-type support gets
    logged** in `docs/superpowers/plans/2026-08-25-unsupported-market-types.md`,
    not force-fitted.

---

## 10. Sources, access and legal register

**Governing rule:** robots.txt compliance reduces risk but is **not legal
clearance**; a site's Terms of Service can be stricter. Check both before
adding a source. Never work around a block, a broken certificate, or a
WAF challenge.

| Source | Use | Status / why |
|---|---|---|
| Bing News RSS | Main evidence search | In use. Needs a User-Agent (see section 6). |
| Google News RSS | Headlines + date-scoped archive | In use. Links unfetchable by design. |
| News sites (Tier 2 lists in `source_config.py`) | Article text | Each domain verified live (robots.txt + real extraction) before adding; per-domain records in comments. |
| curl_cffi TLS impersonation | Akamai-protected ordinary news sites | Judged acceptable for ordinary public news sites only. |
| X / Instagram | — | **Never fetched.** A narrow post-fetch feature was built then **reverted** (commit `f570537`): TLS impersonation against platforms with a history of suing scrapers is the strongest form of ToS violation. Only site-scoped search headlines, shown for manual verification. |
| federalreserve.gov | Fed resolver | US government work, free for any use. |
| Polymarket Gamma API | Eval data pulls; peer check (disabled) | Public API. |
| Congress.gov API | Planned Congress resolver | Public domain; **needs a free api.data.gov key** (owner to obtain). `DEMO_KEY` = 10 req/hour. |
| House Clerk roll-call XML | Possible | Keyless, works. |
| senate.gov | — | **Blocks automated access** (Akamai 403). Use Congress.gov instead. |
| ballotpedia.org | — | AWS WAF JS-challenge on every curl_cffi fetch (2026-08-22). Don't whitelist until re-verified repeatedly. |
| inecnigeria.org | — | Broken SSL certificate chain (2026-08-17). Never disable verification. |
| marketwatch.com / nature.com / columbian.com / i24news.tv / msn.com | — | Disallowed by robots, paywalled, 403, empty extraction, or app-shell respectively. |
| Binance / Coinbase / CoinGecko free / Kraken | Crypto prices | **Ruled out** — terms bar commercial/settlement use (Kraken: "only for your own benefit"). |
| Pyth | Crypto/gold/stock prices | Paid (~$500/month) since 2026-08-26. |
| Chainlink feeds | Crypto prices | **Ambiguous** licensing; needs the owner's legal review. |
| ESPN (incl. site.api.espn.com) | Sports data | **Ruled out:** Disney Terms of Use prohibit commercial use and automated extraction. |

**⚠ Open inconsistency to resolve:** `espn.com` is still listed in
`TIER2_SECONDARY_OUTLETS` (`source_config.py`), so the news path fetches
ESPN article text found via Bing. That predates the 2026-09-17 finding that
Disney's terms prohibit automated extraction. Not changed at handoff — the
owner should decide whether to remove it.

---

## 11. Strengths

- **Very low wrong rate** by construction: corroboration, NLI verification,
  context vetoes, strict thresholds, abstain-by-default.
- **Structured-resolver tier** gives exact, fully explained answers where an
  official source exists (Fed: 14/14), and is a clean plug-in point.
- **Explainable:** every verdict carries the exact evidence sentence and
  link; every guard's code comment records the real case that motivated it.
- **Measurable:** a real labeled eval set, deterministic replay, full
  per-market traces, history tied to commits.
- **Availability signal** (~96% coverage) already answers "does this need
  resolving now?" for most markets, separately from the outcome.
- **Careful sourcing discipline:** robots.txt fail-closed, blocked hosts,
  documented legal decisions.
- **Swappable seams:** market provider, verdict engine, peer checker,
  structured resolvers — all injectable.
- **$0 to run**, fully local models.

## 12. Weaknesses and known limitations

**Coverage**
- **Most markets stay unresolved** (composite 114 of 161). The news path
  needs two independent sources saying the same thing in verifiable words.
- **Niche markets** (the real target) have little coverage by definition —
  the corroboration bar was tuned on mainstream markets and hasn't been
  measured on niche ones.
- **Unsupported market shapes** (tracked in
  `docs/superpowers/plans/2026-08-25-unsupported-market-types.md`):
  bracketed numeric ranges (vote counts, election margins, crypto price
  brackets, TV viewership), "between X and Y" ranges, price-history windows
  and submarket reopening, box-office weekend grosses, multi-winner
  "qualifies" markets forced into single-winner shape, multi-category
  classification forced into binary.
- **Outcome-description options** ("25 bps decrease", "64,000-66,000") break
  the `"{option} has won {title}"` NLI hypothesis (the real headline scored
  0.337 vs 0.85). Fed markets sidestep it via the structured resolver; the
  wording bug itself is unfixed.
- **Membership/abstraction joins:** options that are nations while articles
  name players (or "Canadian team") need a join NLI can't do.

**Retrieval**
- Depends on Bing/Google RSS behavior (the silent-empty-feed bug shows how
  fragile that is); results are non-deterministic.
- Google results are headlines only.
- Named sources are only a display label (`source_type="primary"`); a named
  official page is fetched only if its URL is literally in the description
  and extractable.

**Engine**
- Numeric extraction can pick the wrong number (a Binance futures/spot
  **volume ratio** of 7.82 was read as the BTC price; a film's total gross
  read as its 5th-weekend gross). `bitcoin-above-64k-on-august-17-2026`
  currently resolves YES at 0.551 via that exact mechanism — matching the
  truth by coincidence.
- `SIMILARITY_THRESHOLD = 0.35` never calibrated.
- Sentence splitting on abbreviations ("Sen.", "Rep.", "Jan.") is a known
  residual risk.
- **Performance:** heavy multi-outcome markets ~5 minutes each; the only
  output-identical speedup found is memoizing repeated NLI calls (~12%).
  Batching the model directly shifts scores up to 0.001 — rejected.

**Operations**
- No scheduler set up; no launcher script for the dashboard; dead Currents
  settings page; no real rain.trade API client; single-user local SQLite.
- Requirements unpinned.
- The ESPN inconsistency (section 10).

## 13. Roadmap (prioritized, with where the design lives)

| # | Item | State | Doc |
|---|---|---|---|
| 1 | **Congress/Senate official-record resolver** (nominations, both-chambers passage, impeachment, bill-becomes-law) — 6 real markets with truth, 0 resolved today | Outline design accepted, not built. Blocked on api.data.gov key. Verify the 3 "No" markets' records first. | `docs/superpowers/plans/2026-09-17-congress-resolver-handoff.md` |
| 2 | **SCOTUS ruling resolver** — 9 real markets, 0 resolved | Not designed; harder (interpreting holdings) | `docs/superpowers/plans/2026-09-17-remaining-work-triage.md` |
| 3 | Fed dissent-count market (same FOMC statement) | Small add-on | triage doc |
| 4 | NLI call memoization (output-identical, ~12% fewer calls) | Small | triage doc |
| 5 | Decide on `espn.com` in Tier 2 | Owner decision | section 10 |
| 6 | Crypto/gold/stock price markets | **Blocked on licensing** (owner chose to leave blocked) | `docs/superpowers/plans/2026-09-17-price-markets-handoff.md` |
| 7 | Structured sports-data source (yellow cards, goals, match times) | ESPN ruled out; needs a provider whose terms allow it | unsupported-market-types doc |
| 8 | Unsupported shapes (brackets, multi-winner, etc.) | Tracked | unsupported-market-types doc |
| 9 | One full 165-market baseline run; calibrate `SIMILARITY_THRESHOLD` | Not done | — |
| 10 | Niche-market eval batch (needs a non-Polymarket sourcing approach) | Planned later phase | — |
| 11 | Operations: scheduler (via WSL on the owner's machine), dashboard launcher, pin requirements, real rain.trade provider | Not done | — |

Closed as not worth doing (with evidence, in the triage doc): rewording the
"has won" hypothesis alone (resolves ~nothing), expanding the
`TIER1_DOMAINS` named-source list alone (display-only, moves no numbers).

---

## 14. History — key milestones and lessons

- **2026-08-10** Design spec + 23-task implementation plan; built via
  task-by-task subagent development (briefs/reports in `.superpowers/sdd/`).
- **2026-08-17** robots.txt enforcement; curl_cffi for Akamai sites; X
  post-fetching built then reverted on ToS grounds; INEC SSL finding.
- **2026-08-18** Smart App Control blocks numpy → move to WSL2. Data-purity
  rule established after invented ids/merged descriptions.
- **2026-08-22–23** Zero-retrieval investigations (Ballotpedia WAF);
  **architecture change: keywords become candidates, NLI verifies**; NLI
  model chosen over 3 alternatives on the real multi-outcome test cases
  (only xsmall scored 25/25).
- **2026-08-25–26** Verbatim Polymarket puller; Google archive pass with
  date operators (turned long-standing wrong sports markets correct);
  **default-outcome-as-verdict removed** after 2 wrong verdicts.
- **2026-09-02** Corroboration rolled out across all decide paths; 14-market
  audit found structural gaps; description-over-title lesson.
- **2026-09-07** 97-market batch (18 / 1 / 74); owner clarifies niche-market
  target and sequencing.
- **2026-09-08** **Bing User-Agent silent failure found** (evidence had been
  headlines only); description-first strategy; availability/outcome split
  designed and shipped (`resolution_spec.py`, dashboard column).
- **2026-09-16** Elimination corroboration; binary NO path; eval cache saves
  per market; `market_provider` null-close_date crash fixed; fresh 68-market
  Polymarket batch.
- **2026-09-17** "vs." sentence-split bug (2 wrongs → 0); **Fed structured
  resolver** (14/14); licensing research (crypto, ESPN, Congress); triage;
  Congress resolver outline; this handoff.

Recurring lesson: **the biggest wins were found by reading real data**
(descriptions, raw feeds, actual API responses) rather than by tuning
thresholds.

---

## 15. Documentation index

**Specs** (`docs/superpowers/specs/`)
- `2026-08-10-resolution-finder-design.md` — original design: goals, non-goals, architecture, tiers.
- `2026-09-17-fed-decision-resolver-design.md` — Fed resolver spec, real page structures.

**Plans & notes** (`docs/superpowers/plans/`)
- `2026-08-10-resolution-finder-scanner.md` — the original task-by-task plan (Tasks 1–23+), plus a long Backlog section with per-feature histories and verification records (e.g. Tier 2 domain checks, submarket price-history requirements).
- `2026-08-25-unsupported-market-types.md` — running tracker of market shapes the engine can't handle, with real examples, audits, and status notes.
- `2026-09-07-opus-briefing.md`, `2026-09-07-opus-plan.md` — mid-project review and prioritized plan (binary NO path, eval cache, etc.).
- `2026-09-08-description-first-strategy.md` — the "descriptions are the spec" analysis.
- `2026-09-08-resolution-availability-design.md` — availability vs outcome, three signals, coverage numbers.
- `2026-09-08-opus-followup-notes.md` — design answers (elimination corroboration ordering, availability split).
- `2026-09-16-opus-session-agenda.md` — sharpened priority, open questions Q1–Q5, fresh-batch results, what got done.
- `2026-09-17-fed-decision-resolver.md` — the executed Fed implementation plan.
- `2026-09-17-price-markets-handoff.md` — crypto/gold/stock markets: shapes, parsing rules, licensing table, proxy+margin approach, reopening requirement.
- `2026-09-17-remaining-work-triage.md` — every remaining item checked against licensing texts and measurements.
- `2026-09-17-congress-resolver-handoff.md` — Congress resolver outline, verified API facts, traps.

**Also in the zip (not in git)**
- `.superpowers/sdd/2026-08-10-resolution-finder-scanner/` — original task briefs, task reports, final review, and `progress.md` (a detailed session log through 2026-09-02).
- `scratchpad/` — one-off diagnostic scripts (NLI profiling, model comparisons, batch curation, feasibility probes). Disposable; findings live in commits and docs.

---

## 16. Working with the project owner

These are the owner's stated preferences from the build sessions:

- **Plan on Opus, implement on Sonnet.** Say plainly when a design is done
  and it's time to switch. Flag when a "mechanical" task is really a design
  decision.
- **Don't spawn subagents to save tokens.** They start cold and burned more
  tokens than inline work. Ask which model/effort to use instead.
- **Inside an agreed test-find-fix loop, don't ask before each fix.** Fix
  with TDD, verify, keep going, summarize when asked. Still stop for genuine
  new architecture decisions or when the safe scope turns out narrower than
  asked.
- **Ask before anything outward-facing or hard to reverse** (pushing,
  deleting, publishing, contacting a service with the owner's identity).
- The owner often dictates by voice; messages can be fragmentary. Confirm
  intent on anything ambiguous before destructive or large actions.
- Commit messages in this repo end with a `Co-Authored-By: Claude <model>`
  line naming the model that did the work.

---

## 17. Glossary

- **Binary market** — Yes/No question; `options == []`.
- **Multi-outcome market** — pick one of several options.
- **Cumulative date-threshold market** — options like "by Aug 1 / by Sep 1";
  an earlier yes implies later yeses.
- **Availability** — whether a resolution is ready to look for (dates).
- **Outcome / verdict** — what the market resolves to.
- **Structured resolver** — reads an official source directly; claims the
  market (no news fallback).
- **News path** — search → extract → rank → verdict engine.
- **Corroboration** — ≥ 2 distinct publisher domains confirming.
- **NLI** — natural language inference: does a sentence entail a hypothesis.
- **Hedge** — speculative or negating wording that disqualifies a sentence
  as confirmation.
- **Context conflict** — evidence about a different year/season/phase/
  competition than the market's.
- **UNCLEAR / NO_EVIDENCE** — abstentions: evidence exists but doesn't
  settle it / nothing relevant found or not yet available.
- **Tier 1 / primary** — the market's named source. **Tier 2** — credible
  outlets whitelist (`credible_backup`, `credible_backup_secondary`).
- **Eval cache / replay** — frozen retrieval inputs so code changes are
  compared on identical evidence.
