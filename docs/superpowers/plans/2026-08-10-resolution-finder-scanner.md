# Resolution Finder Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a scheduled scanner that checks RainTrade's unresolved markets against free news/official sources, produces a rule-based proposed verdict with evidence, and surfaces it in a local review dashboard.

**Architecture:** A Python pipeline (Market Provider → Query Builder → tiered Evidence Retriever → Article Extractor → Relevance Ranker → rule-based Verdict Engine → SQLite Storage) run on a schedule, plus a small Flask dashboard for human review. Market Provider and Verdict Engine are built behind interfaces so a real RainTrade API and/or an AI-based verdict step can be swapped in later without touching the rest of the pipeline.

**Tech Stack:** Python 3.11+, `requests`, `feedparser` (Google News RSS), `trafilatura` (article text extraction), `sentence-transformers` (local embedding similarity), `spacy` (`en_core_web_sm`, entity extraction), `flask` (dashboard), `sqlite3` (stdlib), `pytest`.

## Global Constraints

- Zero marginal cost: no paid APIs, no LLM calls anywhere in this plan (spec: "Run for $0 marginal cost — no paid APIs, no LLM required for v1").
- Market Provider must be swappable for a real API with zero changes downstream (spec: "nothing downstream changes when that swap happens").
- Verdict Engine must be swappable for an AI-based engine with zero changes upstream (spec: "no restructuring required").
- No per-source navigation scrapers (multi-step browsing simulation) in this plan — a single page fetch + text extraction only (spec Non-goals).
- This tool never auto-settles a market. It only ever writes proposed verdicts to a review queue.
- Official X (Twitter) accounts: the actual x.com page is NEVER fetched directly anywhere in this plan (no X API, no scraping x.com) — the real X API has no free tier and scraping violates its ToS. Task 6 does perform a best-effort, zero-cost Google News RSS search scoped to `site:x.com` when `TIER1_SOCIAL_ACCOUNTS` (Task 5) has a known handle for the market's named organization; only the search result's own title/snippet is used as evidence, tagged `source_type: official_social`, and the dashboard (Task 11) must flag it for manual verification rather than presenting it as confirmed evidence.

---

## File Structure

```
Resolution Finder/
  requirements.txt
  run_scan.py
  resolution_finder/
    __init__.py
    models.py             # Market, ArticleRef, RankedArticle, Verdict
    config.py              # constants: paths, thresholds, source lists, delay
    storage.py              # SQLite read/write for findings
    market_provider.py      # MarketProvider interface + JsonFileMarketProvider
    query_builder.py        # URL/entity extraction, query generation
    source_config.py        # Tier 1 domain map, Tier 2 outlet whitelist, resolve_named_source
    evidence_retriever.py   # Google News RSS search, Tier 1/Tier 2 retrieval
    article_extractor.py    # fetch + clean-text extraction
    relevance_ranker.py     # embedding similarity ranking
    verdict_engine.py       # rule-based binary/multi-outcome decision logic
    pipeline.py             # wires everything together, one run
    dashboard.py            # Flask app
    templates/
      index.html
  data/
    markets.json            # manually maintained market list (stub Market Provider source)
  tests/
    test_models.py
    test_storage.py
    test_market_provider.py
    test_query_builder.py
    test_source_config.py
    test_evidence_retriever.py
    test_article_extractor.py
    test_relevance_ranker.py
    test_verdict_engine.py
    test_pipeline.py
    test_dashboard.py
```

Each module has one responsibility and depends only on modules earlier in the pipeline. `pipeline.py` is the only place that imports every stage.

---

### Task 1: Project scaffolding and data models

**Files:**
- Create: `requirements.txt`
- Create: `resolution_finder/__init__.py`
- Create: `resolution_finder/models.py`
- Create: `resolution_finder/config.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `Market(id, title, description, options, close_date)`, `ArticleRef(url, title, source_type, published_date=None)`, `RankedArticle(article, text, similarity)`, `Verdict(outcome, confidence, evidence_snippet, source_url, source_type)` dataclasses. Config constants: `DB_PATH`, `MARKETS_JSON_PATH`, `SIMILARITY_THRESHOLD`, `EMBEDDING_MODEL_NAME`, `SPACY_MODEL_NAME`, `REQUEST_DELAY_SECONDS`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from datetime import date
from resolution_finder.models import Market, ArticleRef, RankedArticle, Verdict


def test_market_creation():
    market = Market(
        id="clarity-act-2026",
        title="Will the CLARITY act be signed into law in 2026?",
        description="This market resolves to \"Yes\" if...",
        options=[],
        close_date=date(2026, 12, 31),
    )
    assert market.id == "clarity-act-2026"
    assert market.options == []


def test_article_ref_defaults():
    ref = ArticleRef(url="https://example.com/a", title="A", source_type="primary")
    assert ref.published_date is None
    assert ref.summary is None


def test_verdict_creation():
    v = Verdict(outcome="YES", confidence=0.9, evidence_snippet="signed into law",
                source_url="https://congress.gov/x", source_type="primary")
    assert v.outcome == "YES"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resolution_finder'`

- [ ] **Step 3: Create the package and requirements file**

```
# requirements.txt
requests
feedparser
trafilatura
sentence-transformers
spacy
flask
pytest
```

```python
# resolution_finder/__init__.py
```

- [ ] **Step 4: Implement the data models**

```python
# resolution_finder/models.py
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class Market:
    id: str
    title: str
    description: str
    options: list[str]
    close_date: date


@dataclass
class ArticleRef:
    url: str
    title: str
    source_type: str  # "primary", "credible_backup", "official_social", or "general"
    published_date: Optional[date] = None
    summary: Optional[str] = None  # search-result snippet text; used in place of a
                                    # fetched page for "official_social" refs, since
                                    # those URLs are never fetched directly


@dataclass
class RankedArticle:
    article: ArticleRef
    text: str
    similarity: float


@dataclass
class Verdict:
    outcome: str  # "YES", "NO", an option name, "UNCLEAR", or "NO_EVIDENCE"
    confidence: float
    evidence_snippet: Optional[str]
    source_url: Optional[str]
    source_type: Optional[str]
```

- [ ] **Step 5: Implement config**

```python
# resolution_finder/config.py
DB_PATH = "data/resolution_finder.db"
MARKETS_JSON_PATH = "data/markets.json"
SIMILARITY_THRESHOLD = 0.35
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
SPACY_MODEL_NAME = "en_core_web_sm"
REQUEST_DELAY_SECONDS = 1
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Install dependencies and the spaCy model**

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

> **Superseded by Task 4:** on this environment, `import spacy` itself fails
> (an Application Control policy blocks a DLL inside numpy that spaCy's
> parser pulls in at import time — not just the model download). Task 4
> replaces spaCy with a regex heuristic and removes this dependency; if the
> download above fails, proceed anyway and let Task 4's fix land.

- [ ] **Step 8: Commit**

```bash
git add requirements.txt resolution_finder/__init__.py resolution_finder/models.py resolution_finder/config.py tests/test_models.py
git commit -m "feat: add core data models and config"
```

---

### Task 2: Storage layer

**Files:**
- Create: `resolution_finder/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `Verdict` (Task 1).
- Produces: `init_db(db_path)`, `save_finding(db_path, market_id, run_timestamp, verdict) -> int`, `get_latest_findings(db_path) -> list[dict]`, `get_history(db_path, market_id) -> list[dict]`, `set_review_status(db_path, finding_id, status)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage.py
import os
import tempfile
from resolution_finder.models import Verdict
from resolution_finder.storage import (
    init_db, save_finding, get_latest_findings, get_history, set_review_status,
)


def make_temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    init_db(path)
    return path


def test_save_and_get_latest_finding():
    db_path = make_temp_db()
    verdict = Verdict(outcome="YES", confidence=0.8, evidence_snippet="signed into law",
                       source_url="https://congress.gov/x", source_type="primary")
    finding_id = save_finding(db_path, "clarity-act-2026", "2026-08-10T00:00:00", verdict)
    assert isinstance(finding_id, int)

    latest = get_latest_findings(db_path)
    assert len(latest) == 1
    assert latest[0]["market_id"] == "clarity-act-2026"
    assert latest[0]["outcome"] == "YES"
    assert latest[0]["review_status"] == "Pending"
    os.remove(db_path)


def test_latest_finding_is_most_recent_run():
    db_path = make_temp_db()
    v1 = Verdict(outcome="UNCLEAR", confidence=0.2, evidence_snippet=None,
                 source_url=None, source_type=None)
    v2 = Verdict(outcome="YES", confidence=0.9, evidence_snippet="signed",
                 source_url="https://congress.gov/x", source_type="primary")
    save_finding(db_path, "clarity-act-2026", "2026-08-10T00:00:00", v1)
    save_finding(db_path, "clarity-act-2026", "2026-08-10T06:00:00", v2)

    latest = get_latest_findings(db_path)
    assert len(latest) == 1
    assert latest[0]["outcome"] == "YES"

    history = get_history(db_path, "clarity-act-2026")
    assert len(history) == 2
    os.remove(db_path)


def test_set_review_status():
    db_path = make_temp_db()
    verdict = Verdict(outcome="NO", confidence=0.7, evidence_snippet="not signed",
                       source_url="https://reuters.com/x", source_type="credible_backup")
    finding_id = save_finding(db_path, "clarity-act-2026", "2026-08-10T00:00:00", verdict)
    set_review_status(db_path, finding_id, "Confirmed")

    latest = get_latest_findings(db_path)
    assert latest[0]["review_status"] == "Confirmed"
    os.remove(db_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resolution_finder.storage'`

- [ ] **Step 3: Implement storage**

```python
# resolution_finder/storage.py
import sqlite3
from resolution_finder.models import Verdict

SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    run_timestamp TEXT NOT NULL,
    outcome TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_snippet TEXT,
    source_url TEXT,
    source_type TEXT,
    review_status TEXT NOT NULL DEFAULT 'Pending'
);
"""


def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def save_finding(db_path: str, market_id: str, run_timestamp: str, verdict: Verdict) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO findings
                (market_id, run_timestamp, outcome, confidence, evidence_snippet,
                 source_url, source_type, review_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending')
            """,
            (market_id, run_timestamp, verdict.outcome, verdict.confidence,
             verdict.evidence_snippet, verdict.source_url, verdict.source_type),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "market_id": row["market_id"],
        "run_timestamp": row["run_timestamp"],
        "outcome": row["outcome"],
        "confidence": row["confidence"],
        "evidence_snippet": row["evidence_snippet"],
        "source_url": row["source_url"],
        "source_type": row["source_type"],
        "review_status": row["review_status"],
    }


def get_latest_findings(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT f.* FROM findings f
            INNER JOIN (
                SELECT market_id, MAX(run_timestamp) AS max_ts
                FROM findings GROUP BY market_id
            ) latest
            ON f.market_id = latest.market_id AND f.run_timestamp = latest.max_ts
            ORDER BY f.confidence DESC
            """
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_history(db_path: str, market_id: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM findings WHERE market_id = ? ORDER BY run_timestamp DESC",
            (market_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def set_review_status(db_path: str, finding_id: int, status: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE findings SET review_status = ? WHERE id = ?", (status, finding_id)
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_storage.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add resolution_finder/storage.py tests/test_storage.py
git commit -m "feat: add SQLite storage layer for findings"
```

---

### Task 3: Market Provider

**Files:**
- Create: `resolution_finder/market_provider.py`
- Create: `data/markets.json`
- Test: `tests/test_market_provider.py`

