# Resolution Finder — Design Spec

Date: 2026-08-10

## Purpose

RainTrade runs prediction markets (binary and multi-outcome) that need to be
settled once their real-world outcome is known. Today that requires a human
to manually research each unresolved market. Resolution Finder automates the
*research* step: it scans open and closed-but-unresolved markets, searches
for credible evidence of the real-world outcome, and produces a proposed
verdict with citations for a human reviewer to confirm or reject. It never
settles a market itself.

## Goals

- Cover all unresolved markets: still-open (trading) and closed-but-not-yet-settled.
- Run for $0 marginal cost — no paid APIs, no LLM required for v1.
- Surface a proposed verdict + evidence for every market where evidence exists;
  clearly mark markets where nothing was found rather than guessing.
- Feed a human review queue (dashboard) — this is a decision-support tool, not
  an auto-settlement engine.
- Leave two clean extension points for later: a real market-data API, and an
  optional AI-based verdict step, neither of which should require restructuring
  the pipeline.

## Non-goals (v1)

- Auto-settling markets.
- Building per-source "navigator" scrapers that simulate multi-step browsing
  of a portal (e.g. clicking through a tracker UI). If a source's answer is on
  the page as text, we read it; if it genuinely requires multi-step navigation,
  that becomes a future per-source plugin, not v1 scope.
- Any paid API or LLM call. The verdict engine is rule-based only for v1.

## Architecture

```
Market Provider      (interface: get_unresolved_markets())
   -> Query Builder
   -> Evidence Retriever   (Tier 1: named/explicit sources; Tier 2: credible backup)
   -> Article Extractor
   -> Relevance Ranker
   -> Verdict Engine        (rule-based v1; swappable for AI later)
   -> Storage (SQLite)
   -> Dashboard (local Flask app, human review)
```

Runs on a schedule (Windows Task Scheduler invoking a script every few hours
or daily — no long-running service needed at this scale). The dashboard is a
separate small local web app started on demand for review.

## Components

### Market Provider

Interface: `get_unresolved_markets() -> List[Market]`, where `Market` has
`id, title, description, options (list, empty for binary), close_date`.

V1 implementation reads a manually-maintained local JSON file (you fill in
the 62 markets by hand). This is intentionally the same interface a future
RainTrade API client will implement — nothing downstream changes when that
swap happens.

### Query Builder

Extracts search terms from `title` + `description`: quoted proper nouns and
named entities (via a small free NER model), the resolution deadline, any
named official source mentioned in the text (see Evidence Retriever), and —
for multi-outcome markets — each option name individually. Produces 1–3
search queries per market, plus one per option where applicable. Also pulls
useful terms out of any navigation/explanation text in the description (e.g.
"check its status on Congress.gov") to sharpen the Tier 1 fetch/search target.

### Evidence Retriever

Two source tiers, each result tagged with its tier for the dashboard:

- **Tier 1 — named/explicit sources** (`source_type: primary`, highest
  confidence). The description is scanned for:
  - Literal URLs — fetched directly.
  - Named organizations/domains without a URL (e.g. "Congress.gov", "Norwegian
    Nobel Committee") — mapped via a small, growable config table to a base
    domain, then fetched or searched scoped to that domain (`site:` operator).
- **Tier 2 — credible backup sources** (`source_type: credible_backup`, medium
  confidence). Used when Tier 1 has nothing yet, or the criteria simply says
  "consensus of credible reporting." Searches Google News RSS (free, no API
  key) filtered/preferred to a curated outlet whitelist, default: Reuters, AP,
  BBC, AFP, NPR — kept in an editable config file.

If both tiers return nothing, the market is recorded as `NO_EVIDENCE` — the
pipeline does not fall back further or guess.

### Article Extractor

Fetches each candidate URL (from either tier) and extracts clean article
text. Failures (timeout, 404, paywall) are logged and skipped; they don't
fail the market or the run.

### Relevance Ranker

Embeds the market's title+description once using a local, free embedding
model (runs offline, no API), embeds each candidate article/page text, and
ranks by cosine similarity. Only candidates above a configurable similarity
threshold move on to the Verdict Engine. This is the main NLP filtering step
that keeps noise out before any verdict logic runs.

### Verdict Engine (v1: rule-based)

Interface: `decide(market, ranked_evidence) -> Verdict(outcome, confidence,
evidence_snippet, source_url, source_type)`.

V1 logic:
- Binary markets: keyword/date-proximity checks against the criteria (e.g.
  "signed into law" appearing near the bill number and a date before the
  deadline -> YES).
- Multi-outcome markets: checks which option's name appears in an
  announcement-type sentence in the top evidence (e.g. "awarded to", "wins",
  "named recipient").
- Deadline-aware default: if the deadline has passed with no matching
  evidence *and* the criteria explicitly states a default-if-missed outcome
  (as in the CLARITY Act example), the engine proposes that default instead
  of leaving the market `UNCLEAR` forever.
- Otherwise: `UNCLEAR` if evidence exists but isn't decisive, `NO_EVIDENCE` if
  nothing was found at all.

This engine sits behind an interface so a future `LLMVerdictEngine` (using a
free-tier or cheap paid model) can be dropped in later without touching
anything upstream — no restructuring required.

### Storage

SQLite, one row per (market, run): market id, run timestamp, verdict,
confidence, evidence (JSON: list of `{url, snippet, source_type, published_date}`),
and a `review_status` column (Pending / Confirmed / Rejected) that the
dashboard updates. Re-runs insert new rows rather than overwriting, so history
is preserved; the dashboard shows the latest run per market by default.

### Dashboard

Small local Flask app. Lists latest finding per market, sorted so markets
with a proposed verdict surface above `NO_EVIDENCE` ones. Each row expands to
show evidence snippets, source links, and source tier. Buttons let the team
set `review_status`. Read access to run history per market.

## Error Handling

- Per-article network/scrape failures: log and skip, never fail the whole run.
- No evidence at all: recorded as `NO_EVIDENCE`, distinct from "not yet checked."
- Courtesy rate-limiting: small delay between requests to any single domain.
- Deadline-passed-with-no-evidence: handled by the Verdict Engine's
  deadline-aware default (see above), only when the criteria states one.

## Testing

- Unit tests for Query Builder and Verdict Engine using the CLARITY Act and
  Nobel Peace Prize examples as fixtures.
- Retriever/Extractor tested against recorded/mocked HTTP responses, not live
  network calls.
- Manual end-to-end dry run against the real 62 markets once the local JSON
  file is filled in, with spot-checks against known outcomes.

## Future Extensions (explicitly out of scope for v1)

- Swap Market Provider's stub for a real RainTrade API client.
- Add an `LLMVerdictEngine` (free-tier or cheap paid model) behind the
  existing Verdict Engine interface for cases the rule-based engine marks
  `UNCLEAR`.
- Per-source navigation plugins for official sources that require multi-step
  browsing rather than a single page fetch.
- **Official X (Twitter) accounts as a Tier 1 source.** Posts from an
  organization's official account (e.g. the Nobel Committee's or a
  government body's verified account) are a legitimate, high-value
  resolution signal. This is documented here as a known-valuable source
  type, not built: X's API has no usable free tier (paid plans start
  around $100/month), and scraping x.com directly is unreliable and
  against its terms of service. Given the project's zero-cost constraint,
  this is unlikely to ever be implemented unless that changes — treat it
  as a placeholder for future manual reference during human review, not a
  planned automation target.