**Interfaces:**
- Consumes: `Market` (Task 1).
- Produces: `MarketProvider` (protocol), `JsonFileMarketProvider(json_path).get_unresolved_markets() -> list[Market]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_market_provider.py
import json
import os
import tempfile
from resolution_finder.market_provider import JsonFileMarketProvider


def make_temp_markets_file(markets):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(markets, f)
    return path


def test_loads_binary_market():
    path = make_temp_markets_file([
        {
            "id": "m1",
            "title": "Will X happen?",
            "description": "Resolves Yes if X happens by 2026-12-31.",
            "options": [],
            "close_date": "2026-12-31",
        }
    ])
    provider = JsonFileMarketProvider(path)
    markets = provider.get_unresolved_markets()
    assert len(markets) == 1
    assert markets[0].id == "m1"
    assert markets[0].options == []
    os.remove(path)


def test_loads_multi_outcome_market():
    path = make_temp_markets_file([
        {
            "id": "m2",
            "title": "Who will win?",
            "description": "Resolves based on winner.",
            "options": ["A", "B"],
            "close_date": "2027-01-01",
        }
    ])
    provider = JsonFileMarketProvider(path)
    markets = provider.get_unresolved_markets()
    assert markets[0].options == ["A", "B"]
    os.remove(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_market_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resolution_finder.market_provider'`

- [ ] **Step 3: Implement the market provider**

```python
# resolution_finder/market_provider.py
import json
from datetime import date
from typing import Protocol
from resolution_finder.models import Market


class MarketProvider(Protocol):
    def get_unresolved_markets(self) -> list[Market]: ...


class JsonFileMarketProvider:
    def __init__(self, json_path: str):
        self.json_path = json_path

    def get_unresolved_markets(self) -> list[Market]:
        with open(self.json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return [
            Market(
                id=item["id"],
                title=item["title"],
                description=item["description"],
                options=item.get("options", []),
                close_date=date.fromisoformat(item["close_date"]),
            )
            for item in raw
        ]
```

- [ ] **Step 4: Create the real markets file with your two known examples**

```json
[
  {
    "id": "clarity-act-2026",
    "title": "Will the CLARITY act be signed into law in 2026?",
    "description": "This market resolves to \"Yes\" if the Digital Asset Market Clarity Act of 2025 (H.R. 3633) is approved by both the U.S. House of Representatives and the U.S. Senate, and is signed into law no later than December 31, 2026, at 11:59 PM ET. If these conditions are not met by the deadline, the market resolves to \"No\". The primary resolution source will be the legislation tracker on Congress.gov, along with other official information published by the United States government. If necessary, a consensus of credible reporting may also be used to determine the outcome.",
    "options": [],
    "close_date": "2026-12-31"
  },
  {
    "id": "nobel-peace-2026",
    "title": "Who will win the 2026 Nobel Peace Prize?",
    "description": "This market will be settled based on the recipient of the 2026 Nobel Peace Prize officially announced by the Norwegian Nobel Committee. The listed person or entity that receives the 2026 Nobel Peace Prize will resolve to \"Yes\", and all other listed outcomes will resolve to \"No\". If multiple listed individuals or entities jointly receive the prize, the listed individual whose last name, or the entity whose name, comes first alphabetically will resolve to \"Yes\", and all other listed recipients will resolve to \"No\". If none of the listed individuals or entities receive the prize, all listed outcomes will resolve to \"No\". If the 2026 Nobel Peace Prize has not been officially announced by March 31, 2027, 11:59 PM ET, all listed outcomes will resolve to \"No\". The market will use the first official announcement issued by the Norwegian Nobel Committee as its resolution source.",
    "options": ["Yulia Navalnaya", "Volodymyr Zelenskyy", "UNRWA", "Pope Leo XIV", "Donald Trump"],
    "close_date": "2027-03-31"
  }
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_market_provider.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add resolution_finder/market_provider.py data/markets.json tests/test_market_provider.py
git commit -m "feat: add JSON-file-backed market provider"
```

---

### Task 4: Query Builder

> **Note (added after Task 1):** the plan originally used spaCy for entity
> extraction. This environment's Application Control policy blocks a DLL
> deep inside numpy that spaCy's parser imports at load time (`spacy.load`
> never even gets called — plain `import spacy` fails), so spaCy cannot run
> here at all. This is unrelated to the numpy/torch stack `sentence-transformers`
> uses in Task 8, which was verified working end-to-end. This task now uses a
> small regex-based heuristic instead (captures runs of 2+ capitalized words,
> allowing "of/the/for/and" as a joining word — e.g. matches "U.S. House of
> Representatives" as one entity) and removes the now-unnecessary spaCy
> dependency added in Task 1.

**Files:**
- Create: `resolution_finder/query_builder.py`
- Modify: `requirements.txt` (remove the `spacy` line added in Task 1)
- Modify: `resolution_finder/config.py` (remove the `SPACY_MODEL_NAME` constant added in Task 1 — nothing else uses it)
- Test: `tests/test_query_builder.py`

**Interfaces:**
- Consumes: `Market` (Task 1).
- Produces: `extract_urls(text: str) -> list[str]`, `build_queries(market: Market) -> list[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_query_builder.py
from datetime import date
from resolution_finder.models import Market
from resolution_finder.query_builder import extract_urls, build_queries

CLARITY_MARKET = Market(
    id="clarity-act-2026",
    title="Will the CLARITY act be signed into law in 2026?",
    description=(
        "This market resolves to \"Yes\" if the Digital Asset Market Clarity Act "
        "of 2025 (H.R. 3633) is approved by both the U.S. House of Representatives "
        "and the U.S. Senate. The primary resolution source will be the legislation "
        "tracker on https://www.congress.gov/bill/119th-congress/house-bill/3633."
    ),
    options=[],
    close_date=date(2026, 12, 31),
)

NOBEL_MARKET = Market(
    id="nobel-peace-2026",
    title="Who will win the 2026 Nobel Peace Prize?",
    description="This market will be settled by the Norwegian Nobel Committee.",
    options=["Yulia Navalnaya", "Volodymyr Zelenskyy", "UNRWA", "Pope Leo XIV", "Donald Trump"],
    close_date=date(2027, 3, 31),
)


def test_extract_urls_finds_congress_link_without_trailing_period():
    urls = extract_urls(CLARITY_MARKET.description)
    assert urls == ["https://www.congress.gov/bill/119th-congress/house-bill/3633"]


def test_build_queries_includes_title():
    queries = build_queries(CLARITY_MARKET)
    assert CLARITY_MARKET.title in queries


def test_build_queries_includes_each_option():
    queries = build_queries(NOBEL_MARKET)
    for option in NOBEL_MARKET.options:
        assert any(option in q for q in queries)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_query_builder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resolution_finder.query_builder'`

- [ ] **Step 3: Remove the now-unnecessary spaCy dependency from Task 1**

Edit `requirements.txt` and delete the `spacy` line.

Edit `resolution_finder/config.py` and delete the `SPACY_MODEL_NAME = "en_core_web_sm"` line — nothing else in the codebase references it.

- [ ] **Step 4: Implement the query builder**

```python
# resolution_finder/query_builder.py
import re
from resolution_finder.models import Market

URL_PATTERN = re.compile(r"https?://[^\s)]+")

# Heuristic proper-noun matcher: a capitalized word, followed by one or more
# more capitalized words optionally joined by a short lowercase connector
# ("of", "the", "for", "and") — e.g. matches "U.S. House of Representatives"
# and "Digital Asset Market Clarity Act" as single entities. This replaces
# spaCy (see note above this task) with a dependency-free approximation; it
# is intentionally simple and only used to enrich search queries, not to
# make resolution decisions.
ENTITY_PATTERN = re.compile(
    r"\b[A-Z][a-zA-Z0-9.]*(?:\s+(?:of|the|for|and)?\s*[A-Z][a-zA-Z0-9.]*)+\b"
)


def extract_urls(text: str) -> list[str]:
    raw = URL_PATTERN.findall(text)
    return [u.rstrip(".,;:)") for u in raw]


def extract_entities(text: str) -> list[str]:
    seen = []
    for match in ENTITY_PATTERN.finditer(text):
        candidate = match.group().strip()
        if candidate not in seen:
            seen.append(candidate)
    return seen


def build_queries(market: Market) -> list[str]:
    queries = [market.title]
    entities = extract_entities(market.description)
    if entities:
        queries.append(" ".join(entities[:3]) + " " + market.title.split("?")[0])
    for option in market.options:
        queries.append(f"{option} {market.title}")
    return queries
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_query_builder.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add requirements.txt resolution_finder/config.py resolution_finder/query_builder.py tests/test_query_builder.py
git commit -m "feat: add query builder using regex entity extraction (spaCy unusable in this environment)"
```

---

### Task 5: Source config and named-source resolution

**Files:**
- Create: `resolution_finder/source_config.py`
- Test: `tests/test_source_config.py`

**Interfaces:**
- Consumes: `extract_urls` (Task 4).
- Produces: `TIER1_DOMAINS: dict`, `TIER2_OUTLETS: list[str]`, `TIER1_SOCIAL_ACCOUNTS: dict`, `resolve_named_source(description: str) -> Optional[str]` (returns a literal URL, a domain string, or `None`), `resolve_social_handle(description: str) -> Optional[str]` (returns an `@handle` string or `None`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_source_config.py
from resolution_finder.source_config import resolve_named_source, resolve_social_handle


def test_resolves_literal_url_first():
    description = "See https://www.congress.gov/bill/119th-congress/house-bill/3633 for status."
    assert resolve_named_source(description) == "https://www.congress.gov/bill/119th-congress/house-bill/3633"


def test_resolves_named_organization_to_domain():
    description = (
        "This market will be settled based on the recipient of the 2026 Nobel "
        "Peace Prize officially announced by the Norwegian Nobel Committee."
    )
    assert resolve_named_source(description) == "nobelprize.org"


def test_returns_none_when_no_named_source():
    description = "This market resolves based on consensus of credible reporting."
    assert resolve_named_source(description) is None


def test_resolves_social_handle_for_known_organization():
    description = "Officially announced by the Norwegian Nobel Committee."
    assert resolve_social_handle(description) == "@NobelPrize"


def test_resolves_social_handle_returns_none_when_unknown():
    description = "This market resolves based on consensus of credible reporting."
    assert resolve_social_handle(description) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_source_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resolution_finder.source_config'`

- [ ] **Step 3: Implement source config**

```python
# resolution_finder/source_config.py
from typing import Optional
from resolution_finder.query_builder import extract_urls

TIER1_DOMAINS = {
    "congress.gov": "congress.gov",
    "u.s. congress": "congress.gov",
    "house of representatives": "congress.gov",
    "norwegian nobel committee": "nobelprize.org",
    "nobel committee": "nobelprize.org",
    "sec.gov": "sec.gov",
    "securities and exchange commission": "sec.gov",
}

TIER2_OUTLETS = [
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "afp.com",
    "npr.org",
]

# Official X/Twitter handles for named organizations. Used only to build a
# site-scoped search query (see evidence_retriever.py) — the actual x.com page
# is NEVER fetched. This is a best-effort, zero-cost lookup: it relies on
# Google News RSS occasionally indexing a post, which is not guaranteed since
# News RSS is scoped to news publishers, not general web/social content. Any
# hit is surfaced to the reviewer as a link to check by hand, never treated as
# confirmed evidence on its own.
TIER1_SOCIAL_ACCOUNTS = {
    "congress.gov": "@HouseFloor",
    "norwegian nobel committee": "@NobelPrize",
    "sec.gov": "@SECGov",
}


def resolve_named_source(description: str) -> Optional[str]:
    urls = extract_urls(description)
    if urls:
        return urls[0]
    lowered = description.lower()
    for phrase, domain in TIER1_DOMAINS.items():
        if phrase in lowered:
            return domain
    return None


def resolve_social_handle(description: str) -> Optional[str]:
    lowered = description.lower()
    for phrase, handle in TIER1_SOCIAL_ACCOUNTS.items():
        if phrase in lowered:
            return handle
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_source_config.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add resolution_finder/source_config.py tests/test_source_config.py
git commit -m "feat: add tiered source config, named-source and social-handle resolution"
```

---

### Task 6: Evidence Retriever

**Files:**
- Create: `resolution_finder/evidence_retriever.py`
- Test: `tests/test_evidence_retriever.py`

**Interfaces:**
- Consumes: `Market`, `ArticleRef` (Task 1); `resolve_named_source`, `resolve_social_handle`, `TIER2_OUTLETS` (Task 5); `REQUEST_DELAY_SECONDS` (Task 1 config).
- Produces: `search_google_news_rss(query, site=None) -> list[ArticleRef]`, `retrieve_evidence(market, queries) -> list[ArticleRef]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_retriever.py
from unittest.mock import patch, MagicMock
from datetime import date
from resolution_finder.models import Market
from resolution_finder.evidence_retriever import search_google_news_rss, retrieve_evidence

CLARITY_MARKET = Market(
    id="clarity-act-2026",
    title="Will the CLARITY act be signed into law in 2026?",
    description="Primary resolution source: Congress.gov legislation tracker.",
    options=[],
    close_date=date(2026, 12, 31),
)


def make_fake_feed(entries):
    feed = MagicMock()
    feed.entries = entries
    return feed


def make_entry(link, title):
    entry = MagicMock()
    entry.link = link
    entry.title = title
    return entry


@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_google_news_rss_tags_whitelisted_source(mock_parse):
    mock_parse.return_value = make_fake_feed([
        make_entry("https://www.reuters.com/article/x", "Reuters headline"),
        make_entry("https://randomblog.com/article/y", "Random headline"),
    ])
    results = search_google_news_rss("CLARITY act")
    assert results[0].source_type == "credible_backup"
    assert results[1].source_type == "general"


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_uses_tier1_domain_scoped_search(mock_parse, mock_sleep):
    mock_parse.return_value = make_fake_feed([
        make_entry("https://www.congress.gov/bill/3633", "Bill status"),
    ])
    evidence = retrieve_evidence(CLARITY_MARKET, ["CLARITY act"])
    assert any(e.source_type == "primary" for e in evidence)


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_deduplicates_urls(mock_parse, mock_sleep):
    mock_parse.return_value = make_fake_feed([
        make_entry("https://www.reuters.com/article/x", "Reuters headline"),
    ])
    evidence = retrieve_evidence(CLARITY_MARKET, ["CLARITY act", "CLARITY act status"])
    urls = [e.url for e in evidence]
    assert len(urls) == len(set(urls))


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_includes_social_search_for_known_organization(mock_parse, mock_sleep):
    nobel_market = Market(
        id="nobel-peace-2026",
        title="Who will win the 2026 Nobel Peace Prize?",
        description="Officially announced by the Norwegian Nobel Committee.",
        options=["Pope Leo XIV"],
        close_date=date(2027, 3, 31),
    )
    mock_parse.side_effect = [
        make_fake_feed([]),  # Tier 1 domain-scoped search (nobelprize.org)
        make_fake_feed([make_entry(
            "https://x.com/NobelPrize/status/123",
            "NobelPrize: The 2026 laureate is...",
        )]),  # social search, scoped to x.com
        make_fake_feed([]),  # Tier 2 general search
    ]

    evidence = retrieve_evidence(nobel_market, ["Nobel Peace Prize winner"])

    social_hits = [e for e in evidence if e.source_type == "official_social"]
    assert len(social_hits) == 1
    assert social_hits[0].url == "https://x.com/NobelPrize/status/123"
    assert social_hits[0].summary == "NobelPrize: The 2026 laureate is..."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_evidence_retriever.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resolution_finder.evidence_retriever'`

- [ ] **Step 3: Implement the evidence retriever**

```python
# resolution_finder/evidence_retriever.py
import time
from typing import Optional
from urllib.parse import quote_plus
import feedparser
from resolution_finder.models import Market, ArticleRef
from resolution_finder.source_config import resolve_named_source, resolve_social_handle, TIER2_OUTLETS
from resolution_finder.config import REQUEST_DELAY_SECONDS

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def _is_whitelisted(url: str) -> bool:
    return any(outlet in url for outlet in TIER2_OUTLETS)


def search_google_news_rss(query: str, site: Optional[str] = None) -> list[ArticleRef]:
    full_query = f"{query} site:{site}" if site else query
    url = GOOGLE_NEWS_RSS.format(query=quote_plus(full_query))
    feed = feedparser.parse(url)
    results = []
    for entry in feed.entries:
        source_type = "credible_backup" if _is_whitelisted(entry.link) else "general"
        results.append(ArticleRef(url=entry.link, title=entry.title, source_type=source_type))
    return results


def retrieve_evidence(market: Market, queries: list[str]) -> list[ArticleRef]:
    evidence: list[ArticleRef] = []
    named_source = resolve_named_source(market.description)

    if named_source and named_source.startswith("http"):
        evidence.append(ArticleRef(url=named_source, title="Named resolution source", source_type="primary"))
    elif named_source:
        for query in queries:
            for ref in search_google_news_rss(query, site=named_source):
                evidence.append(ArticleRef(url=ref.url, title=ref.title, source_type="primary"))
            time.sleep(REQUEST_DELAY_SECONDS)

    # Best-effort only: this searches Google News RSS scoped to x.com for a
    # known official handle, but never fetches the actual X page. News RSS is
    # scoped to news publishers, so this frequently finds nothing — any hit
    # is still surfaced to the reviewer as a link to verify by hand, never
    # treated as confirmed evidence on its own (see source_config.py).
    social_handle = resolve_social_handle(market.description)
    if social_handle:
        for ref in search_google_news_rss(f"{queries[0]} {social_handle}", site="x.com"):
            evidence.append(ArticleRef(
                url=ref.url,
                title=ref.title,
                source_type="official_social",
                summary=ref.title,
            ))
        time.sleep(REQUEST_DELAY_SECONDS)

    for query in queries:
        for ref in search_google_news_rss(query):
            if ref.source_type == "credible_backup":
                evidence.append(ref)
        time.sleep(REQUEST_DELAY_SECONDS)

    seen_urls = set()
    deduped = []
    for ref in evidence:
        if ref.url not in seen_urls:
            seen_urls.add(ref.url)
            deduped.append(ref)
    return deduped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_evidence_retriever.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add resolution_finder/evidence_retriever.py tests/test_evidence_retriever.py
git commit -m "feat: add tiered evidence retriever with best-effort official-account search"
```

---

### Task 7: Article Extractor

**Files:**
- Create: `resolution_finder/article_extractor.py`
- Test: `tests/test_article_extractor.py`

**Interfaces:**
- Produces: `extract_article_text(url: str, timeout: int = 10) -> Optional[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_article_extractor.py
from unittest.mock import patch, MagicMock
import requests
from resolution_finder.article_extractor import extract_article_text


@patch("resolution_finder.article_extractor.trafilatura.extract")
@patch("resolution_finder.article_extractor.requests.get")
def test_extract_article_text_returns_clean_text(mock_get, mock_extract):
    mock_response = MagicMock()
    mock_response.text = "<html>...</html>"
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response
    mock_extract.return_value = "The bill was signed into law today."

    result = extract_article_text("https://example.com/article")
    assert result == "The bill was signed into law today."


@patch("resolution_finder.article_extractor.requests.get")
def test_extract_article_text_returns_none_on_network_error(mock_get):
    mock_get.side_effect = requests.ConnectionError("failed")
    result = extract_article_text("https://example.com/article")
    assert result is None


@patch("resolution_finder.article_extractor.trafilatura.extract")
@patch("resolution_finder.article_extractor.requests.get")
def test_extract_article_text_returns_none_when_no_content_extracted(mock_get, mock_extract):
    mock_response = MagicMock()
    mock_response.text = "<html></html>"
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response
    mock_extract.return_value = None

    result = extract_article_text("https://example.com/empty")
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_article_extractor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resolution_finder.article_extractor'`

- [ ] **Step 3: Implement the article extractor**

```python
# resolution_finder/article_extractor.py
from typing import Optional
import requests
import trafilatura


def extract_article_text(url: str, timeout: int = 10) -> Optional[str]:
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except requests.RequestException:
        return None

    text = trafilatura.extract(response.text)
    if not text or not text.strip():
        return None
    return text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_article_extractor.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add resolution_finder/article_extractor.py tests/test_article_extractor.py
git commit -m "feat: add article text extractor with failure handling"
```

---

### Task 8: Relevance Ranker

**Files:**
- Create: `resolution_finder/relevance_ranker.py`
- Test: `tests/test_relevance_ranker.py`

**Interfaces:**
- Consumes: `Market`, `ArticleRef`, `RankedArticle` (Task 1); `EMBEDDING_MODEL_NAME`, `SIMILARITY_THRESHOLD` (Task 1 config).
- Produces: `rank_by_relevance(market, articles: list[tuple[ArticleRef, str]], threshold=SIMILARITY_THRESHOLD) -> list[RankedArticle]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_relevance_ranker.py
from unittest.mock import patch, MagicMock
from datetime import date
from resolution_finder.models import Market, ArticleRef
from resolution_finder.relevance_ranker import rank_by_relevance

MARKET = Market(
    id="m1", title="Will X happen?", description="Resolves Yes if X.",
    options=[], close_date=date(2026, 12, 31),
)


@patch("resolution_finder.relevance_ranker.util.cos_sim")
@patch("resolution_finder.relevance_ranker._get_model")
def test_filters_below_threshold(mock_get_model, mock_cos_sim):
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.side_effect = [[[0.9]], [[0.1]]]

    articles = [
        (ArticleRef(url="https://a.com", title="A", source_type="primary"), "relevant text"),
        (ArticleRef(url="https://b.com", title="B", source_type="general"), "irrelevant text"),
    ]
    ranked = rank_by_relevance(MARKET, articles, threshold=0.35)
    assert len(ranked) == 1
    assert ranked[0].article.url == "https://a.com"


@patch("resolution_finder.relevance_ranker.util.cos_sim")
@patch("resolution_finder.relevance_ranker._get_model")
def test_sorts_by_similarity_descending(mock_get_model, mock_cos_sim):
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.side_effect = [[[0.5]], [[0.8]]]

    articles = [
        (ArticleRef(url="https://a.com", title="A", source_type="primary"), "text a"),
        (ArticleRef(url="https://b.com", title="B", source_type="general"), "text b"),
    ]
    ranked = rank_by_relevance(MARKET, articles, threshold=0.35)
    assert [r.article.url for r in ranked] == ["https://b.com", "https://a.com"]


def test_empty_articles_returns_empty_list():
    ranked = rank_by_relevance(MARKET, [])
    assert ranked == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_relevance_ranker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resolution_finder.relevance_ranker'`

- [ ] **Step 3: Implement the relevance ranker**

```python
# resolution_finder/relevance_ranker.py
from sentence_transformers import SentenceTransformer, util
from resolution_finder.models import Market, ArticleRef, RankedArticle
from resolution_finder.config import EMBEDDING_MODEL_NAME, SIMILARITY_THRESHOLD

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def rank_by_relevance(
    market: Market,
    articles: list[tuple[ArticleRef, str]],
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[RankedArticle]:
    if not articles:
        return []

    model = _get_model()
    query_text = f"{market.title} {market.description}"
    query_embedding = model.encode(query_text, convert_to_tensor=True)

    ranked = []
    for article_ref, text in articles:
        article_embedding = model.encode(text, convert_to_tensor=True)
        similarity = float(util.cos_sim(query_embedding, article_embedding)[0][0])
        if similarity >= threshold:
            ranked.append(RankedArticle(article=article_ref, text=text, similarity=similarity))

    ranked.sort(key=lambda r: r.similarity, reverse=True)
    return ranked
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_relevance_ranker.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add resolution_finder/relevance_ranker.py tests/test_relevance_ranker.py
git commit -m "feat: add embedding-based relevance ranker"
```

---

### Task 9: Verdict Engine

> **Note (added before dispatch, from a real market example):** a
> "Which team will Vinicius Junior join next?" market showed that
> multi-outcome markets can ALSO have a deadline-based default — but unlike
> the CLARITY Act's plain "resolves to No", this one defaults to a specific
> *named option* ("...the market will resolve to 'Real Madrid'") if no
> transfer happens by the deadline. The original plan only gave
> `_decide_binary` a default-outcome check; this version generalizes
> `_extract_default_outcome` to capture either "Yes"/"No" or an arbitrary
> option name, and adds the same deadline-default check to
> `_decide_multi_outcome`. Verified by hand against all three real market
> descriptions (CLARITY Act, Nobel Peace Prize, Vinicius Junior) before
> writing this brief — each extracts exactly the right default.

**Files:**
- Create: `resolution_finder/verdict_engine.py`
- Test: `tests/test_verdict_engine.py`

**Interfaces:**
- Consumes: `Market`, `RankedArticle`, `Verdict` (Task 1).
- Produces: `decide(market: Market, ranked_evidence: list[RankedArticle]) -> Verdict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_verdict_engine.py
from datetime import date, timedelta
from resolution_finder.models import Market, ArticleRef, RankedArticle
from resolution_finder.verdict_engine import decide

CLARITY_DESCRIPTION = (
    "This market resolves to \"Yes\" if the Digital Asset Market Clarity Act "
    "of 2025 (H.R. 3633) is approved by both the U.S. House of Representatives "
    "and the U.S. Senate, and is signed into law no later than December 31, 2026, "
    "at 11:59 PM ET. If these conditions are not met by the deadline, the market "
    "resolves to \"No\"."
)

CLARITY_MARKET = Market(
    id="clarity-act-2026",
    title="Will the CLARITY act be signed into law in 2026?",
    description=CLARITY_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=365),
)

NOBEL_DESCRIPTION = (
    "This market will be settled based on the recipient of the 2026 Nobel "
    "Peace Prize officially announced by the Norwegian Nobel Committee. "
    "The listed person or entity that receives the 2026 Nobel Peace Prize "
    "will resolve to \"Yes\", and all other listed outcomes will resolve to "
    "\"No\". If the 2026 Nobel Peace Prize has not been officially announced "
    "by March 31, 2027, 11:59 PM ET, all listed outcomes will resolve to \"No\"."
)

NOBEL_MARKET = Market(
    id="nobel-peace-2026",
    title="Who will win the 2026 Nobel Peace Prize?",
    description=NOBEL_DESCRIPTION,
    options=["Yulia Navalnaya", "Volodymyr Zelenskyy", "UNRWA", "Pope Leo XIV", "Donald Trump"],
    close_date=date.today() + timedelta(days=365),
)

VINICIUS_DESCRIPTION = (
    "This market will settle based on the next team Vinicius Junior "
    "officially joins by September 1, 2026, at 11:59 PM ET. If he has not "
    "officially joined a new team by that deadline, the market will resolve "
    "to \"Real Madrid\". If he joins a team that is not included among the "
    "listed options, all listed teams on this market will resolve to \"No\"."
)

VINICIUS_MARKET = Market(
    id="vinicius-transfer-2026",
    title="Which team will Vinicius Junior join next?",
    description=VINICIUS_DESCRIPTION,
    options=["Real Madrid", "Arsenal"],
    close_date=date.today() + timedelta(days=365),
)


def make_ranked(text, url="https://congress.gov/bill/3633", source_type="primary", similarity=0.8):
    return RankedArticle(
        article=ArticleRef(url=url, title="t", source_type=source_type),
        text=text,
        similarity=similarity,
    )


def test_binary_market_resolves_yes_on_keyword_match():
    evidence = [make_ranked("The bill was signed into law by the President today.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "YES"
    assert verdict.source_url == "https://congress.gov/bill/3633"


def test_binary_market_applies_stated_default_after_deadline():
    past_deadline_market = Market(
        id="clarity-act-2026",
        title=CLARITY_MARKET.title,
        description=CLARITY_DESCRIPTION,
        options=[],
        close_date=date.today() - timedelta(days=1),
    )
    verdict = decide(past_deadline_market, [])
    assert verdict.outcome == "NO"


def test_binary_market_unclear_when_evidence_inconclusive():
    evidence = [make_ranked("The committee discussed the bill's implications for markets.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


def test_binary_market_no_evidence():
    verdict = decide(CLARITY_MARKET, [])
    assert verdict.outcome == "NO_EVIDENCE"


def test_multi_outcome_market_picks_matching_option():
    evidence = [make_ranked(
        "The Norwegian Nobel Committee announced that the prize is awarded to Pope Leo XIV.",
        url="https://nobelprize.org/announcement",
    )]
    verdict = decide(NOBEL_MARKET, evidence)
    assert verdict.outcome == "Pope Leo XIV"


def test_multi_outcome_market_unclear_when_no_option_matches():
    evidence = [make_ranked("The Nobel Committee will announce the winner next week.")]
    verdict = decide(NOBEL_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


def test_multi_outcome_market_applies_stated_no_default_after_deadline():
    past_deadline_market = Market(
        id="nobel-peace-2026",
        title=NOBEL_MARKET.title,
        description=NOBEL_DESCRIPTION,
        options=NOBEL_MARKET.options,
        close_date=date.today() - timedelta(days=1),
    )
    verdict = decide(past_deadline_market, [])
    assert verdict.outcome == "NO"


def test_multi_outcome_market_applies_stated_option_default_after_deadline():
    past_deadline_market = Market(
        id="vinicius-transfer-2026",
        title=VINICIUS_MARKET.title,
        description=VINICIUS_DESCRIPTION,
        options=VINICIUS_MARKET.options,
        close_date=date.today() - timedelta(days=1),
    )
    verdict = decide(past_deadline_market, [])
    assert verdict.outcome == "Real Madrid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_verdict_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resolution_finder.verdict_engine'`

- [ ] **Step 3: Implement the verdict engine**

```python
# resolution_finder/verdict_engine.py
import re
from datetime import date
from typing import Optional
from resolution_finder.models import Market, RankedArticle, Verdict

# Generalized: captures a plain Yes/No default (CLARITY Act, Nobel Prize) OR a
# specific named option default (Vinicius Junior -> "Real Madrid"). The
# trigger phrases anchor on deadline-miss language so this doesn't match an
# unrelated "resolves to X" sentence describing the normal win condition.
DEFAULT_OUTCOME_PATTERN = re.compile(
    r"(?:not met|has not|have not|not officially|not been)[^.]{0,150}?"
    r"resolve[s]?\s+to\s+\"?([A-Za-z][A-Za-z0-9 .&'-]*?)\"?[.\n]",
    re.IGNORECASE | re.DOTALL,
)

BINARY_YES_KEYWORDS = ["signed into law", "became law", "enacted", "approved by both"]
ANNOUNCEMENT_KEYWORDS = ["awarded to", "wins", "winner is", "named recipient", "recipient is"]


def _extract_default_outcome(description: str) -> Optional[str]:
    match = DEFAULT_OUTCOME_PATTERN.search(description)
    if not match:
        return None
    candidate = match.group(1).strip()
    if candidate.lower() == "yes":
        return "YES"
    if candidate.lower() == "no":
        return "NO"
    return candidate


def _decide_binary(market: Market, ranked_evidence: list[RankedArticle]) -> Verdict:
    for item in ranked_evidence:
        lowered = item.text.lower()
        if any(keyword in lowered for keyword in BINARY_YES_KEYWORDS):
            return Verdict(
                outcome="YES",
                confidence=item.similarity,
                evidence_snippet=item.text[:280],
                source_url=item.article.url,
                source_type=item.article.source_type,
            )

    default_outcome = _extract_default_outcome(market.description)
    if default_outcome in ("YES", "NO") and date.today() > market.close_date:
        return Verdict(
            outcome=default_outcome,
            confidence=0.5,
            evidence_snippet="Deadline passed with no matching evidence; applying stated default.",
            source_url=None,
            source_type=None,
        )

    if ranked_evidence:
        top = ranked_evidence[0]
        return Verdict(
            outcome="UNCLEAR",
            confidence=top.similarity,
            evidence_snippet=top.text[:280],
            source_url=top.article.url,
            source_type=top.article.source_type,
        )

    return Verdict(outcome="NO_EVIDENCE", confidence=0.0, evidence_snippet=None,
                    source_url=None, source_type=None)


def _decide_multi_outcome(market: Market, ranked_evidence: list[RankedArticle]) -> Verdict:
    for item in ranked_evidence:
        lowered = item.text.lower()
        for option in market.options:
            option_lower = option.lower()
            for keyword in ANNOUNCEMENT_KEYWORDS:
                pattern = re.compile(
                    rf"{re.escape(option_lower)}.{{0,40}}{re.escape(keyword)}|"
                    rf"{re.escape(keyword)}.{{0,40}}{re.escape(option_lower)}"
                )
                if pattern.search(lowered):
                    return Verdict(
                        outcome=option,
                        confidence=item.similarity,
                        evidence_snippet=item.text[:280],
                        source_url=item.article.url,
                        source_type=item.article.source_type,
                    )

    default_outcome = _extract_default_outcome(market.description)
    if default_outcome and date.today() > market.close_date:
        if default_outcome == "NO":
            return Verdict(
                outcome="NO",
                confidence=0.5,
                evidence_snippet=(
                    "Deadline passed with no matching evidence; applying "
                    "stated default (no listed option resolves Yes)."
                ),
                source_url=None,
                source_type=None,
            )
        matching_option = next(
            (opt for opt in market.options if opt.lower() == default_outcome.lower()),
            None,
        )
        if matching_option:
            return Verdict(
                outcome=matching_option,
                confidence=0.5,
                evidence_snippet="Deadline passed with no matching evidence; applying stated default option.",
                source_url=None,
                source_type=None,
            )

    if ranked_evidence:
        top = ranked_evidence[0]
        return Verdict(outcome="UNCLEAR", confidence=top.similarity,
                        evidence_snippet=top.text[:280], source_url=top.article.url,
                        source_type=top.article.source_type)

    return Verdict(outcome="NO_EVIDENCE", confidence=0.0, evidence_snippet=None,
                    source_url=None, source_type=None)


def decide(market: Market, ranked_evidence: list[RankedArticle]) -> Verdict:
    if market.options:
        return _decide_multi_outcome(market, ranked_evidence)
    return _decide_binary(market, ranked_evidence)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_verdict_engine.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add resolution_finder/verdict_engine.py tests/test_verdict_engine.py
git commit -m "feat: add rule-based verdict engine with generalized deadline defaults"
```

---

### Task 10: Pipeline wiring

**Files:**
- Create: `resolution_finder/pipeline.py`
- Create: `run_scan.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `MarketProvider` (Task 3), `build_queries` (Task 4), `retrieve_evidence` (Task 6), `extract_article_text` (Task 7), `rank_by_relevance` (Task 8), `decide` (Task 9), `init_db`/`save_finding` (Task 2), `REQUEST_DELAY_SECONDS`/`DB_PATH`/`MARKETS_JSON_PATH` (Task 1 config).
- Produces: `run_pipeline(market_provider, db_path) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline.py
import os
import tempfile
from unittest.mock import patch
from datetime import date, timedelta
from resolution_finder.models import Market, ArticleRef
from resolution_finder.pipeline import run_pipeline
from resolution_finder.storage import get_latest_findings


class FakeMarketProvider:
    def get_unresolved_markets(self):
        return [
            Market(
                id="clarity-act-2026",
                title="Will the CLARITY act be signed into law in 2026?",
                description="If these conditions are not met by the deadline, the market resolves to \"No\".",
                options=[],
                close_date=date.today() + timedelta(days=365),
            )
        ]


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_writes_a_finding_per_market(mock_retrieve, mock_extract, mock_rank, mock_sleep):
    mock_retrieve.return_value = [
        ArticleRef(url="https://congress.gov/bill/3633", title="t", source_type="primary")
    ]
    mock_extract.return_value = "The bill was signed into law today."
    mock_rank.return_value = []

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)

    run_pipeline(FakeMarketProvider(), db_path)

    findings = get_latest_findings(db_path)
    assert len(findings) == 1
    assert findings[0]["market_id"] == "clarity-act-2026"
    os.remove(db_path)


@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_never_fetches_official_social_urls(mock_retrieve, mock_extract, mock_rank, mock_sleep):
    mock_retrieve.return_value = [
        ArticleRef(
            url="https://x.com/NobelPrize/status/123",
            title="NobelPrize post",
            source_type="official_social",
            summary="NobelPrize: The 2026 laureate is Pope Leo XIV.",
        )
    ]
    mock_rank.return_value = []

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)

    run_pipeline(FakeMarketProvider(), db_path)

    mock_extract.assert_not_called()
    articles_passed = mock_rank.call_args[0][1]
    assert articles_passed[0][1] == "NobelPrize: The 2026 laureate is Pope Leo XIV."
    os.remove(db_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resolution_finder.pipeline'`

- [ ] **Step 3: Implement the pipeline and entry point**

```python
# resolution_finder/pipeline.py
import time
from datetime import datetime, timezone
from resolution_finder.market_provider import MarketProvider
from resolution_finder.query_builder import build_queries
from resolution_finder.evidence_retriever import retrieve_evidence
from resolution_finder.article_extractor import extract_article_text
from resolution_finder.relevance_ranker import rank_by_relevance
from resolution_finder.verdict_engine import decide
from resolution_finder.storage import init_db, save_finding
from resolution_finder.config import REQUEST_DELAY_SECONDS


def run_pipeline(market_provider: MarketProvider, db_path: str) -> None:
    init_db(db_path)
    run_timestamp = datetime.now(timezone.utc).isoformat()

    for market in market_provider.get_unresolved_markets():
        queries = build_queries(market)
        candidate_refs = retrieve_evidence(market, queries)

        articles_with_text = []
        for ref in candidate_refs:
            if ref.source_type == "official_social":
                # Never fetch the actual X page — only use the search-result
                # snippet already captured by the retriever. The reviewer
                # checks the real post by hand via the dashboard link.
                text = ref.summary or ref.title
                if text:
                    articles_with_text.append((ref, text))
                continue

            text = extract_article_text(ref.url)
            if text:
                articles_with_text.append((ref, text))
            time.sleep(REQUEST_DELAY_SECONDS)

        ranked = rank_by_relevance(market, articles_with_text)
        verdict = decide(market, ranked)
        save_finding(db_path, market.id, run_timestamp, verdict)
```

```python
# run_scan.py
from resolution_finder.market_provider import JsonFileMarketProvider
from resolution_finder.pipeline import run_pipeline
from resolution_finder.config import DB_PATH, MARKETS_JSON_PATH


def main():
    provider = JsonFileMarketProvider(MARKETS_JSON_PATH)
    run_pipeline(provider, DB_PATH)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add resolution_finder/pipeline.py run_scan.py tests/test_pipeline.py
git commit -m "feat: wire pipeline stages together with a runnable entry point"
```

- [ ] **Step 6: Register the scheduled run (Windows Task Scheduler)**

Open Task Scheduler, create a new task that runs:

```
python "C:\Users\liam\Documents\Resolution Finder\run_scan.py"
```

Set the trigger to repeat every few hours (or daily), with "Start in" set to the project directory so relative paths (`data/markets.json`, `data/resolution_finder.db`) resolve correctly. This is a one-time manual setup step, not part of the codebase.

---

### Task 11: Dashboard

**Files:**
- Create: `resolution_finder/dashboard.py`
- Create: `resolution_finder/templates/index.html`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `get_latest_findings`, `set_review_status` (Task 2).
- Produces: `create_app(db_path: str) -> Flask`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard.py
import os
import tempfile
from resolution_finder.storage import init_db, save_finding
from resolution_finder.models import Verdict
from resolution_finder.dashboard import create_app


def make_temp_db_with_finding():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    init_db(path)
    verdict = Verdict(outcome="YES", confidence=0.8, evidence_snippet="signed into law",
                       source_url="https://congress.gov/x", source_type="primary")
    finding_id = save_finding(path, "clarity-act-2026", "2026-08-10T00:00:00", verdict)
    return path, finding_id


def test_index_lists_findings():
    db_path, _ = make_temp_db_with_finding()
    app = create_app(db_path)
    client = app.test_client()

    response = client.get("/")
    assert response.status_code == 200
    assert b"clarity-act-2026" in response.data
    assert b"YES" in response.data
    os.remove(db_path)


def test_review_updates_status_and_redirects():
    db_path, finding_id = make_temp_db_with_finding()
    app = create_app(db_path)
    client = app.test_client()

    response = client.post(f"/review/{finding_id}", data={"status": "Confirmed"})
    assert response.status_code == 302

    response = client.get("/")
    assert b"Confirmed" in response.data
    os.remove(db_path)


def test_index_flags_official_social_source_for_manual_verification():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)
    verdict = Verdict(outcome="Pope Leo XIV", confidence=0.5,
                       evidence_snippet="NobelPrize: The 2026 laureate is Pope Leo XIV.",
                       source_url="https://x.com/NobelPrize/status/123",
                       source_type="official_social")
    save_finding(db_path, "nobel-peace-2026", "2026-08-10T00:00:00", verdict)

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"verify this is the real official account" in response.data
    assert b"https://x.com/NobelPrize/status/123" in response.data
    os.remove(db_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resolution_finder.dashboard'`

- [ ] **Step 3: Implement the dashboard**

```python
# resolution_finder/dashboard.py
from flask import Flask, render_template, request, redirect, url_for
from resolution_finder.storage import get_latest_findings, set_review_status


def create_app(db_path: str) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        findings = get_latest_findings(db_path)
        return render_template("index.html", findings=findings)

    @app.route("/review/<int:finding_id>", methods=["POST"])
    def review(finding_id):
        status = request.form["status"]
        set_review_status(db_path, finding_id, status)
        return redirect(url_for("index"))

    return app
```

```html
<!-- resolution_finder/templates/index.html -->
<!doctype html>
<html>
<head><title>Resolution Finder — Review Queue</title></head>
<body>
  <h1>Review Queue</h1>
  <table border="1">
    <tr>
      <th>Market</th><th>Outcome</th><th>Confidence</th><th>Evidence</th>
      <th>Source</th><th>Status</th><th>Action</th>
    </tr>
    {% for f in findings %}
    <tr>
      <td>{{ f.market_id }}</td>
      <td>{{ f.outcome }}</td>
      <td>{{ "%.2f"|format(f.confidence) }}</td>
      <td>{{ f.evidence_snippet or "-" }}</td>
      <td>
        {% if f.source_url %}
          {% if f.source_type == "official_social" %}
            <a href="{{ f.source_url }}">verify this is the real official account</a>
          {% else %}
            <a href="{{ f.source_url }}">{{ f.source_type }}</a>
          {% endif %}
        {% else %}-{% endif %}
      </td>
      <td>{{ f.review_status }}</td>
      <td>
        <form method="post" action="{{ url_for('review', finding_id=f.id) }}">
          <button name="status" value="Confirmed">Confirm</button>
          <button name="status" value="Rejected">Reject</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
</body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add resolution_finder/dashboard.py resolution_finder/templates/index.html tests/test_dashboard.py
git commit -m "feat: add Flask review dashboard with official-account verification flag"
```

---

### Task 12: Extend official-account search to Instagram

> **Note (added after Task 11):** the user asked to also check official
> Instagram accounts, the same way Task 6 added a best-effort X/Twitter
> search. This reuses the existing `official_social` source type and
> dashboard label unchanged — both are already platform-agnostic — so this
> task only touches source config and the evidence retriever. Instagram is
> even less indexed by Google than X/news, so this will succeed even less
> often than the X search; it's included anyway since it costs nothing and
> everything it finds still goes through the same manual-verification flag.
> This modifies two already-completed, already-reviewed files (Task 5's
> `source_config.py`, Task 6's `evidence_retriever.py`), including one of
> Task 6's existing tests, which now needs a fourth mocked search call in
> its sequence — that update is spelled out below.

**Files:**
- Modify: `resolution_finder/source_config.py` (add `TIER1_INSTAGRAM_ACCOUNTS` and `resolve_instagram_handle`)
- Modify: `resolution_finder/evidence_retriever.py` (add an Instagram-scoped search block, mirroring the X one)
- Modify: `tests/test_source_config.py` (2 new tests)
- Modify: `tests/test_evidence_retriever.py` (1 new test; update the existing X social-search test's mock sequence)

**Interfaces:**
- Consumes: `extract_urls` (Task 4) — unchanged.
- Produces: `TIER1_INSTAGRAM_ACCOUNTS: dict`, `resolve_instagram_handle(description: str) -> Optional[str]`. `retrieve_evidence`'s existing signature and return type (`list[ArticleRef]`) are unchanged — this only adds another source of `official_social`-tagged entries.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_source_config.py` (update the import line to include `resolve_instagram_handle`):

```python
def test_resolves_instagram_handle_for_known_organization():
    description = "Officially announced by the Norwegian Nobel Committee."
    assert resolve_instagram_handle(description) == "@nobelprize_org"


def test_resolves_instagram_handle_returns_none_when_unknown():
    description = "This market resolves based on consensus of credible reporting."
    assert resolve_instagram_handle(description) is None
```

In `tests/test_evidence_retriever.py`, update the existing social-search test to account for the new Instagram search call between the X search and the Tier 2 general search:

```python
@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_includes_social_search_for_known_organization(mock_parse, mock_sleep):
    nobel_market = Market(
        id="nobel-peace-2026",
        title="Who will win the 2026 Nobel Peace Prize?",
        description="Officially announced by the Norwegian Nobel Committee.",
        options=["Pope Leo XIV"],
        close_date=date(2027, 3, 31),
    )
    mock_parse.side_effect = [
        make_fake_feed([]),  # Tier 1 domain-scoped search (nobelprize.org)
        make_fake_feed([make_entry(
            "https://x.com/NobelPrize/status/123",
            "NobelPrize: The 2026 laureate is...",
        )]),  # X social search
        make_fake_feed([]),  # Instagram social search
        make_fake_feed([]),  # Tier 2 general search
    ]

    evidence = retrieve_evidence(nobel_market, ["Nobel Peace Prize winner"])

    social_hits = [e for e in evidence if e.source_type == "official_social"]
    assert len(social_hits) == 1
    assert social_hits[0].url == "https://x.com/NobelPrize/status/123"
    assert social_hits[0].summary == "NobelPrize: The 2026 laureate is..."
```

Add a new test alongside it for the Instagram path:

```python
@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_includes_instagram_search_for_known_organization(mock_parse, mock_sleep):
    nobel_market = Market(
        id="nobel-peace-2026",
        title="Who will win the 2026 Nobel Peace Prize?",
        description="Officially announced by the Norwegian Nobel Committee.",
        options=["Pope Leo XIV"],
        close_date=date(2027, 3, 31),
    )
    mock_parse.side_effect = [
        make_fake_feed([]),  # Tier 1 domain-scoped search (nobelprize.org)
        make_fake_feed([]),  # X social search
        make_fake_feed([make_entry(
            "https://instagram.com/p/abc123",
            "nobelprize_org: The 2026 laureate is...",
        )]),  # Instagram social search
        make_fake_feed([]),  # Tier 2 general search
    ]

    evidence = retrieve_evidence(nobel_market, ["Nobel Peace Prize winner"])

    social_hits = [e for e in evidence if e.source_type == "official_social"]
    assert len(social_hits) == 1
    assert social_hits[0].url == "https://instagram.com/p/abc123"
```

- [ ] **Step 2: Run tests to verify the new/updated ones fail**

Run: `pytest tests/test_source_config.py tests/test_evidence_retriever.py -v`
Expected: the two new `source_config` tests FAIL with `ImportError` (`resolve_instagram_handle` doesn't exist yet); `test_retrieve_evidence_includes_instagram_search_for_known_organization` FAILS; `test_retrieve_evidence_includes_social_search_for_known_organization` FAILS too, since `retrieve_evidence` doesn't yet make a 3rd `feedparser.parse` call and will get the Tier-2 empty feed where the test now expects the Instagram slot (the `side_effect` list is consumed one call short, so the test's own assertions no longer match — confirms the test was actually updated to require the new behavior, not accidentally already passing).

- [ ] **Step 3: Implement the Instagram handle resolver**

Add to `resolution_finder/source_config.py`, right after `TIER1_SOCIAL_ACCOUNTS`:

```python
# Same rationale as TIER1_SOCIAL_ACCOUNTS above, for Instagram instead of X.
# Instagram content is indexed by Google even less than X/news, so this will
# succeed less often — kept anyway since it's zero-cost and any hit still
# goes through the same official_social manual-verification flag.
TIER1_INSTAGRAM_ACCOUNTS = {
    "congress.gov": "@housefloor",
    "norwegian nobel committee": "@nobelprize_org",
    "sec.gov": "@secgov",
}


def resolve_instagram_handle(description: str) -> Optional[str]:
    lowered = description.lower()
    for phrase, handle in TIER1_INSTAGRAM_ACCOUNTS.items():
        if phrase in lowered:
            return handle
    return None
```

- [ ] **Step 4: Add the Instagram search to the evidence retriever**

In `resolution_finder/evidence_retriever.py`, update the import line:

```python
from resolution_finder.source_config import (
    resolve_named_source,
    resolve_social_handle,
    resolve_instagram_handle,
    TIER2_OUTLETS,
)
```

Add this block immediately after the existing X/Twitter social-search block (after its `time.sleep(REQUEST_DELAY_SECONDS)`, before the Tier 2 general-search loop):

```python
    instagram_handle = resolve_instagram_handle(market.description)
    if instagram_handle and queries:
        for ref in search_google_news_rss(f"{queries[0]} {instagram_handle}", site="instagram.com"):
            evidence.append(ArticleRef(
                url=ref.url,
                title=ref.title,
                source_type="official_social",
                summary=ref.title,
            ))
        time.sleep(REQUEST_DELAY_SECONDS)
```

(Note the `and queries` guard is included from the start this time — Task 6 needed a fix round to add the equivalent guard to the X block after review caught its absence.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_source_config.py tests/test_evidence_retriever.py -v`
Expected: PASS (7 passed in test_source_config.py, 6 passed in test_evidence_retriever.py)

Run: `pytest -v`
Expected: full suite passes with no regressions.

- [ ] **Step 6: Commit**

```bash
git add resolution_finder/source_config.py resolution_finder/evidence_retriever.py tests/test_source_config.py tests/test_evidence_retriever.py
git commit -m "feat: extend official-account search to Instagram"
```

---

### Task 13: End-to-end dry run against real data

This task has no new code — it validates the whole pipeline against your real 62 markets.

- [ ] **Step 1: Fill in the real markets**

Edit `data/markets.json` and replace the two example entries with your actual 62 unresolved markets (title, description, options, close_date), following the same JSON shape used in Task 3. Include the "Which team will Vinicius Junior join next?" market (title, description, options `["Real Madrid", "Arsenal"]`, close date 2026-09-01) discussed during planning, since the user specifically wants that one checked once the pipeline is running.

- [ ] **Step 2: Run the full test suite**

Run: `pytest -v`
Expected: All tests from Tasks 1–12 PASS.

- [ ] **Step 3: Run a real scan**

Run: `python run_scan.py`
Expected: Completes without unhandled exceptions; `data/resolution_finder.db` is created/updated.

- [ ] **Step 4: Start the dashboard and review the output**

```bash
python -c "from resolution_finder.dashboard import create_app; from resolution_finder.config import DB_PATH; create_app(DB_PATH).run(debug=True)"
```

Open `http://127.0.0.1:5000` and confirm every market appears with an outcome (`YES`/`NO`/an option name/`UNCLEAR`/`NO_EVIDENCE`).

- [ ] **Step 5: Spot-check accuracy**

Pick 5 markets whose real-world outcome you already know and confirm the dashboard's proposed verdict and evidence snippet agree with reality. Note any systematic misses (e.g., a keyword phrase this rule set doesn't catch) — those become follow-up `BINARY_YES_KEYWORDS`/`ANNOUNCEMENT_KEYWORDS` additions in `verdict_engine.py`, not new files.

- [ ] **Step 6: Decide on markets.json and git**

`data/markets.json` will contain real (possibly sensitive) market data once filled in. Either commit it if that's fine for this private repo, or add `data/markets.json` to a new `.gitignore` and commit an empty `data/markets.json.example` instead — your call at this point.

---

### Task 14: Fix Tier 2 evidence retrieval by switching to Bing News RSS

> **Note (added after the final whole-branch review and its fix wave):** the
> review found — and a live dry run confirmed — that Google News RSS's
> `entry.link` is a `news.google.com/rss/articles/...` redirect wrapper that
> requires JavaScript to resolve to the real article. The fix wave (commits
> `85e5ae5..ced44cb`) correctly fixed how results are *tagged* (using
> `entry.source.href`/`.title` instead of the wrapper URL), but confirmed
> empirically that the wrapper itself still cannot be fetched into usable
> article text via `requests`+`trafilatura` (336/336 live fetches failed).
> This means Tier 2 (credible outlet) evidence was still never reaching the
> verdict engine, even after that fix.
>
> Empirical investigation for this task (done before writing this brief):
> decoding the Google wrapper is a dead end (its path segment is an opaque
> protobuf blob, not a real URL; the fetched interstitial page doesn't embed
> the target URL either — confirmed by fetching a real wrapper page and
> searching its HTML). Reverse-engineering Google's internal redirect API was
> considered and rejected as too fragile for a v1 tool (matches the original
> fix wave's reasoning). **Bing News RSS** (`bing.com/news/search?...&format=RSS`)
> was tested instead: it also wraps links (`bing.com/news/apiclick.aspx?...`),
> but the real destination is a plain `url=` query parameter — no decoding,
> no JS execution needed. Verified against 3 real Bing results: 2 of 3
> fetched and extracted real article text (trafilatura got 2278 and 3161
> characters respectively); the third failed with an ordinary per-site 403
> (normal bot-blocking on some sites, not a systemic problem). This is a
> categorically different failure mode than Google's 0/336 — most Bing
> results should now be usable.
>
> One limitation found: Bing's RSS endpoint does **not** honor `site:`
> scoping (verified: `Nobel Peace Prize site:bbc.com` and `site:reuters.com`
> both returned 0 entries via Bing, while the same query unscoped returned
> 10). So this task only replaces the **Tier 2 general/unscoped search**
> (the credible-outlet fallback) with Bing. Tier 1 (named-source, domain-
> scoped) and the X/Instagram social searches still need `site:` scoping and
> stay on Google News RSS — they already validate the real source domain
> before tagging (from the prior fix wave), so they continue to degrade
> safely rather than mislabeling anything; they just don't get this
> extraction improvement. A future task could revisit Tier 1/social coverage
> separately if needed.

**Files:**
- Modify: `resolution_finder/evidence_retriever.py` (add `search_bing_news_rss`, a `_host_of` helper, and update `retrieve_evidence`'s Tier 2 loop to call it)
- Modify: `tests/test_evidence_retriever.py` (new tests for Bing extraction/tagging; update any existing test whose mock assumptions no longer hold for the Tier 2 loop)

**Interfaces:**
- Consumes: `ArticleRef` (Task 1), `_is_whitelisted`/`TIER2_OUTLETS` (already in this file/Task 5).
- Produces: `search_bing_news_rss(query: str) -> list[ArticleRef]`. `retrieve_evidence`'s existing signature and return type are unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_evidence_retriever.py` (these test `search_bing_news_rss` directly, using realistic Bing wrapper URLs — the wrapper shape and the fact that `url=` carries the real, url-encoded destination were verified against live Bing News RSS output):

```python
from urllib.parse import quote


def make_bing_entry(title, real_url):
    entry = MagicMock()
    entry.title = title
    entry.link = (
        "http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&tid=abc123"
        f"&url={quote(real_url, safe='')}&c=123&mkt=en-ww"
    )
    return entry


@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_bing_news_rss_extracts_real_url_and_tags_whitelisted(mock_parse):
    mock_parse.return_value = make_fake_feed([
        make_bing_entry("Reuters headline", "https://www.reuters.com/article/x"),
        make_bing_entry("Random headline", "https://randomblog.com/article/y"),
    ])
    results = search_bing_news_rss("CLARITY act")
    assert results[0].url == "https://www.reuters.com/article/x"
    assert results[0].source_type == "credible_backup"
    assert results[1].url == "https://randomblog.com/article/y"
    assert results[1].source_type == "general"


@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_bing_news_rss_skips_entries_without_resolvable_url(mock_parse):
    unresolvable = MagicMock()
    unresolvable.title = "No url param"
    unresolvable.link = "http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&c=1&mkt=en-ww"
    mock_parse.return_value = make_fake_feed([unresolvable])
    results = search_bing_news_rss("CLARITY act")
    assert results == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_evidence_retriever.py -v`
Expected: FAIL with `ImportError` (`search_bing_news_rss` doesn't exist yet).

- [ ] **Step 3: Implement the Bing search function**

Update the import line in `resolution_finder/evidence_retriever.py`:

```python
from urllib.parse import quote_plus, urlparse, parse_qs
```

Add a shared host-parsing helper and refactor `entry_source_domain` to use it (removes the one in-file duplication; `article_extractor.py`'s separate copy is a known, already-deferred minor item — don't touch that file in this task):

```python
def _host_of(netloc_source: str) -> Optional[str]:
    """Lowercase host from a URL or bare `host[:port]` string, no scheme required."""
    netloc = urlparse(netloc_source if "//" in netloc_source else "//" + netloc_source).netloc
    host = netloc.split("@")[-1].split(":")[0].strip().lower()
    return host or None
```

In `entry_source_domain`, replace the inline netloc-parsing block:

```python
    href = _source_field(source, "href")
    if href:
        host = _host_of(href)
        if host:
            return host
```

Add, after `_is_whitelisted`:

```python
BING_NEWS_RSS = "https://www.bing.com/news/search?q={query}&format=RSS"


def _bing_target_url(wrapper_url: str) -> Optional[str]:
    """The real article URL from a Bing News RSS wrapper link.

    Unlike Google News RSS's opaque redirect, Bing's wrapper
    (`bing.com/news/apiclick.aspx?...`) carries the real destination as a
    plain `url` query parameter — verified against live Bing output. No
    decoding or JavaScript execution needed, just URL parsing.
    """
    values = parse_qs(urlparse(wrapper_url).query).get("url")
    return values[0] if values else None


def search_bing_news_rss(query: str) -> list[ArticleRef]:
    """General (unscoped) credible-outlet search, replacing Google News RSS
    for Tier 2. Bing's RSS endpoint doesn't honor `site:` scoping (verified
    empirically — see plan Task 14), so this is only used for the unscoped
    Tier 2 fallback. Bing wrapper links resolve to real, directly fetchable
    URLs, unlike Google's JS-redirect wrapper, which is why Tier 2 moved
    here instead of trying to unwrap Google's link.
    """
    url = BING_NEWS_RSS.format(query=quote_plus(query))
    feed = feedparser.parse(url)
    results = []
    for entry in feed.entries:
        real_url = _bing_target_url(entry.link)
        if not real_url:
            logger.warning(
                "Bing News RSS entry has no resolvable url= param: %r",
                getattr(entry, "title", "?"),
            )
            continue
        domain = _host_of(real_url)
        source_type = "credible_backup" if _is_whitelisted(domain) else "general"
        results.append(ArticleRef(
            url=real_url,
            title=entry.title,
            source_type=source_type,
            source_domain=domain,
        ))
    return results
```

- [ ] **Step 4: Point the Tier 2 loop at Bing**

In `retrieve_evidence`, replace the final loop:

```python
    for query in queries:
        for ref in search_google_news_rss(query):
            if ref.source_type == "credible_backup":
                evidence.append(ref)
        time.sleep(REQUEST_DELAY_SECONDS)
```

with:

```python
    for query in queries:
        for ref in search_bing_news_rss(query):
            if ref.source_type == "credible_backup":
                evidence.append(ref)
        time.sleep(REQUEST_DELAY_SECONDS)
```

- [ ] **Step 5: Run the new tests, then the full suite, and fix any mock-shape mismatches**

Run: `pytest tests/test_evidence_retriever.py -v`
Expected: the 2 new tests PASS.

Run: `pytest -v`
Expected: the full suite passes. Some existing tests in `test_evidence_retriever.py` mock `feedparser.parse` with a single `side_effect` list covering every call `retrieve_evidence` makes (Tier 1, social, Tier 2 in order) — since the Tier 2 slot now goes through `search_bing_news_rss` instead of `search_google_news_rss`, any test whose Tier-2-slot mock feed is Google-shaped (a plain `.link` with no `url=` param) will now find `_bing_target_url` returns `None` for those entries and they'll be silently skipped rather than counted as evidence — which will change that test's assertions if it relied on Tier-2-sourced entries. Read each failure, and update the affected mock's final slot(s) to use `make_bing_entry(...)` instead of `make_entry(...)` so the test still exercises what it originally intended. Do not weaken assertions to make them pass — fix the mock shape to match which backend that call slot now actually represents.

- [ ] **Step 6: Empirically verify against live data**

Run a real, live check (not mocked) to confirm this works against the actual internet, similar to what was done for the earlier fix wave:

```python
python -c "
from resolution_finder.evidence_retriever import search_bing_news_rss
results = search_bing_news_rss('Nobel Peace Prize 2026')
for r in results[:5]:
    print(r.source_type, r.url)
"
```

Expected: real URLs printed (not `bing.com/news/apiclick...` wrapper links), with at least some tagged based on their real domain.

- [ ] **Step 7: Re-run the real end-to-end dry run**

Run: `python run_scan.py` against the current `data/markets.json` (3 real markets), then inspect `data/resolution_finder.db`:

```python
python -c "
from resolution_finder.storage import get_latest_findings
for f in get_latest_findings('data/resolution_finder.db'):
    print(f['market_id'], '->', f['outcome'], f'(confidence={f[\"confidence\"]:.2f}, source_type={f[\"source_type\"]})')
"
```

Report whether Tier 2 (`credible_backup`) evidence now actually reaches a stored verdict for at least one market — this is the real pass/fail signal, not just "no exceptions." (It may still show `NO_EVIDENCE`/`UNCLEAR` if no genuinely relevant credible-outlet coverage exists for these specific three markets right now — that would be a correct result, not a bug. The bar is "Tier 2 evidence can reach the verdict engine when it exists," not "these three specific test markets must resolve.")

- [ ] **Step 8: Commit**

```bash
git add resolution_finder/evidence_retriever.py tests/test_evidence_retriever.py
git commit -m "fix: switch Tier 2 evidence search to Bing News RSS

Google News RSS wrapper links cannot be resolved into fetchable
article text (confirmed: 336/336 live fetches failed after the prior
fix wave correctly fixed tagging but not fetchability). Bing News RSS
wrapper links carry the real URL as a plain query parameter, verified
against live data. Bing's RSS endpoint doesn't support site: scoping,
so only the unscoped Tier 2 fallback moves to Bing; Tier 1 and the
social searches stay on Google News RSS."
```

---

### Task 15: Two-tier credible-outlet whitelist

> **Note (added after Task 14):** Task 14 proved the Tier 2 mechanism works
> end-to-end, but a real dry run found 0 of 88 live Bing results for the
> project's 3 test markets fell inside the 5-outlet `TIER2_OUTLETS`
> whitelist (Reuters, AP, BBC, AFP, NPR) — Bing surfaced Guardian, Forbes,
> MSN, Yahoo, and Goal.com heavily instead. Since this tool never
> auto-settles anything — a human always makes the final call in the
> dashboard — the user decided to trust more sources rather than keep the
> whitelist narrow, on the condition that sources with looser editorial
> standards are visibly flagged as lower-reliability rather than presented
> with the same trust level as a wire service.
>
> This task splits `TIER2_OUTLETS` into two tiers: the existing high-trust
> list (expanded with a few more established journalism organizations) and
> a new `TIER2_SECONDARY_OUTLETS` list for broader-but-shakier sources
> (Forbes: open contributor platform, quality varies by author; MSN/Yahoo:
> aggregators that republish other outlets' content rather than doing
> original reporting; Goal.com: a real outlet but only within its
> football/soccer niche, not general-purpose news). Secondary-tier results
> get a new `source_type: "credible_backup_secondary"` and a distinct
> dashboard warning label, rather than being silently mixed in as
> equally-trusted evidence.

**Files:**
- Modify: `resolution_finder/source_config.py` (expand `TIER2_OUTLETS`, add `TIER2_SECONDARY_OUTLETS`)
- Modify: `resolution_finder/evidence_retriever.py` (replace `_is_whitelisted` with a tier-aware `_outlet_tier`, update both search functions and `retrieve_evidence`'s two credible-tier checks)
- Modify: `resolution_finder/templates/index.html` (add the lower-reliability warning label)
- Modify: `resolution_finder/models.py` (update `ArticleRef.source_type`'s comment to list the new value)
- Modify: `tests/test_evidence_retriever.py`, `tests/test_dashboard.py` (new tests)

**Interfaces:**
- Consumes: `ArticleRef`, `_domain_matches` (already in evidence_retriever.py).
- Produces: `_outlet_tier(host: Optional[str]) -> Optional[str]` returning `"credible_backup"`, `"credible_backup_secondary"`, or `None`. `search_bing_news_rss`/`search_google_news_rss`'s return type and `retrieve_evidence`'s signature are unchanged; they can now additionally produce/pass through `source_type="credible_backup_secondary"` refs.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_source_config.py` is not needed (the outlet lists are plain data, exercised through `evidence_retriever` tests below). Add to `tests/test_evidence_retriever.py`:

```python
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_bing_news_rss_tags_secondary_tier_outlet(mock_parse):
    mock_parse.return_value = make_fake_feed([
        make_bing_entry("Forbes headline", "https://www.forbes.com/sites/x/article"),
    ])
    results = search_bing_news_rss("CLARITY act")
    assert results[0].source_type == "credible_backup_secondary"


@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_search_bing_news_rss_still_tags_primary_tier_outlet(mock_parse):
    mock_parse.return_value = make_fake_feed([
        make_bing_entry("Reuters headline", "https://www.reuters.com/article/x"),
    ])
    results = search_bing_news_rss("CLARITY act")
    assert results[0].source_type == "credible_backup"


@patch("resolution_finder.evidence_retriever.time.sleep")
@patch("resolution_finder.evidence_retriever.feedparser.parse")
def test_retrieve_evidence_includes_secondary_tier_evidence(mock_parse, mock_sleep):
    vinicius_market = Market(
        id="vinicius-transfer-2026",
        title="Which team will Vinicius Junior join next?",
        description="No named source in this description.",
        options=["Real Madrid", "Arsenal"],
        close_date=date(2026, 9, 1),
    )
    mock_parse.return_value = make_fake_feed([
        make_bing_entry("Goal.com headline", "https://www.goal.com/en/news/x"),
    ])
    evidence = retrieve_evidence(vinicius_market, ["Vinicius Junior transfer"])
    secondary = [e for e in evidence if e.source_type == "credible_backup_secondary"]
    assert len(secondary) == 1
    assert secondary[0].url == "https://www.goal.com/en/news/x"
```

Add to `tests/test_dashboard.py`:

```python
def test_index_flags_secondary_tier_source_as_lower_reliability():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)
    verdict = Verdict(outcome="Arsenal", confidence=0.6,
                       evidence_snippet="Goal.com reports Vinicius to Arsenal",
                       source_url="https://www.goal.com/en/news/x",
                       source_type="credible_backup_secondary")
    save_finding(db_path, "vinicius-transfer-2026", "2026-08-10T00:00:00", verdict)

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"lower-reliability" in response.data
    assert b'href="https://www.goal.com/en/news/x"' in response.data
    os.remove(db_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_evidence_retriever.py tests/test_dashboard.py -v`
Expected: FAIL — `credible_backup_secondary` never produced yet (existing tagging logic only knows `credible_backup`/`general`), and the dashboard never renders "lower-reliability".

- [ ] **Step 3: Expand the outlet lists**

In `resolution_finder/source_config.py`, replace `TIER2_OUTLETS` and add a new list after it:

```python
TIER2_OUTLETS = [
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "afp.com",
    "npr.org",
    "theguardian.com",
    "aljazeera.com",
    "cnn.com",
    "nytimes.com",
    "washingtonpost.com",
    "politico.com",
]

# Broader coverage, looser editorial standards than TIER2_OUTLETS. Included
# because this tool never auto-settles anything — a human always makes the
# final call in the dashboard — so more evidence (clearly labeled) is better
# than none. Forbes runs an open contributor platform where article quality
# varies by author, not just by outlet; MSN and Yahoo are aggregators that
# republish other outlets' wire content rather than doing original
# reporting, so their own editorial accountability is looser even when the
# underlying story is sound; Goal.com is a real, reputable outlet but only
# within its football/soccer niche, not a general-purpose news wire.
TIER2_SECONDARY_OUTLETS = [
    "forbes.com",
    "goal.com",
    "msn.com",
    "yahoo.com",
]
```

- [ ] **Step 4: Make the evidence retriever tier-aware**

In `resolution_finder/evidence_retriever.py`, update the import line:

```python
from resolution_finder.source_config import (
    resolve_named_source,
    resolve_social_handle,
    resolve_instagram_handle,
    TIER2_OUTLETS,
    TIER2_SECONDARY_OUTLETS,
)
```

Replace `_is_whitelisted`:

```python
def _outlet_tier(host: Optional[str]) -> Optional[str]:
    """Which credibility tier `host` belongs to, or None if neither."""
    if any(_domain_matches(host, outlet) for outlet in TIER2_OUTLETS):
        return "credible_backup"
    if any(_domain_matches(host, outlet) for outlet in TIER2_SECONDARY_OUTLETS):
        return "credible_backup_secondary"
    return None
```

In both `search_bing_news_rss` and `search_google_news_rss`, replace:

```python
        source_type = "credible_backup" if _is_whitelisted(domain) else "general"
```

with:

```python
        source_type = _outlet_tier(domain) or "general"
```

In `retrieve_evidence`, add a module-level constant near the top (after `SOCIAL_PLATFORM_HOSTS` is fine):

```python
CREDIBLE_TIERS = ("credible_backup", "credible_backup_secondary")
```

Then update the two places that currently check `ref.source_type == "credible_backup"`:

```python
                elif ref.source_type in CREDIBLE_TIERS:
                    evidence.append(ref)
```

and:

```python
    for query in queries:
        for ref in search_bing_news_rss(query):
            if ref.source_type in CREDIBLE_TIERS:
                evidence.append(ref)
        time.sleep(REQUEST_DELAY_SECONDS)
```

- [ ] **Step 5: Add the dashboard warning label**

In `resolution_finder/templates/index.html`, replace the label block:

```html
      <td>
        {% if f.source_url %}
          {% if f.source_type == "official_social" %}
            {% set label = "verify this is the real official account" %}
          {% elif f.source_type == "credible_backup_secondary" %}
            {% set label = "⚠ lower-reliability source — verify" %}
          {% else %}
            {% set label = f.source_type %}
          {% endif %}
          {% if f.source_url.startswith('http://') or f.source_url.startswith('https://') %}
            <a href="{{ f.source_url }}">{{ label }}</a>
          {% else %}
            {{ label }}
          {% endif %}
        {% else %}-{% endif %}
      </td>
```

- [ ] **Step 6: Update the ArticleRef comment**

In `resolution_finder/models.py`, update the `source_type` field comment:

```python
    source_type: str  # "primary", "credible_backup", "credible_backup_secondary", "official_social", or "general"
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_evidence_retriever.py tests/test_dashboard.py -v`
Expected: PASS.

Run: `pytest -v`
Expected: full suite passes. Double-check none of the existing boundary tests in `test_evidence_retriever.py` (e.g. ones testing `notreuters.com`, `reuters.com.example.net`) accidentally now match a `TIER2_SECONDARY_OUTLETS` entry — they shouldn't, since none of the new outlet domains overlap with existing test fixtures, but verify rather than assume.

- [ ] **Step 8: Re-run the real dry run and report the outcome**

Run: `python run_scan.py` against the current `data/markets.json`, then inspect results the same way as before:

```python
python -c "
from resolution_finder.storage import get_latest_findings
for f in get_latest_findings('data/resolution_finder.db'):
    print(f['market_id'], '->', f['outcome'], f'(confidence={f[\"confidence\"]:.2f}, source_type={f[\"source_type\"]})')
"
```

Report whether any market now surfaces `credible_backup` or `credible_backup_secondary` evidence. As before, a `NO_EVIDENCE`/`UNCLEAR` result is only a problem if no real coverage exists for a market from ANY whitelisted outlet (primary or secondary) at all — report the raw numbers honestly either way.

- [ ] **Step 9: Commit**

```bash
git add resolution_finder/source_config.py resolution_finder/evidence_retriever.py resolution_finder/templates/index.html resolution_finder/models.py tests/test_evidence_retriever.py tests/test_dashboard.py
git commit -m "feat: add secondary-tier credible outlets with a dashboard reliability warning

Widens outlet coverage since this tool never auto-settles anything —
a human always makes the final call — while keeping looser-standard
sources (contributor platforms, aggregators) visibly distinguished
from wire services rather than blended in as equally trusted."
```

---

### Task 16: Settings storage and dashboard page for the Currents API key + usage tracking

> **Note (added after researching additional free news sources):** the user
> wants to add Currents News API (`api.currentsapi.services`, verified via
> its real OpenAPI spec) as another evidence source, and wants its API key
> manageable from the dashboard UI rather than only via an environment
> variable, plus a visible tracker of remaining daily quota. This task only
> builds the settings storage and UI — it does NOT call the Currents API
> yet, since that requires the user's real API key to test against live
> data (a separate future task once the key is available). This task can be
> fully built and tested now with a fake key string.
>
> Verified from the real Currents API OpenAPI spec (`https://currentsapi.services/json/swagger.json`):
> the API key is sent via an `Authorization` HTTP header (not a URL query
> parameter), and — per Currents' own rate-limit documentation — every
> authenticated response includes `X-RateLimit-Remaining` and
> `X-RateLimit-Limit` headers. This task's usage tracker is designed to
> store whatever those headers report after each real call (a future task's
> job), not to independently guess/count usage itself — the API already
> tells us the true remaining count, so there's no need to duplicate that
> bookkeeping.

**Files:**
- Modify: `resolution_finder/storage.py` (add a `settings` table and `get_setting`/`set_setting` functions)
- Modify: `resolution_finder/dashboard.py` (add `/settings` GET and POST routes)
- Create: `resolution_finder/templates/settings.html`
- Modify: `resolution_finder/templates/index.html` (add a link to the settings page)
- Modify: `tests/test_storage.py`, `tests/test_dashboard.py` (new tests)

**Interfaces:**
- Produces: `get_setting(db_path: str, key: str) -> Optional[str]`, `set_setting(db_path: str, key: str, value: str) -> None`. Settings keys used by this task: `"currents_api_key"`, `"currents_rate_limit_remaining"`, `"currents_rate_limit_limit"`, `"currents_rate_limit_updated_at"` — the last three are written by a future task (Task 17) after a real API call; this task only needs to read and display them if present.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_storage.py`:

```python
def test_set_and_get_setting():
    db_path = make_temp_db()
    set_setting(db_path, "currents_api_key", "abc123")
    assert get_setting(db_path, "currents_api_key") == "abc123"
    os.remove(db_path)


def test_get_setting_returns_none_when_unset():
    db_path = make_temp_db()
    assert get_setting(db_path, "currents_api_key") is None
    os.remove(db_path)


def test_set_setting_overwrites_existing_value():
    db_path = make_temp_db()
    set_setting(db_path, "currents_api_key", "first")
    set_setting(db_path, "currents_api_key", "second")
    assert get_setting(db_path, "currents_api_key") == "second"
    os.remove(db_path)
```

(Update the import line in `tests/test_storage.py` to include `get_setting, set_setting`.)

Add to `tests/test_dashboard.py`:

```python
def test_settings_page_shows_masked_key_when_set():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)
    set_setting(db_path, "currents_api_key", "abcd1234567890")

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/settings")

    assert response.status_code == 200
    assert b"abcd1234567890" not in response.data  # never show the full key
    assert b"7890" in response.data  # last 4 chars, so the user can tell which key is saved
    os.remove(db_path)


def test_settings_page_shows_no_key_message_when_unset():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/settings")

    assert response.status_code == 200
    assert b"No API key saved" in response.data
    os.remove(db_path)


def test_settings_page_saves_new_key():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)

    app = create_app(db_path)
    client = app.test_client()
    response = client.post("/settings", data={"currents_api_key": "newkey1234567890"})

    assert response.status_code == 302
    assert get_setting(db_path, "currents_api_key") == "newkey1234567890"
    os.remove(db_path)


def test_settings_page_shows_usage_when_available():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)
    set_setting(db_path, "currents_api_key", "abcd1234567890")
    set_setting(db_path, "currents_rate_limit_remaining", "543")
    set_setting(db_path, "currents_rate_limit_limit", "600")
    set_setting(db_path, "currents_rate_limit_updated_at", "2026-08-16T12:00:00+00:00")

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/settings")

    assert response.status_code == 200
    assert b"543" in response.data
    assert b"600" in response.data


def test_settings_page_shows_no_usage_data_message_when_unavailable():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)
    set_setting(db_path, "currents_api_key", "abcd1234567890")

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/settings")

    assert response.status_code == 200
    assert b"No usage data yet" in response.data
```

(Update the import line in `tests/test_dashboard.py` to include `get_setting, set_setting`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_storage.py tests/test_dashboard.py -v`
Expected: FAIL — `get_setting`/`set_setting` don't exist yet, `/settings` route doesn't exist yet.

- [ ] **Step 3: Add the settings table and functions**

In `resolution_finder/storage.py`, add to `SCHEMA` (append, don't replace the existing `findings` table definition):

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    run_timestamp TEXT NOT NULL,
    outcome TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_snippet TEXT,
    source_url TEXT,
    source_type TEXT,
    review_status TEXT NOT NULL DEFAULT 'Pending'
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
```

Add these functions (near the bottom of the file, `Optional` needs importing: `from typing import Optional`):

```python
def get_setting(db_path: str, key: str) -> Optional[str]:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_setting(db_path: str, key: str, value: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Add the settings page**

In `resolution_finder/dashboard.py`, update the import line and add the route:

```python
from resolution_finder.storage import get_latest_findings, set_review_status, get_setting, set_setting
```

```python
    @app.route("/settings", methods=["GET"])
    def settings():
        api_key = get_setting(db_path, "currents_api_key")
        masked_key = f"••••{api_key[-4:]}" if api_key else None
        remaining = get_setting(db_path, "currents_rate_limit_remaining")
        limit = get_setting(db_path, "currents_rate_limit_limit")
        updated_at = get_setting(db_path, "currents_rate_limit_updated_at")
        return render_template(
            "settings.html",
            masked_key=masked_key,
            remaining=remaining,
            limit=limit,
            updated_at=updated_at,
        )

    @app.route("/settings", methods=["POST"])
    def save_settings():
        new_key = request.form.get("currents_api_key", "").strip()
        if new_key:
            set_setting(db_path, "currents_api_key", new_key)
        return redirect(url_for("settings"))
```

Add both routes inside `create_app`, alongside the existing `index`/`review` routes (same indentation level, same function).

- [ ] **Step 5: Create the settings template**

```html
<!-- resolution_finder/templates/settings.html -->
<!doctype html>
<html>
<head><title>Resolution Finder — Settings</title></head>
<body>
  <h1>Settings</h1>
  <p><a href="{{ url_for('index') }}">&larr; Back to Review Queue</a></p>

  <h2>Currents News API</h2>
  {% if masked_key %}
    <p>Current key: {{ masked_key }}</p>
  {% else %}
    <p>No API key saved.</p>
  {% endif %}

  <form method="post" action="{{ url_for('save_settings') }}">
    <label for="currents_api_key">API key:</label>
    <input type="password" id="currents_api_key" name="currents_api_key" autocomplete="off">
    <button type="submit">Save</button>
  </form>

  <h3>Usage</h3>
  {% if remaining and limit %}
    <p>{{ remaining }} / {{ limit }} requests remaining today (as of {{ updated_at }}).</p>
  {% else %}
    <p>No usage data yet — save an API key and run a scan to populate this.</p>
  {% endif %}
</body>
</html>
```

- [ ] **Step 6: Link to the settings page from the review queue**

In `resolution_finder/templates/index.html`, add a link right after the `<h1>Review Queue</h1>` line:

```html
  <p><a href="{{ url_for('settings') }}">Settings</a></p>
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_storage.py tests/test_dashboard.py -v`
Expected: PASS.

Run: `pytest -v`
Expected: full suite passes.

- [ ] **Step 8: Commit**

```bash
git add resolution_finder/storage.py resolution_finder/dashboard.py resolution_finder/templates/settings.html resolution_finder/templates/index.html tests/test_storage.py tests/test_dashboard.py
git commit -m "feat: add settings page for Currents API key and usage tracking

Adds a generic key-value settings table and a dashboard /settings
page to manage the (not-yet-integrated) Currents News API key from
the UI instead of only an environment variable, plus a usage display
that a future task will populate from the API's own rate-limit
response headers (X-RateLimit-Remaining / X-RateLimit-Limit) rather
than tracking usage independently."
```
