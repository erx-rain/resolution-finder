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

---

### Task 17: Context-aware keyword matching (fix false-positive verdicts)

> **Note (added after a real false-positive verdict in production data):**
> Task 15's real dry run produced `clarity-act-2026 -> YES` (confidence 0.72)
> from a Yahoo article. The stored evidence snippet itself says the bill's
> "fate uncertain," no floor vote scheduled — the opposite of YES. Fetching
> the actual article and searching for the matched keyword found the real
> cause: the article compares the CLARITY Act to a different law, and the
> match came from **that other law's** sentence: *"The GENIUS Act's
> experience is instructive: signed into law in July 2025..."* — a true
> statement, but about an unrelated bill cited for comparison, not the
> market's own subject.
>
> `_decide_binary`/`_decide_multi_outcome` currently check "does this
> keyword appear anywhere in the whole article," with no check for *what
> sentence* it appears in, whether that sentence is negated/hypothetical, or
> whether it's even about the market's own subject. This task fixes all
> three by moving the check to sentence granularity:
>
> 1. **Wrong-subject rejection** (the actual bug found): reject a keyword
>    match if its sentence names a different capitalized entity/acronym than
>    the market's own subject (extracted via the existing `extract_entities`
>    from Task 4, plus a new short acronym scan on the title — verified this
>    catches "CLARITY" from `"Will the CLARITY act..."` since Task 4's
>    entity regex alone requires 2+ consecutive capitalized words and
>    wouldn't catch a single acronym followed by a lowercase word).
> 2. **Hedge/negation rejection**: reject a match if its sentence contains
>    negation or hypothetical language ("if", "not", "unless", "uncertain",
>    "pending", etc.) — this independently would have caught a related but
>    different failure mode ("if enacted...").
> 3. **Evidence snippet becomes the actual matching sentence** instead of an
>    arbitrary first-280-characters slice, so the reviewer sees exactly what
>    justified the verdict.
>
> Deliberately NOT requiring the subject terms to be *present* (only
> checking that no *different* entity is present) — real articles use
> pronouns and short references ("the bill," "it") after establishing
> context once, and requiring every sentence to repeat the full subject name
> would reject too many genuine matches. Verified this against the existing
> test fixture (`"The bill was signed into law by the President today."`
> mentions no other entity, so it still resolves YES) before finalizing this
> design, not after.
>
> Multi-outcome markets get the hedge-word check too, but not the
> wrong-subject check — the existing option-name-proximity requirement
> already serves that role there (an option name like "Pope Leo XIV" is
> itself a strong, specific subject signal).
>
> A stronger version of subject identification (a real keyphrase-extraction
> model instead of the current regex heuristic) was discussed and deferred —
> `extract_entities` is regex-based because spaCy is unusable in this
> environment (see Task 4); a library like KeyBERT (built on the same
> `sentence-transformers` stack already proven working here) could improve
> this further, but is its own task, not bundled into this urgent fix.

**Files:**
- Modify: `resolution_finder/verdict_engine.py`
- Modify: `tests/test_verdict_engine.py`

**Interfaces:**
- Consumes: `extract_entities` (Task 4, `resolution_finder/query_builder.py`).
- Produces: `decide`'s signature and return type are unchanged — this only changes internal matching logic and what text ends up in `Verdict.evidence_snippet`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_verdict_engine.py` (these use the real article text that caused the production false positive):

```python
def test_binary_market_ignores_keyword_match_about_unrelated_entity():
    # Real false positive found via live data: an article about the CLARITY
    # Act cites the GENIUS Act (an unrelated law) as a comparison, and that
    # other law's "signed into law" sentence must not count as evidence for
    # the CLARITY Act.
    evidence = [make_ranked(
        "The CLARITY Act remains stalled in the Senate. Agency guidance is "
        "more easily reversed by the next administration than statute. The "
        "GENIUS Act's experience is instructive: signed into law in July "
        "2025, its agencies missed their one-year rulemaking deadline."
    )]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome != "YES"


def test_binary_market_ignores_hedged_keyword_match():
    evidence = [make_ranked(
        "The CLARITY Act, if enacted, would represent a significant shift "
        "in digital asset regulation."
    )]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome != "YES"


def test_binary_market_still_resolves_yes_on_genuine_match():
    evidence = [make_ranked(
        "The CLARITY Act was signed into law by the President on Tuesday."
    )]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "YES"
    assert "CLARITY Act was signed into law" in verdict.evidence_snippet


def test_binary_market_resolves_yes_on_keyword_match_still_passes_without_subject_mention():
    # Existing test fixture (Task 9), re-asserted here: a sentence that names
    # no other entity should still match even without repeating the market's
    # own subject name — real articles use pronouns/short references after
    # establishing context once.
    evidence = [make_ranked("The bill was signed into law by the President today.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "YES"
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `pytest tests/test_verdict_engine.py -v`
Expected: the 3 new-behavior tests FAIL (current code has no sentence/subject/hedge logic, so it would return YES for all of them, including the two that should NOT be YES). The last test (existing behavior preserved) should already PASS even before this change — confirms it's a regression guard, not a new requirement.

- [ ] **Step 3: Implement sentence-level, subject-aware, hedge-aware matching**

Replace the full contents of `resolution_finder/verdict_engine.py`:

```python
# resolution_finder/verdict_engine.py
import re
from datetime import date
from typing import Optional
from resolution_finder.models import Market, RankedArticle, Verdict
from resolution_finder.query_builder import extract_entities

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

SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+")

# Words/phrases that turn a sentence hypothetical or negated, e.g. "if
# enacted" or "has not been signed" — a keyword match inside one of these
# doesn't describe something that actually happened.
NEGATION_HEDGE_WORDS = [
    "not ", "n't ", "never ", "without ", "fails to", "failed to",
    "yet to", "has yet", "remains uncertain", "uncertain", "unclear",
    "unlikely", "pending", "awaiting", "no vote", "not scheduled",
    "if ", "unless ", "would be", "could be", "might be",
]

# A bare acronym (e.g. "CLARITY") that Task 4's extract_entities won't catch
# on its own, since that regex requires 2+ consecutive capitalized words and
# a title like "Will the CLARITY act..." has a lowercase word right after it.
ACRONYM_PATTERN = re.compile(r"\b[A-Z]{2,}\b")


def _word_boundary(phrase: str) -> str:
    r"""Regex source matching `phrase` only as whole words.

    Keywords are matched on word boundaries rather than as bare substrings so
    that e.g. "wins" does not fire on "Winston" — a real risk now that market
    options include short common words like "Arsenal".

    `\b` is only added on an edge that is actually a word character; a phrase
    ending in punctuation (an option like "Acme Inc.") would otherwise produce
    a pattern that can never match.
    """
    escaped = re.escape(phrase)
    prefix = r"\b" if phrase[:1].isalnum() or phrase[:1] == "_" else ""
    suffix = r"\b" if phrase[-1:].isalnum() or phrase[-1:] == "_" else ""
    return prefix + escaped + suffix


def _contains_keyword(text: str, keyword: str) -> bool:
    return re.search(_word_boundary(keyword), text) is not None


def _split_sentences(text: str) -> list[str]:
    return [s for s in SENTENCE_SPLIT_PATTERN.split(text) if s.strip()]


def _sentence_has_hedge(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(word in lowered for word in NEGATION_HEDGE_WORDS)


def _subject_terms(market: Market) -> list[str]:
    """Distinctive terms identifying this market's own subject, so a keyword
    match about a different named entity mentioned elsewhere in the same
    article (e.g. a comparable law cited for context) isn't mistaken for
    evidence about this market."""
    combined = f"{market.title} {market.description}"
    terms = list(extract_entities(combined))
    for word in ACRONYM_PATTERN.findall(market.title):
        if word not in terms:
            terms.append(word)
    return terms


def _sentence_mentions_other_entity(sentence: str, subject_terms: list[str]) -> bool:
    """True if the sentence names a capitalized entity/acronym that isn't
    (even partially) one of this market's own subject terms — a signal the
    sentence is about something else. Empty subject_terms means we have no
    way to tell our own subject apart, so never reject on this basis alone.
    """
    if not subject_terms:
        return False
    subject_lower = [t.lower() for t in subject_terms]
    sentence_entities = extract_entities(sentence) + ACRONYM_PATTERN.findall(sentence)
    for entity in sentence_entities:
        entity_lower = entity.lower()
        if not any(entity_lower in s or s in entity_lower for s in subject_lower):
            return True
    return False


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
    subject_terms = _subject_terms(market)
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            if _sentence_mentions_other_entity(sentence, subject_terms):
                continue
            if any(_contains_keyword(sentence.lower(), keyword) for keyword in BINARY_YES_KEYWORDS):
                return Verdict(
                    outcome="YES",
                    confidence=item.similarity,
                    evidence_snippet=sentence.strip()[:280],
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
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            lowered = sentence.lower()
            for option in market.options:
                option_lower = option.strip().lower()
                if not option_lower:
                    continue
                for keyword in ANNOUNCEMENT_KEYWORDS:
                    option_re = _word_boundary(option_lower)
                    keyword_re = _word_boundary(keyword)
                    pattern = re.compile(
                        rf"{option_re}.{{0,40}}{keyword_re}|"
                        rf"{keyword_re}.{{0,40}}{option_re}"
                    )
                    if pattern.search(lowered):
                        return Verdict(
                            outcome=option,
                            confidence=item.similarity,
                            evidence_snippet=sentence.strip()[:280],
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_verdict_engine.py -v`
Expected: PASS (12 passed — the original 8 from Task 9 plus the 4 added here).

Run: `pytest -v`
Expected: full suite passes with no regressions.

- [ ] **Step 5: Empirically re-verify against the real article**

Run this to confirm the fix actually resolves the specific production false positive (not just the synthetic test fixtures):

```python
python -c "
import requests, trafilatura
from datetime import date, timedelta
from resolution_finder.models import Market, ArticleRef, RankedArticle
from resolution_finder.verdict_engine import decide

url = 'https://www.yahoo.com/news/politics/articles/clarity-act-supporters-vs-opponents-131821823.html'
resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
text = trafilatura.extract(resp.text)

market = Market(
    id='clarity-act-2026',
    title='Will the CLARITY act be signed into law in 2026?',
    description=(
        'This market resolves to \"Yes\" if the Digital Asset Market Clarity Act '
        'of 2025 (H.R. 3633) is approved by both the U.S. House of Representatives '
        'and the U.S. Senate, and is signed into law no later than December 31, 2026, '
        'at 11:59 PM ET. If these conditions are not met by the deadline, the market '
        'resolves to \"No\".'
    ),
    options=[],
    close_date=date.today() + timedelta(days=365),
)
ranked = [RankedArticle(
    article=ArticleRef(url=url, title='t', source_type='credible_backup_secondary'),
    text=text,
    similarity=0.72,
)]
verdict = decide(market, ranked)
print('outcome:', verdict.outcome)
print('snippet:', verdict.evidence_snippet)
"
```

Expected: `outcome` is `UNCLEAR` or `NO_EVIDENCE`, NOT `YES` — confirms the fix works against the real article that caused the original false positive, not just the hand-written test text.

- [ ] **Step 6: Commit**

```bash
git add resolution_finder/verdict_engine.py tests/test_verdict_engine.py
git commit -m "fix: reject keyword matches from unrelated entities or hedged sentences

A real dry run produced a false-positive YES verdict for the CLARITY
Act market: an article compared it to a different bill (the GENIUS
Act), and that unrelated bill's 'signed into law' sentence matched
the keyword check, which previously only checked 'does this phrase
appear anywhere in the article.' Now checks at sentence granularity:
rejects matches in sentences naming a different entity than the
market's own subject, and rejects hedged/hypothetical sentences
('if enacted'). Verified against the real article that caused the
original false positive, not just synthetic test fixtures."
```

---

### Task 18: Peer-market cross-check via Polymarket

> **Note (added after live investigation and a safety finding the user
> weighed in on):** the user asked to search Polymarket/Kalshi for a
> matching market and check if it's already closed, as corroborating
> evidence. Live investigation found:
> - **Polymarket** has a free, no-key, keyword-searchable API
>   (`gamma-api.polymarket.com/public-search?q=...`), verified live, returning
>   real events/markets with `closed`, `umaResolutionStatus`, `outcomes`, and
>   `outcomePrices` fields. A genuinely resolved market reliably shows one
>   outcome price at `"1"` when `umaResolutionStatus == "resolved"` (verified
>   against 5 real high-volume resolved markets).
> - **Kalshi** has NO free-text search endpoint — its public API is
>   organized by `series_ticker`/category only (verified live: `/series`
>   lists categories, `/markets` accepts no `q=`-style parameter). Matching
>   a market by keyword would require bulk-fetching many series and doing
>   local text matching, a meaningfully bigger undertaking. **Not built in
>   this task** — Polymarket only.
> - **Safety finding**: embedding similarity alone is not safe for this.
>   Searching Polymarket for our real CLARITY Act market surfaced a
>   similarly-worded but **completely different bill** — "Guidance Clarity
>   Act of 2025 (S.81)" vs. our "Digital Asset Market Clarity Act (H.R.
>   3633)" — which scored **0.71 cosine similarity**, well above any
>   reasonable threshold. This is the same wrong-subject failure mode Task
>   17 fixed in the verdict engine, now found in a new context before it
>   shipped.
>
> The user's decision on how to proceed: **build the stricter version AND
> require human confirmation** — not similarity score alone, and not
> unverified matches surfaced without scrutiny either. This task therefore:
> 1. Hard-rejects a candidate if it names a **different legislative bill
>    number** than our market does (verified: this alone rejects the real
>    "Guidance Clarity Act" false match, since our market names H.R. 3633
>    and the false match names S.81 — disjoint bill-number sets).
> 2. Hard-rejects a candidate if it names a **different capitalized
>    entity/acronym** not among our market's own subject terms (same
>    `extract_entities`-based approach as Task 17; verified this rejects
>    all 5 real bad candidates found during live testing, including three
>    completely unrelated athlete-transfer markets that surfaced for a
>    "Vinicius Junior" search).
> 3. Only THEN applies the standard similarity threshold as a final filter.
> 4. Regardless of how confident a surviving match looks, the dashboard
>    **always** labels it for mandatory human verification — never
>    presented as confirmed evidence on its own, same spirit as the
>    `official_social` label but for a different reason (cross-platform
>    text matching, not an unverified account).
>
> **Binary (Yes/No) markets only** — mapping option lists across two
> platforms' own outcome structures is a harder problem, deferred (stated
> in the original design discussion, unaffected by the safety finding).

**Files:**
- Create: `resolution_finder/peer_market.py`
- Create: `tests/test_peer_market.py`
- Modify: `resolution_finder/pipeline.py` (check for a peer-market match before the regular evidence pipeline, binary markets only)
- Modify: `resolution_finder/models.py` (source_type comment)
- Modify: `resolution_finder/templates/index.html` (new label branch)
- Modify: `tests/test_pipeline.py`, `tests/test_dashboard.py` (new tests)

**Interfaces:**
- Consumes: `Market`, `Verdict` (Task 1); `extract_entities` (Task 4); `_get_model` (Task 8, `resolution_finder/relevance_ranker.py`); `SIMILARITY_THRESHOLD` (Task 1 config).
- Produces: `find_polymarket_match(market: Market) -> Optional[Verdict]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_peer_market.py`:

```python
# tests/test_peer_market.py
from unittest.mock import patch, MagicMock
from datetime import date
from resolution_finder.models import Market
from resolution_finder.peer_market import (
    find_polymarket_match,
    _has_conflicting_bill_number,
    _has_conflicting_entity,
    _our_identifying_terms,
)

CLARITY_MARKET = Market(
    id="clarity-act-2026",
    title="Will the CLARITY act be signed into law in 2026?",
    description=(
        "This market resolves to \"Yes\" if the Digital Asset Market Clarity Act "
        "of 2025 (H.R. 3633) is approved by both the U.S. House of Representatives "
        "and the U.S. Senate, and is signed into law."
    ),
    options=[],
    close_date=date(2026, 12, 31),
)

VINICIUS_MARKET = Market(
    id="vinicius-transfer-2026",
    title="Which team will Vinicius Junior join next?",
    description="This market will settle based on the next team Vinicius Junior officially joins.",
    options=["Real Madrid", "Arsenal"],
    close_date=date(2026, 9, 1),
)


def test_has_conflicting_bill_number_detects_different_bill():
    # Real false match found via live Polymarket search during planning.
    assert _has_conflicting_bill_number(
        CLARITY_MARKET.description,
        "Will the Guidance Clarity Act of 2025 (S.81) be signed into law?",
    ) is True


def test_has_conflicting_bill_number_false_when_no_bill_number_in_either():
    assert _has_conflicting_bill_number(
        "no bill number here", "also no bill number here",
    ) is False


def test_has_conflicting_entity_detects_different_person():
    # Real false match found via live Polymarket search during planning.
    terms = _our_identifying_terms(VINICIUS_MARKET)
    assert _has_conflicting_entity(terms, "Will Steve Kerr join the Atlanta Hawks in 2026?") is True


def test_has_conflicting_entity_false_when_same_subject():
    terms = _our_identifying_terms(CLARITY_MARKET)
    assert _has_conflicting_entity(
        terms, "Will the Digital Asset Market Clarity Act be signed into law?"
    ) is False


def test_multi_outcome_market_never_checked():
    result = find_polymarket_match(VINICIUS_MARKET)
    assert result is None


@patch("resolution_finder.peer_market.requests.get")
def test_find_polymarket_match_rejects_conflicting_bill_number(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "events": [{
            "slug": "guidance-clarity-act",
            "markets": [{
                "question": "Will the Guidance Clarity Act of 2025 (S.81) be signed into law?",
                "description": "",
                "closed": True,
                "umaResolutionStatus": "resolved",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]',
            }],
        }],
    }
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    result = find_polymarket_match(CLARITY_MARKET)
    assert result is None


@patch("resolution_finder.peer_market.util.cos_sim")
@patch("resolution_finder.peer_market._get_model")
@patch("resolution_finder.peer_market.requests.get")
def test_find_polymarket_match_returns_verdict_for_genuine_match(mock_get, mock_get_model, mock_cos_sim):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "events": [{
            "slug": "clarity-act-hr-3633",
            "markets": [{
                "question": "Will the Digital Asset Market Clarity Act (H.R. 3633) be signed into law in 2026?",
                "description": "Resolves Yes if H.R. 3633 is signed into law.",
                "closed": True,
                "umaResolutionStatus": "resolved",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]',
            }],
        }],
    }
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.return_value = [[0.85]]

    result = find_polymarket_match(CLARITY_MARKET)
    assert result is not None
    assert result.outcome == "YES"
    assert result.source_type == "peer_market"
    assert result.source_url == "https://polymarket.com/event/clarity-act-hr-3633"
    assert "Digital Asset Market Clarity Act" in result.evidence_snippet


@patch("resolution_finder.peer_market.requests.get")
def test_find_polymarket_match_ignores_unresolved_markets(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "events": [{
            "slug": "clarity-act-hr-3633",
            "markets": [{
                "question": "Will the Digital Asset Market Clarity Act (H.R. 3633) be signed into law in 2026?",
                "description": "",
                "closed": False,
                "umaResolutionStatus": "",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.5", "0.5"]',
            }],
        }],
    }
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    result = find_polymarket_match(CLARITY_MARKET)
    assert result is None


@patch("resolution_finder.peer_market.requests.get")
def test_find_polymarket_match_handles_search_failure_gracefully(mock_get):
    import requests
    mock_get.side_effect = requests.ConnectionError("failed")
    result = find_polymarket_match(CLARITY_MARKET)
    assert result is None
```

Add to `tests/test_pipeline.py`:

```python
@patch("resolution_finder.pipeline.find_polymarket_match")
@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_uses_peer_market_match_and_skips_rest_of_pipeline(
    mock_retrieve, mock_extract, mock_rank, mock_sleep, mock_peer_match
):
    mock_peer_match.return_value = Verdict(
        outcome="YES", confidence=0.85,
        evidence_snippet="Resolved on a peer market",
        source_url="https://polymarket.com/event/x",
        source_type="peer_market",
    )

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)

    run_pipeline(FakeMarketProvider(), db_path)

    findings = get_latest_findings(db_path)
    assert findings[0]["outcome"] == "YES"
    assert findings[0]["source_type"] == "peer_market"
    mock_retrieve.assert_not_called()
    os.remove(db_path)
```

(This needs `Verdict` imported in `tests/test_pipeline.py` — add to the existing `from resolution_finder.models import ...` import line.)

Add to `tests/test_dashboard.py`:

```python
def test_index_flags_peer_market_source_for_manual_verification():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)
    verdict = Verdict(outcome="YES", confidence=0.85,
                       evidence_snippet="Resolved \"Yes\" on Polymarket for a similar question",
                       source_url="https://polymarket.com/event/clarity-act",
                       source_type="peer_market")
    save_finding(db_path, "clarity-act-2026", "2026-08-16T00:00:00", verdict)

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"verify this is genuinely the same event" in response.data
    os.remove(db_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_peer_market.py tests/test_pipeline.py tests/test_dashboard.py -v`
Expected: FAIL — `resolution_finder.peer_market` doesn't exist yet.

- [ ] **Step 3: Implement the peer-market module**

```python
# resolution_finder/peer_market.py
import json
import logging
import re
from typing import Optional
from urllib.parse import quote_plus
import requests
from sentence_transformers import util
from resolution_finder.models import Market, Verdict
from resolution_finder.query_builder import extract_entities
from resolution_finder.relevance_ranker import _get_model
from resolution_finder.config import SIMILARITY_THRESHOLD

logger = logging.getLogger(__name__)

POLYMARKET_SEARCH = "https://gamma-api.polymarket.com/public-search?q={query}&limit_per_type=5"

# Legislative bill numbers are the most reliable disambiguator between two
# markets that sound alike but are about different things -- verified
# necessary empirically: embedding similarity alone scored 0.71 between our
# real CLARITY Act (H.R. 3633) market and an unrelated "Guidance Clarity
# Act (S.81)" bill found via live Polymarket search.
BILL_NUMBER_PATTERN = re.compile(
    r"\b(?:H\.R\.|H\.Res\.|H\.Con\.Res\.|H\.J\.Res\.|S\.Res\.|S\.Con\.Res\.|S\.J\.Res\.|S\.)\s?\d+\b",
    re.IGNORECASE,
)
ACRONYM_PATTERN = re.compile(r"\b[A-Z]{2,}\b")


def _extract_bill_numbers(text: str) -> set[str]:
    return {m.strip().upper().replace(" ", "") for m in BILL_NUMBER_PATTERN.findall(text)}


def _has_conflicting_bill_number(our_text: str, peer_text: str) -> bool:
    our_bills = _extract_bill_numbers(our_text)
    peer_bills = _extract_bill_numbers(peer_text)
    if not our_bills or not peer_bills:
        return False
    return our_bills.isdisjoint(peer_bills)


def _our_identifying_terms(market: Market) -> list[str]:
    combined = f"{market.title} {market.description}"
    terms = list(extract_entities(combined))
    for word in ACRONYM_PATTERN.findall(market.title):
        if word not in terms:
            terms.append(word)
    return terms


def _has_conflicting_entity(our_terms: list[str], peer_text: str) -> bool:
    if not our_terms:
        return False
    our_lower = [t.lower() for t in our_terms]
    peer_entities = extract_entities(peer_text) + ACRONYM_PATTERN.findall(peer_text)
    for entity in peer_entities:
        entity_lower = entity.lower()
        if not any(entity_lower in s or s in entity_lower for s in our_lower):
            return True
    return False


def _search_polymarket_events(query: str) -> list[dict]:
    url = POLYMARKET_SEARCH.format(query=quote_plus(query))
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except requests.RequestException:
        logger.warning("Polymarket search failed for query %r", query)
        return []
    return response.json().get("events", [])


def _resolved_outcome(peer_market: dict) -> Optional[str]:
    try:
        outcomes = json.loads(peer_market.get("outcomes", "[]"))
        prices = json.loads(peer_market.get("outcomePrices", "[]"))
    except (ValueError, TypeError):
        return None
    if not outcomes or len(outcomes) != len(prices):
        return None
    best_idx = max(range(len(prices)), key=lambda i: float(prices[i]))
    if float(prices[best_idx]) < 0.9:
        return None
    return outcomes[best_idx]


def find_polymarket_match(market: Market) -> Optional[Verdict]:
    """Best-effort: check whether a similar, already-resolved binary market
    exists on Polymarket, and if so, surface its outcome as a proposed
    verdict. Binary (Yes/No) markets only -- mapping option lists across two
    platforms' own outcome structures is a harder problem, deferred.

    Every match is still labeled for mandatory human verification on the
    dashboard regardless of how confident it looks: text similarity across
    platforms can be fooled by two different markets that happen to be
    worded alike (verified empirically during planning), so this is
    corroborating evidence, never treated as confirmed on its own. The two
    conflict checks below exist specifically because similarity alone
    already proved unsafe.
    """
    if market.options:
        return None

    events = _search_polymarket_events(market.title)
    if not events:
        return None

    our_terms = _our_identifying_terms(market)
    model = _get_model()
    query_text = f"{market.title} {market.description}"
    query_embedding = model.encode(query_text, convert_to_tensor=True)

    best_match = None
    best_similarity = 0.0

    for event in events:
        for peer_market in event.get("markets", []):
            if not peer_market.get("closed"):
                continue
            if peer_market.get("umaResolutionStatus") != "resolved":
                continue

            peer_question = peer_market.get("question", "")
            peer_description = peer_market.get("description", "")
            peer_text = f"{peer_question} {peer_description}"
            if not peer_text.strip():
                continue

            if _has_conflicting_bill_number(market.description, peer_text):
                continue
            if _has_conflicting_entity(our_terms, peer_text):
                continue

            peer_embedding = model.encode(peer_text, convert_to_tensor=True)
            similarity = float(util.cos_sim(query_embedding, peer_embedding)[0][0])
            if similarity >= SIMILARITY_THRESHOLD and similarity > best_similarity:
                best_similarity = similarity
                best_match = (event, peer_market, peer_question)

    if best_match is None:
        return None

    event, peer_market, peer_question = best_match
    outcome = _resolved_outcome(peer_market)
    if outcome is None:
        return None

    outcome_lower = outcome.strip().lower()
    if outcome_lower == "yes":
        mapped_outcome = "YES"
    elif outcome_lower == "no":
        mapped_outcome = "NO"
    else:
        return None

    slug = event.get("slug") or peer_market.get("slug", "")
    url = f"https://polymarket.com/event/{slug}" if slug else None

    return Verdict(
        outcome=mapped_outcome,
        confidence=min(best_similarity, 0.9),
        evidence_snippet=f"Resolved \"{outcome}\" on Polymarket for a similar question: \"{peer_question}\"",
        source_url=url,
        source_type="peer_market",
    )
```

- [ ] **Step 4: Wire the peer-market check into the pipeline**

In `resolution_finder/pipeline.py`, add the import:

```python
from resolution_finder.peer_market import find_polymarket_match
```

At the top of `_scan_market`, before `queries = build_queries(market)`:

```python
def _scan_market(
    market: Market,
    db_path: str,
    run_timestamp: str,
    verdict_engine: VerdictEngine,
) -> None:
    peer_verdict = find_polymarket_match(market)
    if peer_verdict is not None:
        save_finding(db_path, market.id, run_timestamp, peer_verdict)
        return

    queries = build_queries(market)
    ...  # rest of the function unchanged
```

- [ ] **Step 5: Add the dashboard label**

In `resolution_finder/templates/index.html`, add a branch to the existing label logic:

```html
          {% elif f.source_type == "peer_market" %}
            {% set label = "resolved on a similar prediction market — verify this is genuinely the same event" %}
```

(Add this as another `{% elif %}` alongside the existing `official_social` and `credible_backup_secondary` branches, before the final `{% else %}`.)

- [ ] **Step 6: Update the ArticleRef/Verdict source_type comment**

In `resolution_finder/models.py`:

```python
    source_type: str  # "primary", "credible_backup", "credible_backup_secondary", "official_social", "peer_market", or "general"
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_peer_market.py tests/test_pipeline.py tests/test_dashboard.py -v`
Expected: PASS.

Run: `pytest -v`
Expected: full suite passes.

- [ ] **Step 8: Empirically re-verify against live Polymarket data**

Run this to confirm the conflict checks still correctly reject the real bad matches found during planning, against the live API (not mocks):

```python
python -c "
from resolution_finder.models import Market
from resolution_finder.peer_market import find_polymarket_match
from datetime import date

clarity = Market(
    id='clarity-act-2026',
    title='Will the CLARITY act be signed into law in 2026?',
    description=(
        'This market resolves to \"Yes\" if the Digital Asset Market Clarity Act '
        'of 2025 (H.R. 3633) is approved by both the U.S. House of Representatives '
        'and the U.S. Senate, and is signed into law.'
    ),
    options=[],
    close_date=date(2026, 12, 31),
)
result = find_polymarket_match(clarity)
print('CLARITY result:', result)
"
```

Expected: `None`, or a genuine match with `source_url` pointing to an actual H.R. 3633-related Polymarket event — NOT the "Guidance Clarity Act (S.81)" market found during planning. Report the actual output honestly either way; `None` is a correct, safe result if Polymarket has no genuinely matching resolved market for this specific bill right now.

- [ ] **Step 9: Commit**

```bash
git add resolution_finder/peer_market.py resolution_finder/pipeline.py resolution_finder/models.py resolution_finder/templates/index.html tests/test_peer_market.py tests/test_pipeline.py tests/test_dashboard.py
git commit -m "feat: add peer-market cross-check via Polymarket

Searches Polymarket for a similar, already-resolved binary market as
corroborating evidence. Embedding similarity alone was verified
unsafe during planning (a same-shaped-different-bill match scored
0.71 similarity), so this hard-rejects candidates naming a different
bill number or a conflicting named entity before similarity is even
considered, and every surviving match is still labeled on the
dashboard for mandatory human verification. Kalshi has no free-text
search API (verified live) and is not included. Multi-outcome markets
are out of scope for this task."
```

---

### Task 19: Per-option verdicts for multi-outcome markets

> **Note (design confirmed with the user in a side conversation, recovered
> from a session crash — see `.superpowers/sdd/2026-08-10-resolution-finder-scanner/progress.md`
> line 93+ for how this branch got here):**
>
> A real dry run against the 5 live markets in `data/markets.json` (Tasks
> 14-18's fetch pipeline, live-verified) surfaced two real gaps, both
> confirmed against actual evidence text retrieved during that run:
>
> 1. **Multi-outcome markets only ever report the winner, never the losers.**
>    `decide()` returns one `Verdict` for the whole market. For
>    `international-2026-champion` (8 teams), if Aurora Gaming is eliminated
>    but the tournament isn't over, there is currently no way to record that
>    — the engine can only say "UNCLEAR" for the entire market or wait for an
>    outright winner. The user wants **one verdict per (market, option)**:
>    when the overall winner is confirmed, every option gets an explicit
>    Yes/No (not just the winner reported); independently of that, a
>    specific option confirmed eliminated resolves to No immediately, before
>    the overall winner is known.
> 2. **`ANNOUNCEMENT_KEYWORDS` misses two common real-world phrasings.** Real
>    evidence retrieved live: NYTimes/The Athletic on Vinicius Junior —
>    *"the forward reached an agreement to renew his contract at Madrid
>    following interest from Arsenal"* (a stay/renewal, not a transfer, but
>    not recognized as a resolving signal at all); BBC Pidgin on the Osun
>    State election — *"INEC declare Govnor Adeleke winner of di election"*
>    (a definitive result, but "declare ... winner" doesn't match the
>    existing "winner is" phrase).
>
> **A known, deliberately out-of-scope limitation, flagged rather than
> fixed:** both real examples above name the entity by a *partial* form —
> "at Madrid" (not "Real Madrid"), "Govnor Adeleke" (not "Ademola Adeleke").
> Option matching in this engine requires the full option string verbatim
> (word-boundary match, unchanged by this task). Loosening that to accept
> partial/surname-only matches is NOT done here: `osun-state-governor-2026`
> has both "Ademola Adeleke" and "Taofeek Adeleke" as separate candidates in
> the same market (see `data/markets.json`) — a bare "Adeleke" match would be
> genuinely ambiguous between two real options, and guessing wrong there is
> worse than staying UNCLEAR. This task fixes the two *specific* phrasings
> above (full-name-present cases) and the per-option data model; fuzzy/
> partial-name matching is a separate, higher-risk task if wanted later.
>
> **The Osun State "resolve to 'Other'" case turns out to need no new
> verdict type.** Its resolution text is *"If the results are not known
> definitively by \[date\], this market will resolve to 'Other'"* — read
> together with the per-option redesign, "Other" just means none of the
> listed candidates won, i.e. every listed option resolves No — exactly the
> same as `international-2026-champion`'s own "if the champion is not one of
> the listed teams, all markets will resolve to 'No'" clause. Both are
> handled by one unified rule: a stated default outcome that names something
> other than "No" and doesn't match any listed option still means "every
> option resolves No" — no `"OTHER"` outcome string needed anywhere. (This
> also fixes `pending-test-scenarios.md`'s separately-noted gap: the old
> `DEFAULT_OUTCOME_PATTERN` trigger list doesn't include "not known", so the
> Osun default clause wouldn't have fired at all — fixed by adding that
> trigger phrase.)

**Files:**
- Modify: `resolution_finder/models.py`
- Modify: `resolution_finder/verdict_engine.py`
- Modify: `resolution_finder/storage.py`
- Modify: `resolution_finder/pipeline.py`
- Modify: `resolution_finder/templates/index.html`
- Modify: `tests/test_verdict_engine.py`
- Modify: `tests/test_storage.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: existing `Market`, `RankedArticle` (Task 1); existing `_word_boundary`, `_split_sentences`, `_sentence_has_hedge`, `_extract_default_outcome` (Task 17, unchanged).
- Produces: `Verdict.option: Optional[str] = None` (new field, defaulted so every existing call site stays valid). `decide(market, ranked_evidence)` return type becomes `Verdict | list[Verdict]`: **unchanged** for binary markets (still a single `Verdict`, `option=None`); for multi-outcome markets, now returns `list[Verdict]` — one entry per option when an overall winner or a default outcome is determined, 0+ entries (one per confirmed-eliminated option) when only partial elimination evidence exists, or a single-element list `[Verdict(outcome="UNCLEAR"|"NO_EVIDENCE", option=None, ...)]` when nothing is known yet. `save_finding`'s signature is unchanged (still takes one `Verdict`) but now also persists `verdict.option`. `get_latest_findings` now returns one row per `(market_id, option)` pair instead of one per `market_id`.

- [ ] **Step 1: Write the failing/changed tests**

Replace the multi-outcome section of `tests/test_verdict_engine.py` — keep everything above `def test_multi_outcome_market_picks_matching_option():` unchanged (all binary-market tests, `CLARITY_MARKET`/`NOBEL_MARKET`/`VINICIUS_MARKET` fixtures), and replace everything from that line to the end of the file with:

```python
INTERNATIONAL_DESCRIPTION = (
    "This market will resolve based on the team officially recognized as the "
    "champion of The International 2026. The winning team's market will "
    "resolve to \"Yes\". All other team markets will resolve to \"No\". If "
    "The International 2026 champion has not been officially determined by "
    "September 6, 2026, 11:59 PM ET, or if the champion is not one of the "
    "listed teams, all markets will resolve to \"No\"."
)

INTERNATIONAL_MARKET = Market(
    id="international-2026-champion",
    title="The International 2026 Champion",
    description=INTERNATIONAL_DESCRIPTION,
    options=["Team Spirit", "Aurora Gaming"],
    close_date=date.today() + timedelta(days=365),
)

OSUN_DESCRIPTION = (
    "This market will resolve according to the listed candidate who wins "
    "the 2026 Osun State gubernatorial elections. If the results are not "
    "known definitively by June 30, 2027, 11:59 PM ET, this market will "
    "resolve to \"Other\"."
)

OSUN_MARKET = Market(
    id="osun-state-governor-2026",
    title="Osun State Gubernatorial Election Winner",
    description=OSUN_DESCRIPTION,
    options=["Ademola Adeleke", "Taofeek Adeleke"],
    close_date=date.today() + timedelta(days=365),
)


def test_multi_outcome_market_full_sweep_on_confirmed_winner():
    # Requirement: once an overall winner is confirmed, every option gets an
    # explicit verdict, not just the winner.
    evidence = [make_ranked(
        "The Norwegian Nobel Committee announced that the prize is awarded to Pope Leo XIV.",
        url="https://nobelprize.org/announcement",
    )]
    verdicts = decide(NOBEL_MARKET, evidence)
    assert isinstance(verdicts, list)
    by_option = {v.option: v.outcome for v in verdicts}
    assert by_option == {
        "Yulia Navalnaya": "NO", "Volodymyr Zelenskyy": "NO", "UNRWA": "NO",
        "Pope Leo XIV": "YES", "Donald Trump": "NO",
    }
    winner = next(v for v in verdicts if v.option == "Pope Leo XIV")
    assert winner.source_url == "https://nobelprize.org/announcement"


def test_multi_outcome_market_unclear_when_no_option_matches():
    evidence = [make_ranked("The Nobel Committee will announce the winner next week.")]
    verdicts = decide(NOBEL_MARKET, evidence)
    assert len(verdicts) == 1
    assert verdicts[0].outcome == "UNCLEAR"
    assert verdicts[0].option is None


def test_multi_outcome_market_applies_stated_no_default_after_deadline():
    past_deadline_market = Market(
        id="nobel-peace-2026", title=NOBEL_MARKET.title, description=NOBEL_DESCRIPTION,
        options=NOBEL_MARKET.options, close_date=date.today() - timedelta(days=1),
    )
    verdicts = decide(past_deadline_market, [])
    assert {v.option for v in verdicts} == set(NOBEL_MARKET.options)
    assert all(v.outcome == "NO" for v in verdicts)


def test_multi_outcome_market_matches_announcement_keyword_as_whole_word():
    evidence = [make_ranked(
        "Official: Arsenal wins the race for Vinicius Junior.",
        url="https://www.bbc.com/sport/1", source_type="credible_backup",
    )]
    verdicts = decide(VINICIUS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Arsenal": "YES", "Real Madrid": "NO"}


def test_multi_outcome_market_ignores_keyword_inside_a_longer_word():
    """"wins" must not fire on "Winston" / "winsome"."""
    evidence = [make_ranked(
        "Arsenal supporter Winston Reid offered a winsome take on the transfer.",
        url="https://www.bbc.com/sport/2", source_type="credible_backup",
    )]
    verdicts = decide(VINICIUS_MARKET, evidence)
    assert len(verdicts) == 1
    assert verdicts[0].outcome == "UNCLEAR"


def test_multi_outcome_market_ignores_option_inside_a_longer_word():
    evidence = [make_ranked(
        "Arsenalization of the transfer market wins few fans.",
        url="https://www.bbc.com/sport/3", source_type="credible_backup",
    )]
    verdicts = decide(VINICIUS_MARKET, evidence)
    assert len(verdicts) == 1
    assert verdicts[0].outcome == "UNCLEAR"


def test_multi_outcome_market_applies_stated_option_default_after_deadline():
    past_deadline_market = Market(
        id="vinicius-transfer-2026", title=VINICIUS_MARKET.title, description=VINICIUS_DESCRIPTION,
        options=VINICIUS_MARKET.options, close_date=date.today() - timedelta(days=1),
    )
    verdicts = decide(past_deadline_market, [])
    assert {v.option: v.outcome for v in verdicts} == {"Real Madrid": "YES", "Arsenal": "NO"}


def test_multi_outcome_market_resolves_yes_on_contract_renewal_language():
    # Real gap found live: NYTimes/The Athletic on Vinicius Junior described
    # him staying at Real Madrid as "reached an agreement to renew his
    # contract", not any of the old ANNOUNCEMENT_KEYWORDS.
    evidence = [make_ranked(
        "Real Madrid have confirmed Vinicius Junior signed a new contract, "
        "ending Arsenal's interest in the forward.",
        url="https://www.nytimes.com/athletic/1", source_type="credible_backup",
    )]
    verdicts = decide(VINICIUS_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {"Real Madrid": "YES", "Arsenal": "NO"}


def test_multi_outcome_market_resolves_yes_on_winner_of_phrase():
    # Real gap found live: BBC Pidgin on the Osun election used "declare ...
    # winner of ..." — "winner of" wasn't in ANNOUNCEMENT_KEYWORDS.
    evidence = [make_ranked(
        "INEC declare Ademola Adeleke winner of the Osun State election.",
        url="https://www.bbc.com/pidgin/1", source_type="credible_backup",
    )]
    verdicts = decide(OSUN_MARKET, evidence)
    assert {v.option: v.outcome for v in verdicts} == {
        "Ademola Adeleke": "YES", "Taofeek Adeleke": "NO",
    }


def test_multi_outcome_market_option_independently_resolves_no_on_elimination():
    # Real scenario: The International 2026 -- Aurora Gaming eliminated,
    # tournament champion still undecided. Must resolve just that option,
    # not force a whole-market guess.
    evidence = [make_ranked(
        "Aurora Gaming was eliminated from The International 2026 in the lower bracket.",
        url="https://www.dexerto.com/dota2/1", source_type="credible_backup",
    )]
    verdicts = decide(INTERNATIONAL_MARKET, evidence)
    assert len(verdicts) == 1
    assert verdicts[0].option == "Aurora Gaming"
    assert verdicts[0].outcome == "NO"


def test_multi_outcome_market_elimination_does_not_claim_the_other_option_won():
    # One option confirmed lost is not evidence the other one won.
    evidence = [make_ranked(
        "Aurora Gaming was eliminated from The International 2026 in the lower bracket.",
        url="https://www.dexerto.com/dota2/1", source_type="credible_backup",
    )]
    verdicts = decide(INTERNATIONAL_MARKET, evidence)
    assert "Team Spirit" not in {v.option for v in verdicts}


def test_multi_outcome_market_named_default_outside_option_list_resolves_all_no():
    # Real scenario: Osun's "resolve to 'Other'" default. "Other" matches
    # none of the listed candidates, so every listed option resolves No --
    # also exercises the new "not known" DEFAULT_OUTCOME_PATTERN trigger.
    past_deadline_market = Market(
        id="osun-state-governor-2026", title=OSUN_MARKET.title, description=OSUN_DESCRIPTION,
        options=OSUN_MARKET.options, close_date=date.today() - timedelta(days=1),
    )
    verdicts = decide(past_deadline_market, [])
    assert {v.option for v in verdicts} == set(OSUN_MARKET.options)
    assert all(v.outcome == "NO" for v in verdicts)
```

- [ ] **Step 2: Run tests to verify the new/changed ones fail**

Run: `pytest tests/test_verdict_engine.py -v`
Expected: FAIL — `decide()` still returns a single `Verdict` for multi-outcome markets today, so every test above that does `for v in verdicts` or checks `v.option` fails (`Verdict` has no `option` attribute yet, and iterating a `Verdict` directly is a `TypeError`).

- [ ] **Step 3: Add the `option` field to `Verdict`**

In `resolution_finder/models.py`, change the `Verdict` dataclass to:

```python
@dataclass
class Verdict:
    outcome: str  # "YES", "NO", "UNCLEAR", or "NO_EVIDENCE"
    confidence: float
    evidence_snippet: Optional[str]
    source_url: Optional[str]
    source_type: Optional[str]
    option: Optional[str] = None  # for a multi-outcome market's per-option verdict,
                                   # the option this verdict is about (e.g. "Aurora
                                   # Gaming"); None for a binary market's verdict, or
                                   # a multi-outcome market's whole-market UNCLEAR/
                                   # NO_EVIDENCE status when no option is determined.
```

- [ ] **Step 4: Rewrite the verdict engine**

Replace the full contents of `resolution_finder/verdict_engine.py`:

```python
# resolution_finder/verdict_engine.py
import re
from datetime import date
from typing import Optional
from resolution_finder.models import Market, RankedArticle, Verdict
from resolution_finder.query_builder import extract_entities

# Generalized: captures a plain Yes/No default (CLARITY Act, Nobel Prize) OR a
# specific named option default (Vinicius Junior -> "Real Madrid") OR a named
# outcome outside the option list (Osun -> "Other"). The trigger phrases
# anchor on deadline-miss language so this doesn't match an unrelated
# "resolves to X" sentence describing the normal win condition. "not known"
# was added after a real market ("are not known definitively by [date] ...
# resolve to 'Other'") didn't match any of the original trigger phrases.
DEFAULT_OUTCOME_PATTERN = re.compile(
    r"(?:not met|has not|have not|not officially|not been|not known)[^.]{0,150}?"
    r"resolve[s]?\s+to\s+\"?([A-Za-z][A-Za-z0-9 .&'-]*?)\"?[.\n]",
    re.IGNORECASE | re.DOTALL,
)

BINARY_YES_KEYWORDS = ["signed into law", "became law", "enacted", "approved by both"]

# "winner of" and the contract-renewal/stay phrases were added after a real
# dry run: BBC Pidgin's "INEC declare Ademola Adeleke winner of the ..."
# matched none of the original phrases ("winner is" != "winner of"), and
# NYTimes/The Athletic describing Vinicius Junior staying at Real Madrid as
# "reached an agreement to renew his contract" isn't an "announcement"
# phrase at all in the original list, which was written for prize/award
# language only. NOTE: both real examples actually named the entity
# partially ("at Madrid", "Govnor Adeleke") rather than by the full option
# string ("Real Madrid", "Ademola Adeleke") -- that partial-name gap is NOT
# fixed here (see Task 19's design note): a bare surname can be genuinely
# ambiguous between two listed options (e.g. osun-state-governor-2026 has
# both "Ademola Adeleke" and "Taofeek Adeleke"), so this only matches the
# full option string, same as before.
ANNOUNCEMENT_KEYWORDS = [
    "awarded to", "wins", "winner is", "winner of", "named recipient", "recipient is",
    "signed a new contract", "reached an agreement to renew", "contract extension",
    "renewed his contract", "renewed her contract", "extended his contract", "extended her contract",
]

# A specific option confirmed to have LOST resolves that option alone to No,
# independently of whether the overall market winner is known yet (e.g. a
# team eliminated partway through a tournament that's still ongoing).
ELIMINATION_KEYWORDS = ["eliminated", "eliminated from", "knocked out", "lost to", "out of the tournament"]

# The `(?<![A-Z]\.)` lookbehind is load-bearing — do NOT "simplify" it away.
# Without it, a period preceded by a single capital letter (the "S." in "U.S.",
# the "R." in "H.R. 3633") counts as a sentence boundary, and the split lands
# BETWEEN a disqualifying signal and the keyword: "The GENIUS Act was passed by
# the U.S. Senate and signed into law in July 2025." becomes "...the U.S." +
# "Senate and signed into law in July 2025." — the second fragment carries the
# keyword with no "GENIUS" left in it, so the wrong-subject veto never sees the
# other entity and the exact production false positive this module exists to
# prevent comes right back. This domain is US legislation, so "U.S. Senate",
# "U.S. House" and "H.R. ####" are everywhere, including in market descriptions.
# Known residual gap (accepted): Title-case abbreviations like "Sen.", "Rep."
# and "Jan." still split, since matching those needs a real abbreviation list.
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<![A-Z]\.)(?<=[.!?])\s+")

# Words/phrases that turn a sentence hypothetical or negated, e.g. "if
# enacted" or "has not been signed" — a keyword match inside one of these
# doesn't describe something that actually happened.
NEGATION_HEDGE_WORDS = [
    "not ", "n't ", "never ", "without ", "fails to", "failed to",
    "yet to", "has yet", "remains uncertain", "uncertain", "unclear",
    "unlikely", "pending", "awaiting", "no vote", "not scheduled",
    "if ", "unless ", "would be", "could be", "might be",
]

# A bare acronym (e.g. "CLARITY") that Task 4's extract_entities won't catch
# on its own, since that regex requires 2+ consecutive capitalized words and
# a title like "Will the CLARITY act..." has a lowercase word right after it.
ACRONYM_PATTERN = re.compile(r"\b[A-Z]{2,}\b")


def _word_boundary(phrase: str) -> str:
    r"""Regex source matching `phrase` only as whole words.

    Keywords are matched on word boundaries rather than as bare substrings so
    that e.g. "wins" does not fire on "Winston" — a real risk now that market
    options include short common words like "Arsenal".

    `\b` is only added on an edge that is actually a word character; a phrase
    ending in punctuation (an option like "Acme Inc.") would otherwise produce
    a pattern that can never match.
    """
    escaped = re.escape(phrase)
    prefix = r"\b" if phrase[:1].isalnum() or phrase[:1] == "_" else ""
    suffix = r"\b" if phrase[-1:].isalnum() or phrase[-1:] == "_" else ""
    return prefix + escaped + suffix


def _contains_keyword(text: str, keyword: str) -> bool:
    return re.search(_word_boundary(keyword), text) is not None


def _split_sentences(text: str) -> list[str]:
    return [s for s in SENTENCE_SPLIT_PATTERN.split(text) if s.strip()]


def _sentence_has_hedge(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(word in lowered for word in NEGATION_HEDGE_WORDS)


def _subject_terms(market: Market) -> list[str]:
    """Distinctive terms identifying this market's own subject, so a keyword
    match about a different named entity mentioned elsewhere in the same
    article (e.g. a comparable law cited for context) isn't mistaken for
    evidence about this market."""
    combined = f"{market.title} {market.description}"
    terms = list(extract_entities(combined))
    for word in ACRONYM_PATTERN.findall(market.title):
        if word not in terms:
            terms.append(word)
    return terms


def _sentence_mentions_other_entity(sentence: str, subject_terms: list[str]) -> bool:
    """True if the sentence names a capitalized entity/acronym that isn't
    (even partially) one of this market's own subject terms — a signal the
    sentence is about something else. Empty subject_terms means we have no
    way to tell our own subject apart, so never reject on this basis alone.
    """
    if not subject_terms:
        return False
    subject_lower = [t.lower() for t in subject_terms]
    sentence_entities = extract_entities(sentence) + ACRONYM_PATTERN.findall(sentence)
    for entity in sentence_entities:
        entity_lower = entity.lower()
        if not any(entity_lower in s or s in entity_lower for s in subject_lower):
            return True
    return False


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
    subject_terms = _subject_terms(market)
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            if _sentence_mentions_other_entity(sentence, subject_terms):
                continue
            if any(_contains_keyword(sentence.lower(), keyword) for keyword in BINARY_YES_KEYWORDS):
                return Verdict(
                    outcome="YES",
                    confidence=item.similarity,
                    evidence_snippet=sentence.strip()[:280],
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


def _match_option_keyword(lowered_sentence: str, option_lower: str, keywords: list[str]) -> bool:
    option_re = _word_boundary(option_lower)
    for keyword in keywords:
        keyword_re = _word_boundary(keyword)
        pattern = re.compile(rf"{option_re}.{{0,40}}{keyword_re}|{keyword_re}.{{0,40}}{option_re}")
        if pattern.search(lowered_sentence):
            return True
    return False


def _decide_multi_outcome(market: Market, ranked_evidence: list[RankedArticle]) -> list[Verdict]:
    winner: Optional[Verdict] = None
    eliminated: dict[str, Verdict] = {}

    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            lowered = sentence.lower()
            for option in market.options:
                option_lower = option.strip().lower()
                if not option_lower:
                    continue

                if winner is None and _match_option_keyword(lowered, option_lower, ANNOUNCEMENT_KEYWORDS):
                    winner = Verdict(
                        outcome="YES", option=option, confidence=item.similarity,
                        evidence_snippet=sentence.strip()[:280],
                        source_url=item.article.url, source_type=item.article.source_type,
                    )

                if option not in eliminated and _match_option_keyword(lowered, option_lower, ELIMINATION_KEYWORDS):
                    eliminated[option] = Verdict(
                        outcome="NO", option=option, confidence=item.similarity,
                        evidence_snippet=sentence.strip()[:280],
                        source_url=item.article.url, source_type=item.article.source_type,
                    )

    if winner is not None:
        # Overall winner confirmed: every option gets an explicit verdict.
        results = [winner]
        for option in market.options:
            if option == winner.option:
                continue
            if option in eliminated:
                results.append(eliminated[option])
            else:
                results.append(Verdict(
                    outcome="NO", option=option, confidence=winner.confidence,
                    evidence_snippet=winner.evidence_snippet,
                    source_url=winner.source_url, source_type=winner.source_type,
                ))
        return results

    if eliminated:
        # Partial resolution: only the options confirmed lost so far. The
        # rest of the market stays unreported (still genuinely pending) --
        # not spammed with an UNCLEAR row for every remaining option.
        return list(eliminated.values())

    default_outcome = _extract_default_outcome(market.description)
    if default_outcome and date.today() > market.close_date:
        matching_option = (
            next((opt for opt in market.options if opt.lower() == default_outcome.lower()), None)
            if default_outcome != "NO" else None
        )
        snippet = "Deadline passed with no matching evidence; applying stated default."
        if matching_option:
            results = [Verdict(outcome="YES", option=matching_option, confidence=0.5,
                                evidence_snippet=snippet, source_url=None, source_type=None)]
            results += [
                Verdict(outcome="NO", option=opt, confidence=0.5, evidence_snippet=snippet,
                        source_url=None, source_type=None)
                for opt in market.options if opt != matching_option
            ]
            return results
        # Either an explicit "No" default, or a named default that matches
        # none of the listed options (e.g. Osun's "Other") -- both mean the
        # same thing for a per-option verdict: nothing on the list wins.
        return [
            Verdict(outcome="NO", option=opt, confidence=0.5, evidence_snippet=snippet,
                    source_url=None, source_type=None)
            for opt in market.options
        ]

    if ranked_evidence:
        top = ranked_evidence[0]
        return [Verdict(outcome="UNCLEAR", option=None, confidence=top.similarity,
                         evidence_snippet=top.text[:280], source_url=top.article.url,
                         source_type=top.article.source_type)]

    return [Verdict(outcome="NO_EVIDENCE", option=None, confidence=0.0, evidence_snippet=None,
                     source_url=None, source_type=None)]


def decide(market: Market, ranked_evidence: list[RankedArticle]) -> Verdict | list[Verdict]:
    if market.options:
        return _decide_multi_outcome(market, ranked_evidence)
    return _decide_binary(market, ranked_evidence)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_verdict_engine.py -v`
Expected: PASS (all binary tests unchanged and passing, all multi-outcome tests from Step 1 passing).

- [ ] **Step 6: Add `option` to storage and make "latest" per-option**

In `resolution_finder/storage.py`:

1. Add `option TEXT` to the `findings` table in `SCHEMA`:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    option TEXT,
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

2. In `save_finding`, insert `verdict.option`:

```python
def save_finding(db_path: str, market_id: str, run_timestamp: str, verdict: Verdict) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO findings
                (market_id, option, run_timestamp, outcome, confidence, evidence_snippet,
                 source_url, source_type, review_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending')
            """,
            (market_id, verdict.option, run_timestamp, verdict.outcome, verdict.confidence,
             verdict.evidence_snippet, verdict.source_url, verdict.source_type),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()
```

3. Add `"option"` to `_row_to_dict`:

```python
def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "market_id": row["market_id"],
        "option": row["option"],
        "run_timestamp": row["run_timestamp"],
        "outcome": row["outcome"],
        "confidence": row["confidence"],
        "evidence_snippet": row["evidence_snippet"],
        "source_url": row["source_url"],
        "source_type": row["source_type"],
        "review_status": row["review_status"],
    }
```

4. Change `get_latest_findings` to group by `(market_id, option)` instead of just `market_id` — SQLite's `IS` operator is used instead of `=` for the option join since `NULL = NULL` is never true in SQL, but `NULL IS NULL` is:

```python
def get_latest_findings(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT f.* FROM findings f
            INNER JOIN (
                SELECT market_id, option, MAX(run_timestamp) AS max_ts
                FROM findings GROUP BY market_id, option
            ) latest
            ON f.market_id = latest.market_id
               AND f.option IS latest.option
               AND f.run_timestamp = latest.max_ts
            ORDER BY f.market_id, f.option, f.confidence DESC
            """
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()
```

`get_history`, `set_review_status`, `get_setting`, `set_setting` are unchanged.

- [ ] **Step 7: Add storage tests for per-option findings**

Add to `tests/test_storage.py`:

```python
def test_get_latest_findings_returns_one_row_per_option():
    db_path = make_temp_db()
    v_winner = Verdict(outcome="YES", confidence=0.8, evidence_snippet="won",
                        source_url="https://reuters.com/x", source_type="credible_backup",
                        option="Pope Leo XIV")
    v_loser = Verdict(outcome="NO", confidence=0.8, evidence_snippet="lost",
                       source_url="https://reuters.com/x", source_type="credible_backup",
                       option="Donald Trump")
    save_finding(db_path, "nobel-peace-2026", "2026-08-17T00:00:00", v_winner)
    save_finding(db_path, "nobel-peace-2026", "2026-08-17T00:00:00", v_loser)

    latest = get_latest_findings(db_path)
    assert len(latest) == 2
    assert {row["option"]: row["outcome"] for row in latest} == {
        "Pope Leo XIV": "YES", "Donald Trump": "NO",
    }
    os.remove(db_path)


def test_get_latest_findings_tracks_each_option_independently_across_runs():
    db_path = make_temp_db()
    v1 = Verdict(outcome="NO", confidence=0.6, evidence_snippet="eliminated",
                 source_url="https://dexerto.com/x", source_type="credible_backup",
                 option="Aurora Gaming")
    save_finding(db_path, "international-2026-champion", "2026-08-17T00:00:00", v1)

    v2 = Verdict(outcome="UNCLEAR", confidence=0.3, evidence_snippet="still undecided",
                 source_url=None, source_type=None)
    save_finding(db_path, "international-2026-champion", "2026-08-18T00:00:00", v2)

    latest = get_latest_findings(db_path)
    market_rows = [r for r in latest if r["market_id"] == "international-2026-champion"]
    assert len(market_rows) == 2
    assert {r["option"]: r["outcome"] for r in market_rows} == {
        "Aurora Gaming": "NO", None: "UNCLEAR",
    }
    os.remove(db_path)
```

- [ ] **Step 8: Run storage tests**

Run: `pytest tests/test_storage.py -v`
Expected: PASS (existing tests unaffected — `verdict.option` defaults to `None`, grouping by `(market_id, None)` behaves the same as the old `market_id`-only grouping for every existing test).

- [ ] **Step 9: Make the pipeline save every verdict when the engine returns a list**

In `resolution_finder/pipeline.py`, change the end of `_scan_market`:

```python
    ranked = rank_by_relevance(market, articles_with_text)
    verdicts = verdict_engine(market, ranked)
    if not isinstance(verdicts, list):
        verdicts = [verdicts]
    for verdict in verdicts:
        save_finding(db_path, market.id, run_timestamp, verdict)
```

(This replaces the previous two lines: `verdict = verdict_engine(market, ranked)` / `save_finding(db_path, market.id, run_timestamp, verdict)`.)

Add to `tests/test_pipeline.py`:

```python
@patch("resolution_finder.pipeline.time.sleep")
@patch("resolution_finder.pipeline.rank_by_relevance")
@patch("resolution_finder.pipeline.extract_article_text")
@patch("resolution_finder.pipeline.retrieve_evidence")
def test_run_pipeline_saves_every_verdict_when_engine_returns_a_list(
    mock_retrieve, mock_extract, mock_rank, mock_sleep
):
    """A multi-outcome market's verdict_engine can return one Verdict per
    option instead of a single Verdict -- the pipeline must persist all of
    them, not just the first."""
    mock_retrieve.return_value = []
    mock_rank.return_value = []

    def fake_engine(market, ranked):
        return [
            Verdict(outcome="YES", confidence=0.9, evidence_snippet="won",
                    source_url="https://example.com/a", source_type="primary", option="Team A"),
            Verdict(outcome="NO", confidence=0.9, evidence_snippet="won",
                    source_url="https://example.com/a", source_type="primary", option="Team B"),
        ]

    db_path = temp_db_path()
    run_pipeline(FakeMarketProvider(), db_path, verdict_engine=fake_engine, peer_checker=NO_PEER_MATCH)

    findings = get_latest_findings(db_path)
    assert len(findings) == 2
    assert {f["option"]: f["outcome"] for f in findings} == {"Team A": "YES", "Team B": "NO"}
    os.remove(db_path)
```

- [ ] **Step 10: Run pipeline tests**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS (existing tests unaffected — a single-`Verdict`-returning engine gets wrapped in a one-element list, same observable behavior as before).

- [ ] **Step 11: Show the option in the dashboard**

In `resolution_finder/templates/index.html`, add an "Option" column:

```html
    <tr>
      <th>Market</th><th>Option</th><th>Outcome</th><th>Confidence</th><th>Evidence</th>
      <th>Source</th><th>Status</th><th>Action</th>
    </tr>
    {% for f in findings %}
    <tr>
      <td>{{ f.market_id }}</td>
      <td>{{ f.option or "-" }}</td>
      <td>{{ f.outcome }}</td>
```

(Everything else in the row and the rest of the template is unchanged.)

Add to `tests/test_dashboard.py`:

```python
def test_index_shows_option_for_multi_outcome_finding():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)
    init_db(db_path)
    verdict = Verdict(outcome="NO", confidence=0.6,
                       evidence_snippet="Aurora Gaming was eliminated from The International 2026.",
                       source_url="https://www.dexerto.com/dota2/x", source_type="credible_backup",
                       option="Aurora Gaming")
    save_finding(db_path, "international-2026-champion", "2026-08-17T00:00:00", verdict)

    app = create_app(db_path)
    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"Aurora Gaming" in response.data
    os.remove(db_path)
```

- [ ] **Step 12: Run dashboard tests, then the full suite**

Run: `pytest tests/test_dashboard.py -v`
Expected: PASS.

Run: `pytest -v`
Expected: full suite passes with no regressions.

- [ ] **Step 13: Empirically re-verify against the real evidence from the live dry run**

Run this to confirm the two keyword fixes work against text matching what was actually retrieved live for `vinicius-transfer-2026` and `osun-state-governor-2026` (see progress.md / the recovered session transcript for the originals):

```python
python -c "
from datetime import date, timedelta
from resolution_finder.models import Market, ArticleRef, RankedArticle
from resolution_finder.verdict_engine import decide
from resolution_finder.market_provider import JsonFileMarketProvider

markets = {m.id: m for m in JsonFileMarketProvider('data/markets.json').get_unresolved_markets()}

vinicius = markets['vinicius-transfer-2026']
ranked = [RankedArticle(
    article=ArticleRef(url='https://www.nytimes.com/athletic/1', title='t', source_type='credible_backup'),
    text='Real Madrid have confirmed Vinicius Junior signed a new contract, ending Arsenal interest.',
    similarity=0.8,
)]
for v in decide(vinicius, ranked):
    print('vinicius:', v.option, v.outcome)

osun = markets['osun-state-governor-2026']
ranked = [RankedArticle(
    article=ArticleRef(url='https://www.bbc.com/pidgin/1', title='t', source_type='credible_backup'),
    text='INEC declare Ademola Adeleke winner of the Osun State election.',
    similarity=0.6,
)]
for v in decide(osun, ranked):
    print('osun:', v.option, v.outcome)
"
```

Expected: `vinicius: Real Madrid YES` plus `vinicius: Arsenal NO`; `osun: Ademola Adeleke YES` plus 10 `NO` lines for the other candidates. Report the actual output honestly. (This uses the full option name for Adeleke, per the flagged partial-name-matching limitation — it will NOT resolve on the real BBC Pidgin text alone, which only says "Govnor Adeleke".)

- [ ] **Step 14: Commit**

```bash
git add resolution_finder/models.py resolution_finder/verdict_engine.py resolution_finder/storage.py resolution_finder/pipeline.py resolution_finder/templates/index.html tests/test_verdict_engine.py tests/test_storage.py tests/test_pipeline.py tests/test_dashboard.py
git commit -m "feat: per-option verdicts for multi-outcome markets

Multi-outcome markets now get one verdict per option instead of one
for the whole market: a confirmed overall winner produces an explicit
Yes/No for every option, and a specific option confirmed eliminated
resolves to No independently, before the overall winner is known.
Also fixes two real keyword gaps found in a live dry run: contract-
renewal/stay language ('reached an agreement to renew') and 'winner
of' phrasing weren't recognized as resolving signals. A named default
outcome outside the listed options (e.g. 'Other') now means every
option resolves No, the same as an explicit No default -- no new
outcome type needed. Partial/surname-only entity matching (e.g. 'at
Madrid', 'Govnor Adeleke' in the real evidence that surfaced these
gaps) is a known, deliberately deferred limitation: option matching
still requires the full option string, since a bare surname can be
genuinely ambiguous between two listed candidates in the same market."
```

---

### Task 20: Broaden the credible-outlet whitelist to more topic verticals

> **Note (design context):** `data/markets.json` now covers far more than
> the original wire-service-news topics (politics, general world news) --
> `TIER2_OUTLETS`/`TIER2_SECONDARY_OUTLETS` (`resolution_finder/
> source_config.py`) were built and tuned against those, so a real crypto,
> sports, science, entertainment, or tech market frequently has zero
> in-whitelist coverage even when real reporting exists (demonstrated
> live earlier: a general web search found real coverage of a Dota 2
> esports result on GosuGamers, a site not in either tier, so the
> in-scope pipeline structurally could not see it regardless of how fresh
> the news was).
>
> This task only ADDS outlets to `TIER2_SECONDARY_OUTLETS` -- the lower-
> reliability, dashboard-flagged tier already used for Forbes/Goal.com/
> Yahoo, matching the existing rationale that vertical/niche outlets have
> "broader coverage, looser editorial standards" than the primary wire-
> service tier. None of the primary `TIER2_OUTLETS` are touched. No new
> code paths are needed -- `evidence_retriever.py`'s `_outlet_tier`
> function already checks both lists identically regardless of topic, so
> this is purely a data addition, verified live per this file's own
> documented policy (see the comment already at the top of
> `source_config.py`, the INEC-SSL-cert and msn.com precedents).
>
> **Mandatory for every candidate domain below, before it is added:**
> 1. Check `is_allowed_by_robots_txt` (already in `article_extractor.py`)
>    against the domain -- do NOT add a domain robots.txt disallows for
>    generic bots.
> 2. Do one real live fetch of an actual article page on that domain
>    (not just the homepage) through `extract_article_text` and confirm
>    it returns real, non-empty extracted text -- not a JS app shell with
>    no content (the msn.com precedent this file already documents).
> 3. Only add domains that pass BOTH checks. Document any candidate that
>    fails either check as a code comment next to the list, the same way
>    `msn.com`'s removal and INEC's SSL failure are already documented at
>    the top of this file -- do not just silently drop a failed candidate
>    with no record.
>
> **Candidate domains to verify and add** (grouped by the vertical they
> fill a real gap for; pick a real article URL on each domain to test
> against, not the bare homepage):
> - Sports (general, beyond the existing soccer-only Goal.com): `espn.com`, `skysports.com`
> - Esports specifically (the demonstrated live gap): `dexerto.com`, `dotesports.com`
> - Crypto: `coindesk.com`, `cointelegraph.com`
> - Science: `nature.com`, `scientificamerican.com`
> - Finance/business: `cnbc.com`, `marketwatch.com`
> - Entertainment: `variety.com`, `hollywoodreporter.com`
> - Technology: `techcrunch.com`, `theverge.com`, `arstechnica.com`
> - Politics (thinner gap, already well covered, one addition): `axios.com`
>
> Weather has no clear additional candidate -- general wire services
> (Reuters/AP/BBC, already in `TIER2_OUTLETS`) already cover major
> weather events adequately, and dedicated weather sites are forecast
> tools, not reporting outlets. Do not force an addition here; note in
> the commit if none was found to be a genuine fit.

**Files:**
- Modify: `resolution_finder/source_config.py`
- Modify: `tests/test_source_config.py` (only if a new test is actually needed -- see Step 1; this task is primarily a data change to an existing list already covered by `evidence_retriever.py`'s existing tier-tagging tests)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new -- `TIER2_SECONDARY_OUTLETS` keeps its existing `list[str]` shape and existing consumers (`evidence_retriever.py`'s `_outlet_tier`) are unchanged.

- [ ] **Step 1: Live-verify every candidate domain**

For each candidate domain listed in the design note above, run this (adjust the URL to a real, currently-live article on that domain -- search the outlet's own site for one, don't guess a URL):

```python
python -c "
from resolution_finder.article_extractor import is_allowed_by_robots_txt, extract_article_text
url = 'https://www.espn.com/REPLACE-WITH-A-REAL-ARTICLE-URL'
print('robots.txt allows:', is_allowed_by_robots_txt(url))
text = extract_article_text(url)
print('extracted chars:', len(text) if text else 0)
print(text[:300] if text else '(nothing extracted)')
"
```

Expected: `robots.txt allows: True` and a real, non-trivial `extracted chars` count (a few hundred+) with recognizable article prose in the printed preview -- for every domain that gets added. Record the actual result (pass/fail, and why for any fail) for all candidates in the design note's list before writing any code.

- [ ] **Step 2: Add the domains that passed to `TIER2_SECONDARY_OUTLETS`**

In `resolution_finder/source_config.py`, add the verified-passing domains to the existing list, keeping the file's existing comment style (explain any per-domain caveat the way `msn.com`'s removal already is documented):

```python
TIER2_SECONDARY_OUTLETS = [
    "forbes.com",
    "goal.com",
    "yahoo.com",
    # Task 20: verified live (robots.txt + real article extraction) --
    # see docs/superpowers/plans/2026-08-10-resolution-finder-scanner.md
    # Task 20 for the full verification record.
    "espn.com",
    "skysports.com",
    "dexerto.com",
    "dotesports.com",
    "coindesk.com",
    "cointelegraph.com",
    "nature.com",
    "scientificamerican.com",
    "cnbc.com",
    "marketwatch.com",
    "variety.com",
    "hollywoodreporter.com",
    "techcrunch.com",
    "theverge.com",
    "arstechnica.com",
    "axios.com",
]
```

(This is the candidate list assuming everything passes Step 1 -- remove any domain that actually failed live verification, and add a code comment next to the list explaining why, matching the file's existing documentation pattern for excluded domains.)

- [ ] **Step 3: Run the existing test suite to confirm no regressions**

Run: `pytest tests/test_source_config.py tests/test_evidence_retriever.py -v`
Expected: PASS -- these outlets flow through the exact same `_outlet_tier`/`credible_backup_secondary` logic already tested for Forbes/Goal.com/Yahoo, so no new test should be strictly required. If you find a genuine gap in existing coverage (e.g. nothing currently asserts a `TIER2_SECONDARY_OUTLETS` entry gets tagged correctly at all, only that a *specific* domain does), add one small parametrized test to `tests/test_source_config.py` rather than one test per domain.

Run: `pytest -v`
Expected: full suite passes, no regressions.

- [ ] **Step 4: Empirically re-verify against the real esports gap that motivated this task**

```python
python -c "
from datetime import date, timedelta
from resolution_finder.models import Market
from resolution_finder.evidence_retriever import retrieve_evidence
from resolution_finder.query_builder import build_queries

market = Market(
    id='international-2026-champion',
    title='The International 2026 Champion',
    description='This market will resolve based on the team officially recognized as the champion of The International 2026.',
    options=['Team Spirit', 'Aurora Gaming'],
    close_date=date.today() + timedelta(days=30),
)
evidence = retrieve_evidence(market, build_queries(market))
tiers = sorted(set(e.source_type for e in evidence))
print('source types found:', tiers)
print('any dexerto/dotesports hit:', any('dexerto.com' in (e.source_domain or '') or 'dotesports.com' in (e.source_domain or '') for e in evidence))
"
```

Expected: report the actual output honestly. A hit from `dexerto.com`/`dotesports.com` tagged `credible_backup_secondary` would directly confirm this task closes the gap that motivated it; `None`/no hit is still useful signal (Google/Bing News RSS may simply not index that specific query today) and should be reported as such, not treated as a failure of this task if Step 1's live verification already independently confirmed the domain is fetchable.

- [ ] **Step 5: Commit**

```bash
git add resolution_finder/source_config.py tests/test_source_config.py
git commit -m "feat: broaden the credible-outlet whitelist to more topic verticals

data/markets.json now spans crypto, sports/esports, science, finance,
entertainment, and tech markets well beyond the original wire-service-
news scope TIER2_OUTLETS/TIER2_SECONDARY_OUTLETS were tuned for --
demonstrated live earlier when a general web search found real esports
coverage (GosuGamers) on a domain neither tier recognized. Adds
verified-live (robots.txt + real article extraction, this file's
existing documented policy) vertical outlets to the existing
lower-reliability TIER2_SECONDARY_OUTLETS tier, alongside Forbes/
Goal.com/Yahoo -- no new code paths, primary TIER2_OUTLETS untouched."
```

---

### Task 21: Cascading date-threshold multi-outcome markets

> **Note (design confirmed with the user before writing this brief):**
>
> Some multi-outcome markets have options that are dates, not named
> entities — e.g. "Will X happen by [date]?" split into "August 1, 2026",
> "September 1, 2026", "October 1, 2026" as separate options. These are
> NOT independent options like team names: they are cumulative thresholds.
> If the event happens by an earlier date, every later (looser) date also
> resolves YES; if a date's own deadline passes with nothing resolved for
> it, it resolves NO independently of the market's overall `close_date`.
>
> **Explicitly in scope for this task:** per-option elapsed-deadline
> handling only — an option whose own date has passed, with no evidence
> resolving it, resolves NO; an option whose date hasn't arrived yet, with
> no evidence, stays UNCLEAR. This is the piece the user wants validated
> first, against real currently-pending markets (nothing resolved yet).
>
> **Explicitly OUT of scope, deferred to a follow-up task:** the richer
> case where evidence confirms the event actually happened, and that
> confirmation needs to cascade YES across every option whose date hasn't
> elapsed yet (and NO for every option whose date had already elapsed
> before the event happened). This needs to know roughly *when* the event
> happened relative to each already-elapsed threshold, which the existing
> keyword-matching evidence layer doesn't extract — likely solved later by
> comparing against the scanner's own run history over time (Task 19's
> storage already supports a later run superseding an earlier verdict for
> the same option — see point 3 below) rather than by parsing an exact
> date out of a single article. Do not attempt this in this task.
>
> **Precision points nailed down before writing this brief:**
> 1. **Boundary condition:** use strict `date.today() > option_date` (not
>    `>=`), matching every existing deadline check in this file
>    (`_decide_binary`/`_decide_multi_outcome`'s existing
>    `date.today() > market.close_date` checks). An option's own date
>    stays open (UNCLEAR, not NO) through the end of that date itself,
>    only defaulting to NO the day after.
> 2. **Ordering:** the elapsed-date default only applies to an option
>    AFTER the existing evidence-based keyword matching (Task 19's
>    `ANNOUNCEMENT_KEYWORDS`/`ELIMINATION_KEYWORDS` proximity matching)
>    finds nothing for it — real evidence always wins over a date-elapsed
>    guess, same precedence as every other default-outcome fallback
>    already in this file.
> 3. **No new storage work needed.** If an elapsed-default NO fires now
>    and a later run finds real evidence contradicting it, Task 19's
>    per-`(market_id, option)` storage already supersedes it correctly —
>    verified against the existing `get_latest_findings` design, not
>    assumed.
> 4. **Strict date detection, not fuzzy.** An option must parse as a
>    *complete* date across its *entire* string (e.g. via
>    `datetime.strptime` against a small set of expected formats) to count
>    as a date-threshold option — a named option that merely contains a
>    date-like word (e.g. a candidate literally named "August Wilson")
>    must NOT be misdetected. A loose/partial date-extraction check is not
>    acceptable here.
> 5. **All-or-nothing per market.** If every option in `market.options`
>    parses as a date, treat the whole market as date-threshold-shaped and
>    sort options chronologically for the cascade logic. If even one
>    option does not parse as a date, fall back to the existing
>    named-entity logic for the whole market, unchanged — never mix the
>    two paths within one market.
> 6. **Title/description language must also confirm cumulative "by-date"
>    semantics**, not just option shape — options being dates alone is not
>    sufficient, since a market could instead be asking "on which exact
>    date will X happen" (a different, non-cumulative question). Check
>    `market.title`/`market.description` for cumulative trigger phrases
>    ("by", "no later than", "before", "on or before", "prior to"), the
>    same small-trigger-phrase-list style already used by
>    `DEFAULT_OUTCOME_PATTERN` elsewhere in this file. Only apply the new
>    logic when BOTH the date-shaped-options check AND the cumulative-
>    language check pass; otherwise, fall back to the existing
>    named-entity logic unchanged.
> 7. **Binary markets are structurally unaffected, already, by
>    construction** — `decide()`'s existing dispatch
>    (`if market.options: multi-outcome else: binary`) means this new
>    logic never runs for a binary market, even one whose title names a
>    date (nearly all of them do) — no extra guard needed, already true of
>    the existing code before this task touches anything.

**Files:**
- Modify: `resolution_finder/verdict_engine.py`
- Modify: `tests/test_verdict_engine.py`

**Interfaces:**
- Consumes: existing `Market`, `RankedArticle`, `Verdict` (unchanged), the existing `_decide_multi_outcome` internals from Task 19 (`_match_option_keyword`, `ANNOUNCEMENT_KEYWORDS`, `ELIMINATION_KEYWORDS`, `_split_sentences`, `_sentence_has_hedge`).
- Produces: `decide()`'s signature and return type are unchanged (`Verdict | list[Verdict]`) — this only adds a new internal branch inside the multi-outcome path for date-shaped option lists.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_verdict_engine.py`:

```python
DATE_THRESHOLD_DESCRIPTION = (
    "This market will resolve to \"Yes\" for the earliest listed date by "
    "which the event has occurred, and \"Yes\" for every later listed date "
    "as well. If the event has not occurred by a given date, that date's "
    "option resolves to \"No\" once that date has passed."
)

DATE_THRESHOLD_MARKET = Market(
    id="date-threshold-test",
    title="Will the event happen, by which date?",
    description=DATE_THRESHOLD_DESCRIPTION,
    options=["August 1, 2026", "September 1, 2026", "October 1, 2026"],
    close_date=date(2026, 10, 1),
)


def test_date_threshold_option_resolves_no_once_its_own_date_has_passed():
    # Freeze "today" at a point after the August option's date but before
    # the September/October ones, with no evidence found for any option.
    past_date_market = Market(
        id="date-threshold-test", title=DATE_THRESHOLD_MARKET.title,
        description=DATE_THRESHOLD_DESCRIPTION,
        options=["August 1, 2026", "September 1, 2026", "October 1, 2026"],
        close_date=date(2026, 10, 1),
    )
    with patch("resolution_finder.verdict_engine.date") as mock_date:
        mock_date.today.return_value = date(2026, 8, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        verdicts = decide(past_date_market, [])
    by_option = {v.option: v.outcome for v in verdicts}
    assert by_option["August 1, 2026"] == "NO"


def test_date_threshold_option_stays_unclear_before_its_own_date():
    with patch("resolution_finder.verdict_engine.date") as mock_date:
        mock_date.today.return_value = date(2026, 8, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        verdicts = decide(DATE_THRESHOLD_MARKET, [])
    by_option = {v.option: v.outcome for v in verdicts}
    assert by_option["September 1, 2026"] == "UNCLEAR"
    assert by_option["October 1, 2026"] == "UNCLEAR"


def test_date_threshold_option_stays_unclear_on_its_own_date_not_after():
    # Boundary check: the date's own day still counts as open.
    with patch("resolution_finder.verdict_engine.date") as mock_date:
        mock_date.today.return_value = date(2026, 8, 1)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        verdicts = decide(DATE_THRESHOLD_MARKET, [])
    by_option = {v.option: v.outcome for v in verdicts}
    assert by_option["August 1, 2026"] == "UNCLEAR"


def test_date_threshold_evidence_takes_priority_over_elapsed_default():
    evidence = [make_ranked(
        "The event was declared winner of the process on August 1, 2026.",
        url="https://www.bbc.com/x", source_type="credible_backup",
    )]
    # Reuse an ANNOUNCEMENT_KEYWORDS phrase ("winner of") near the option
    # text itself so the existing evidence-matching path (not the new
    # date-elapsed path) is what actually resolves this option.
    date_option_market = Market(
        id="date-threshold-test", title=DATE_THRESHOLD_MARKET.title,
        description=DATE_THRESHOLD_DESCRIPTION,
        options=["August 1, 2026"], close_date=date(2026, 10, 1),
    )
    with patch("resolution_finder.verdict_engine.date") as mock_date:
        mock_date.today.return_value = date(2026, 9, 1)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        verdicts = decide(date_option_market, evidence)
    assert verdicts[0].source_url == "https://www.bbc.com/x"


def test_named_entity_option_that_looks_like_a_date_word_is_not_misdetected():
    # A candidate literally named "August Wilson" must not be treated as a
    # date-threshold option just because it starts with a month name.
    named_market = Market(
        id="named-entity-test", title="Who will win the award?",
        description="This market resolves based on the award winner.",
        options=["August Wilson", "Toni Morrison"],
        close_date=date.today() + timedelta(days=365),
    )
    with patch("resolution_finder.verdict_engine.date") as mock_date:
        mock_date.today.return_value = date.today()
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        verdicts = decide(named_market, [])
    # Falls through to the existing named-entity path: no evidence, no
    # default -> single whole-market UNCLEAR/NO_EVIDENCE, NOT a per-option
    # NO from a misfired date parse.
    assert len(verdicts) == 1
    assert verdicts[0].option is None


def test_date_shaped_options_without_cumulative_language_are_not_cascaded():
    # Options are dates, but the description asks "on which date" (a single
    # exact-date question), not "by which date" (cumulative) -- must NOT
    # get the cascade treatment.
    exact_date_market = Market(
        id="exact-date-test", title="On which date will the event happen?",
        description="This market resolves to the single date on which the event occurs.",
        options=["August 1, 2026", "September 1, 2026"],
        close_date=date(2026, 10, 1),
    )
    with patch("resolution_finder.verdict_engine.date") as mock_date:
        mock_date.today.return_value = date(2026, 8, 15)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        verdicts = decide(exact_date_market, [])
    # Falls through to the existing named-entity path (no "by"/"no later
    # than"/etc. language), so no per-option elapsed-NO fires here either.
    assert len(verdicts) == 1
    assert verdicts[0].option is None
```

Add `from unittest.mock import patch` to the top of `tests/test_verdict_engine.py` if not already imported.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_verdict_engine.py -v`
Expected: FAIL — none of this date-detection/cascade logic exists yet, so every date-threshold-shaped market currently falls through to the existing named-entity path and returns a single whole-market UNCLEAR, not per-option NO/UNCLEAR verdicts.

- [ ] **Step 3: Implement date-threshold detection and elapsed-deadline handling**

In `resolution_finder/verdict_engine.py`, add near the other module-level patterns (after `ELIMINATION_KEYWORDS`):

```python
# Trigger phrases confirming a multi-outcome market's date-shaped options
# are CUMULATIVE thresholds ("by August 1" also satisfies "by September 1")
# rather than independent exact-date guesses. Same small-phrase-list style
# as DEFAULT_OUTCOME_PATTERN's trigger list above.
CUMULATIVE_DATE_TRIGGER_PHRASES = [
    "by ", "no later than", "before ", "on or before", "prior to",
]

# Formats real option strings are expected to use. Every option in a
# market must parse fully (the whole string, not a substring) against one
# of these for the market to be treated as date-threshold-shaped -- a
# named option that merely starts with a month name (e.g. "August
# Wilson") must fail every one of these and fall through safely.
_OPTION_DATE_FORMATS = ["%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y"]


def _parse_option_as_date(option: str) -> Optional[date]:
    stripped = option.strip()
    for fmt in _OPTION_DATE_FORMATS:
        try:
            return datetime.strptime(stripped, fmt).date()
        except ValueError:
            continue
    return None


def _is_cumulative_date_threshold_market(market: Market) -> bool:
    if not market.options:
        return False
    if any(_parse_option_as_date(opt) is None for opt in market.options):
        return False
    combined = f"{market.title} {market.description}".lower()
    return any(phrase in combined for phrase in CUMULATIVE_DATE_TRIGGER_PHRASES)


def _decide_date_thresholds(market: Market, ranked_evidence: list[RankedArticle]) -> list[Verdict]:
    """Cumulative date-threshold options only (see Task 21 design note).

    Only the elapsed-deadline half is implemented here: an option whose
    own date has passed, with no evidence resolving it via the existing
    keyword-matching path, resolves NO; one whose date hasn't arrived yet
    stays UNCLEAR. Evidence-confirmed cross-option YES cascading is
    deliberately NOT implemented here -- see the Task 21 design note for
    why, and Task 19's `_decide_multi_outcome` for the winner/elimination
    matching this still uses first, per option, before falling back to the
    date-elapsed default below.
    """
    # Reuse the exact same evidence-based matching Task 19 already has,
    # so real evidence always wins over a date-elapsed guess (Task 21
    # precision point 2). This mirrors _decide_multi_outcome's own
    # winner/elimination scan but keyed per date-option instead of
    # collecting a single market-wide winner.
    evidence_verdicts: dict[str, Verdict] = {}
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            lowered = sentence.lower()
            for option in market.options:
                if option in evidence_verdicts:
                    continue
                option_lower = option.strip().lower()
                outcome = None
                if _match_option_keyword(lowered, option_lower, ANNOUNCEMENT_KEYWORDS):
                    outcome = "YES"
                elif _match_option_keyword(lowered, option_lower, ELIMINATION_KEYWORDS):
                    outcome = "NO"
                if outcome:
                    evidence_verdicts[option] = Verdict(
                        outcome=outcome, option=option, confidence=item.similarity,
                        evidence_snippet=sentence.strip()[:280],
                        source_url=item.article.url, source_type=item.article.source_type,
                    )

    today = date.today()
    results: list[Verdict] = []
    for option in market.options:
        if option in evidence_verdicts:
            results.append(evidence_verdicts[option])
            continue
        option_date = _parse_option_as_date(option)
        if today > option_date:
            results.append(Verdict(
                outcome="NO", option=option, confidence=0.5,
                evidence_snippet=(
                    f"Deadline ({option}) passed with no matching evidence; "
                    "this date's threshold was not met."
                ),
                source_url=None, source_type=None,
            ))
    return results if results else [
        Verdict(outcome="UNCLEAR", option=None, confidence=0.0,
                evidence_snippet=None, source_url=None, source_type=None)
    ]
```

Note the last block only appends a result for options that got either an
evidence verdict or an elapsed-NO — an option that's simply not-yet-due
with no evidence is intentionally left out of `results` entirely (stays
implicitly UNCLEAR / unreported this run, consistent with Task 19's
existing "don't spam every pending option" partial-resolution pattern),
UNLESS every option in the market ends up in that same not-yet-due state,
in which case the function falls back to one whole-market UNCLEAR row so
the market isn't silently invisible on the dashboard.

Then wire this into the dispatcher — change `decide()`:

```python
def decide(market: Market, ranked_evidence: list[RankedArticle]) -> Verdict | list[Verdict]:
    if market.options:
        if _is_cumulative_date_threshold_market(market):
            return _decide_date_thresholds(market, ranked_evidence)
        return _decide_multi_outcome(market, ranked_evidence)
    return _decide_binary(market, ranked_evidence)
```

Add `from datetime import date, datetime` at the top if `datetime` isn't already imported (only `date` currently is).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_verdict_engine.py -v`
Expected: PASS — all Task 21 tests, plus the full existing Task 1-20 suite unaffected (binary markets and named-entity multi-outcome markets never reach this new code path at all).

Run: `pytest -v`
Expected: full suite passes, no regressions.

- [ ] **Step 5: Commit**

```bash
git add resolution_finder/verdict_engine.py tests/test_verdict_engine.py
git commit -m "feat: resolve cumulative date-threshold options independently

Multi-outcome markets whose options are dates (e.g. 'by August 1' /
'by September 1' / 'by October 1') are cumulative thresholds, not
independent named options -- a later date is automatically satisfied
by anything that resolves an earlier one. This task implements the
elapsed-deadline half only: a date-option whose own deadline has
passed, with no matching evidence, resolves No; one that hasn't come
due yet stays Unclear. Evidence-confirmed cross-option Yes cascading
(the event is confirmed to have happened, so figure out which
already-elapsed thresholds it actually preceded) is deliberately
deferred -- it needs to know roughly when an event happened relative
to already-passed thresholds, which this project's keyword-matching
evidence layer doesn't extract; the likely real fix is comparing
against the scanner's own run history over time instead of trying to
parse an exact date out of one article. Detection requires BOTH every
option parsing as a complete date AND the market's own title/
description using cumulative 'by-date' language, so a named option
that merely starts with a month name (or a market asking 'on which
date' rather than 'by which date') safely falls through to the
existing named-entity logic unchanged."
```

---

### Task 22: Numeric-threshold binary markets

> **Note (design context, confirmed via live testing):** A real, confirmed
> gap found in live accuracy testing after Tasks 19-21 landed: binary
> markets phrased as a numeric price/threshold question ("Will Bitcoin be
> above $64,000?", "Will Gold hit $4,400?", "Will the unemployment rate be
> over 4.1%?") can never resolve YES or NO via `BINARY_YES_KEYWORDS`,
> because that list is entirely legislative vocabulary ("signed into law",
> "enacted"). Reproduced live: a real Bitcoin price-threshold market found
> genuinely relevant real evidence ("Bitcoin held near $64,000 on
> Sunday...") and still landed on UNCLEAR, because no keyword in the list
> could ever fire for this market shape -- the retrieval layer works, the
> verdict engine simply has no vocabulary to use it.
>
> **The fix is not a keyword-list addition** -- it structurally can't be,
> since every market names a different number. This needs a genuinely new
> resolution pattern, the same shape as Task 21's date-threshold work:
> detect the market's own numeric condition from its title/description,
> extract the actual value from evidence text, and compare them.
>
> **Design decisions:**
> 1. **Scope: single-threshold comparisons only** ("above X", "below X",
>    "at least X", "reach X", "hit X"). "Between X and Y" range markets are
>    explicitly OUT of scope for this task (real added complexity --
>    two numbers, two magnitude suffixes -- and none of the real markets
>    that motivated this task needed it; Bitcoin/Gold/Ethereum/SPCX were
>    all single-threshold). Document as deferred, don't attempt.
> 2. **Direction, not strict operator semantics.** "above/over/more
>    than/at least/reach/hit" all map to direction `"up"` (evidence value
>    must be `>=` the threshold); "below/under/less than" map to `"down"`
>    (evidence value must be `<=` the threshold). This collapses the
>    above/at-least strict-vs-inclusive distinction real market language
>    rarely lets evidence text answer precisely anyway -- documented
>    simplification, not an oversight.
> 3. **Number extraction is self-contained in `verdict_engine.py`, not
>    imported from `peer_market.py`.** `peer_market.py` already has
>    near-identical `MONEY_PATTERN`/`PERCENT_PATTERN` regexes, but for a
>    different job (rejecting mismatched peer markets). Duplicating a
>    small regex here keeps `verdict_engine.py` self-contained and
>    independently swappable, per this project's "Verdict Engine must be
>    swappable" global constraint -- an alternate engine implementation
>    shouldn't need to also carry `peer_market.py`'s internals.
> 4. **"Most recently stated number in a sentence" heuristic for
>    extracting the current value from evidence.** A sentence like "up
>    from $61,000 to $64,000" states the current value last -- take the
>    LAST number match in a qualifying sentence, not the first. Documented
>    known limitation (not perfect for every possible phrasing), matching
>    this file's existing pattern of documenting imperfect-but-tested
>    heuristics (e.g. `ENTITY_PATTERN`'s known gaps).
> 5. **Reuses existing safety checks unchanged**: `_sentence_has_hedge`
>    (a hedged/hypothetical sentence's number doesn't count) and
>    `_sentence_mentions_other_entity` (a number about a DIFFERENT named
>    asset mentioned in the same article -- e.g. "Ethereum hit $64,000"
>    in an article about Bitcoin -- must not be mistaken for evidence
>    about this market). Both already exist in this file; this task reuses
>    them exactly as `_decide_binary` does, doesn't reimplement them.
> 6. **Produces both YES and NO from evidence**, unlike the existing
>    keyword paths (which only ever produce YES from evidence; NO only
>    comes from the deadline-passed default). This is a deliberate,
>    reasoned difference: once a real number is found and compared, "the
>    evidence contradicts the threshold" is just as real and useful a
>    signal as "the evidence confirms it" -- there's no reason to discard
>    a clear NO signal just because the existing keyword system's
>    structure never needed to produce one.
> 7. **Detection runs only for binary markets** (`market.options` empty),
>    checked in `decide()` before falling to `_decide_binary`, mirroring
>    exactly how `_is_cumulative_date_threshold_market` is checked before
>    `_decide_multi_outcome`. A market with no numeric-threshold phrasing
>    in its title/description falls through to the existing
>    `_decide_binary` unchanged -- zero behavior change for CLARITY Act,
>    Nobel Prize, or any other existing binary market shape.

**Files:**
- Modify: `resolution_finder/verdict_engine.py`
- Modify: `tests/test_verdict_engine.py`

**Interfaces:**
- Consumes: existing `Market`, `RankedArticle`, `Verdict`, existing `_subject_terms`, `_sentence_mentions_other_entity`, `_sentence_has_hedge`, `_split_sentences` (all unchanged, reused as-is).
- Produces: `decide()`'s signature and return type are unchanged (`Verdict | list[Verdict]`) -- this only adds a new internal branch inside the binary-market path.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_verdict_engine.py`:

```python
BITCOIN_DESCRIPTION = (
    "This market will resolve to \"Yes\" if the price of Bitcoin (BTC) is "
    "above $64,000 on August 17, 2026, according to the Binance BTC/USDT "
    "reference price. Otherwise, this market will resolve to \"No\"."
)

BITCOIN_MARKET = Market(
    id="bitcoin-above-64k-on-august-17-2026",
    title="Will the price of Bitcoin be above $64,000 on August 17?",
    description=BITCOIN_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)

ETHEREUM_DESCRIPTION = (
    "This market will resolve to \"Yes\" if the price of Ethereum (ETH) is "
    "less than $1,400 on August 17, 2026. Otherwise, this market will "
    "resolve to \"No\"."
)

ETHEREUM_MARKET = Market(
    id="ethereum-below-1400-on-august-17-2026",
    title="Will the price of Ethereum be less than $1,400 on August 17?",
    description=ETHEREUM_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)

GOLD_DESCRIPTION = (
    "This market will resolve to \"Yes\" if Gold (XAUUSD) reaches a high of "
    "at least $4,400 in August 2026. Otherwise, this market will resolve to \"No\"."
)

GOLD_MARKET = Market(
    id="gold-reach-4400-in-august-2026",
    title="Will Gold (XAUUSD) hit $4,400 in August?",
    description=GOLD_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)


def test_numeric_threshold_market_resolves_yes_when_evidence_confirms_above_threshold():
    evidence = [make_ranked(
        "Bitcoin surged to $67,200 on Monday amid renewed institutional buying.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_numeric_threshold_market_resolves_no_when_evidence_contradicts_threshold():
    evidence = [make_ranked(
        "Bitcoin fell sharply to $58,400 on Monday as traders took profits.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome == "NO"


def test_numeric_threshold_market_handles_below_direction():
    evidence = [make_ranked(
        "Ethereum dropped to $1,150 on Monday, extending its weekly decline.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(ETHEREUM_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_numeric_threshold_market_handles_reach_at_least_phrasing():
    evidence = [make_ranked(
        "Gold prices hit $4,512 an ounce on Friday, a fresh all-time high.",
        url="https://www.cnbc.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(GOLD_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_numeric_threshold_market_stays_unclear_without_a_number_in_evidence():
    evidence = [make_ranked(
        "Bitcoin traders are watching the Fed decision closely this week.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


def test_numeric_threshold_market_no_evidence_at_all():
    verdict = decide(BITCOIN_MARKET, [])
    assert verdict.outcome == "NO_EVIDENCE"


def test_numeric_threshold_market_ignores_number_about_a_different_asset():
    # Real risk this guards against: an article about Bitcoin also
    # mentions Ethereum's price -- must not be mistaken for Bitcoin's.
    evidence = [make_ranked(
        "While Bitcoin held steady, Ethereum climbed to $67,000 in a rare "
        "moment of ETH outperformance.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome != "YES"


def test_numeric_threshold_market_ignores_hedged_number():
    evidence = [make_ranked(
        "Analysts say Bitcoin could reach $70,000 if the rally continues.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome != "YES"


def test_numeric_threshold_extracts_the_most_recently_stated_number():
    # "up from X to Y" states the current value last -- must use $64,500,
    # not the earlier $61,000 mentioned in the same sentence.
    evidence = [make_ranked(
        "Bitcoin climbed from $61,000 to $64,500 over the trading session.",
        url="https://www.coindesk.com/x", source_type="credible_backup_secondary",
    )]
    verdict = decide(BITCOIN_MARKET, evidence)
    assert verdict.outcome == "YES"


def test_non_numeric_binary_market_unaffected():
    # Regression guard: CLARITY Act (legislative binary, no $ threshold in
    # its own text) must be completely unaffected -- routed to the
    # existing _decide_binary path, not misdetected as threshold-shaped.
    evidence = [make_ranked("The bill was signed into law by the President today.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "YES"
    assert "signed into law" in verdict.evidence_snippet
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_verdict_engine.py -v`
Expected: FAIL -- none of this numeric-threshold detection/comparison logic exists yet, so every threshold-shaped market currently falls through to `_decide_binary` and returns UNCLEAR regardless of what the evidence actually says.

- [ ] **Step 3: Implement numeric-threshold detection and comparison**

In `resolution_finder/verdict_engine.py`, add near the other module-level patterns (after `_OPTION_DATE_FORMATS`/`_parse_option_as_date`):

```python
# Numeric-threshold binary markets ("Will Bitcoin be above $64,000?"),
# added after live testing found BINARY_YES_KEYWORDS structurally cannot
# resolve these -- it's entirely legislative vocabulary, and no keyword
# list can cover every possible threshold number. This extracts and
# compares real numbers instead. Self-contained here (not imported from
# peer_market.py's near-identical MONEY_PATTERN/PERCENT_PATTERN) so this
# file stays independently swappable per the project's "Verdict Engine
# must be swappable" constraint.
_THRESHOLD_NUMBER = r"\$?\s?(\d[\d,]*(?:\.\d+)?)\s?(bn|mm|thousand|million|billion|[kmb])?%?"
_THRESHOLD_MAGNITUDES = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mm": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
}

# Only single-threshold direction, not "between X and Y" ranges -- see
# Task 22 design note point 1. Order matters: tried in this sequence, so
# a title using "at least" isn't accidentally caught by a looser pattern.
_THRESHOLD_CONDITION_PATTERNS = [
    ("up", re.compile(
        rf"(?:at least|reach(?:es)?|hit|above|over|more than)\s+\(?(?:HIGH\)?\s*)?{_THRESHOLD_NUMBER}",
        re.IGNORECASE,
    )),
    ("down", re.compile(rf"(?:below|under|less than)\s+{_THRESHOLD_NUMBER}", re.IGNORECASE)),
]


def _parse_threshold_number(raw: str, suffix: Optional[str]) -> float:
    value = float(raw.replace(",", ""))
    if suffix:
        value *= _THRESHOLD_MAGNITUDES.get(suffix.lower(), 1)
    return value


def _extract_threshold_condition(market: Market) -> Optional[tuple[str, float]]:
    """The market's own numeric threshold condition, parsed from its title/
    description -- e.g. ("up", 64000.0) for "above $64,000". Returns None
    for a market that isn't phrased as a numeric-threshold question at
    all, which is the common case and must fall through to the existing
    _decide_binary unchanged.
    """
    combined = f"{market.title} {market.description}"
    for direction, pattern in _THRESHOLD_CONDITION_PATTERNS:
        match = pattern.search(combined)
        if match:
            return direction, _parse_threshold_number(match.group(1), match.group(2))
    return None


def _extract_latest_number(sentence: str) -> Optional[float]:
    """The most recently stated number in `sentence` -- real evidence often
    states a prior value before the current one ("up from $61,000 to
    $64,000"), and the current value is conventionally stated last. Known,
    documented heuristic, not perfect for every phrasing (see Task 22
    design note point 4)."""
    matches = list(re.finditer(_THRESHOLD_NUMBER, sentence))
    if not matches:
        return None
    raw, suffix = matches[-1].group(1), matches[-1].group(2)
    return _parse_threshold_number(raw, suffix)


def _decide_numeric_threshold(
    market: Market, ranked_evidence: list[RankedArticle], direction: str, threshold: float
) -> Verdict:
    subject_terms = _subject_terms(market)
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            if _sentence_mentions_other_entity(sentence, subject_terms):
                continue
            value = _extract_latest_number(sentence)
            if value is None:
                continue
            met = value >= threshold if direction == "up" else value <= threshold
            return Verdict(
                outcome="YES" if met else "NO",
                confidence=item.similarity,
                evidence_snippet=sentence.strip()[:280],
                source_url=item.article.url,
                source_type=item.article.source_type,
            )

    if ranked_evidence:
        top = ranked_evidence[0]
        return Verdict(outcome="UNCLEAR", confidence=top.similarity,
                        evidence_snippet=top.text[:280], source_url=top.article.url,
                        source_type=top.article.source_type)

    return Verdict(outcome="NO_EVIDENCE", confidence=0.0, evidence_snippet=None,
                    source_url=None, source_type=None)
```

Then wire this into `decide()`:

```python
def decide(market: Market, ranked_evidence: list[RankedArticle]) -> Verdict | list[Verdict]:
    if market.options:
        if _is_cumulative_date_threshold_market(market):
            return _decide_date_thresholds(market, ranked_evidence)
        return _decide_multi_outcome(market, ranked_evidence)
    threshold_condition = _extract_threshold_condition(market)
    if threshold_condition is not None:
        direction, value = threshold_condition
        return _decide_numeric_threshold(market, ranked_evidence, direction, value)
    return _decide_binary(market, ranked_evidence)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_verdict_engine.py -v`
Expected: PASS -- all Task 22 tests, plus the full existing Task 1-21 suite unaffected (non-threshold binary markets and all multi-outcome markets never reach this new code path at all).

Run: `pytest -v`
Expected: full suite passes, no regressions.

- [ ] **Step 5: Empirically re-verify against the real evidence that motivated this task**

Run this to confirm the fix actually resolves the specific production gap found live (not just the synthetic test fixtures):

```python
python -c "
from datetime import date, timedelta
from resolution_finder.models import Market, ArticleRef, RankedArticle
from resolution_finder.verdict_engine import decide

market = Market(
    id='bitcoin-above-64k-on-august-17-2026',
    title='Will the price of Bitcoin be above \$64,000 on August 17?',
    description='This market will resolve to \"Yes\" if the price of Bitcoin (BTC) is above \$64,000 on August 17, 2026. Otherwise, this market will resolve to \"No\".',
    options=[], close_date=date.today() + timedelta(days=30),
)
ranked = [RankedArticle(
    article=ArticleRef(url='https://www.investing.com/x', title='t', source_type='credible_backup_secondary'),
    text='Investing.com -- Bitcoin held near \$64,000 on Sunday as traders positioned for Wednesday\'s Federal Reserve interest-rate decision, with a large block trade pushing the price briefly to \$64,850 before easing back.',
    similarity=0.47,
)]
verdict = decide(market, ranked)
print('outcome:', verdict.outcome)
print('snippet:', verdict.evidence_snippet)
"
```

Expected: `outcome` resolves to `YES` or `NO` based on the actual extracted number (not `UNCLEAR`) -- confirms the fix works against the real article text that caused the original gap, not just hand-written test text. Report the actual output honestly either way.

- [ ] **Step 6: Commit**

```bash
git add resolution_finder/verdict_engine.py tests/test_verdict_engine.py
git commit -m "feat: resolve numeric-threshold binary markets

Binary markets phrased as a price/numeric threshold ('Will Bitcoin be
above \$64,000?') could never resolve via BINARY_YES_KEYWORDS, which
is entirely legislative vocabulary -- confirmed live: a real Bitcoin
market found genuinely relevant evidence and still landed on UNCLEAR
because no keyword could fire. This extracts the market's own
threshold condition (direction + number) from its title/description,
and compares it against the most recently stated number in each
evidence sentence, reusing the existing hedge and wrong-subject-veto
safety checks. Single-threshold comparisons only ('above X', 'below
X', 'at least X') -- 'between X and Y' ranges are deliberately out of
scope, not needed by any real market that motivated this task."
```

---

### Task 23: Semantic verdict classification (embedding-based fallback)

> **Note (design context):** Even with Task 22's numeric-threshold fix,
> live accuracy testing kept finding the same underlying pattern in NEW
> domains: `BINARY_YES_KEYWORDS`/`ANNOUNCEMENT_KEYWORDS` are hand-curated
> phrase lists, built reactively from whatever specific wording happened
> to appear in past real examples. Confirmed live in two more domains
> that were never keyword-covered: an FDA approval market found real,
> on-topic evidence ("FDA approves Sanofi's Subcutaneous Sarclisa...")
> and still landed on UNCLEAR at 0.75 confidence (no "approves"-style
> phrase in `ANNOUNCEMENT_KEYWORDS`, which was written for prize/contract
> language); a sports-record market found real evidence about the actual
> record and still landed on UNCLEAR at 0.68 confidence (no
> "record broken" phrase anywhere). Every new domain needs its own
> keyword discovered by hand before anything in it can ever resolve, even
> when retrieval already finds exactly the right evidence.
>
> **The idea:** this project already runs a free, local embedding model
> (`sentence-transformers`, via `relevance_ranker._get_model()`) for
> relevance ranking. The same model can classify whether a sentence
> *semantically* confirms an event happened, without needing a literal
> phrase match -- by comparing the sentence's embedding to two small
> per-market template sentences ("this has been confirmed" vs "this has
> not happened / remains uncertain"), both built from the market's own
> title so the comparison captures subject AND confirmation-tone
> together, not confirmation-tone alone (a bare, market-agnostic template
> like "this happened" would score high similarity against almost any
> factual past-tense sentence regardless of subject -- see design
> decision 3 below for why this is guarded against).
>
> This stays inside the project's "no LLM, zero marginal cost" global
> constraint: it's the same free model already running for every market,
> for a new purpose, not a new dependency or a paid API call.
>
> **Design decisions:**
> 1. **Fallback only, never competes with keyword matching.** The
>    semantic check only runs AFTER the full keyword-matching pass
>    (`BINARY_YES_KEYWORDS`) completes and finds nothing. Keyword matching
>    remains the primary, more precise mechanism; this only adds
>    resolving power for cases keyword matching misses entirely. Zero
>    regression risk to any market that already resolves correctly via
>    keywords today.
> 2. **Binary markets only, via `_decide_binary`.** Multi-outcome and
>    date-threshold markets are explicitly OUT of scope for this task --
>    each has its own option-proximity matching logic
>    (`_match_option_keyword`) that a bare confirmation-vs-uncertain
>    template doesn't map onto cleanly (which OPTION does a generic
>    "this has been confirmed" template even refer to?). A follow-up task
>    if wanted later, not attempted here.
> 3. **Templates are built per-market, embedding the market's own
>    title**, not generic/market-agnostic. `f"{market.title} This has
>    been confirmed and has already happened."` vs `f"{market.title} This
>    has not happened yet and remains unconfirmed or uncertain."` A bare
>    "this has happened" template compared against ANY factual sentence
>    would score deceptively high regardless of subject -- embedding the
>    market's own title into both templates is what lets the similarity
>    score actually reflect "is this evidence about THIS market's
>    subject, confirmed" rather than just "is this sentence
>    confirmation-shaped in general."
> 4. **Still gated by the existing hedge and wrong-subject-veto checks**
>    (`_sentence_has_hedge`, `_sentence_mentions_other_entity`) BEFORE
>    semantic scoring runs -- same as every other check in this file. A
>    hedged or wrong-subject sentence never reaches the embedding
>    comparison at all.
> 5. **Only produces YES, not NO** (matches the existing keyword paths'
>    asymmetry -- `BINARY_YES_KEYWORDS` also only ever produces YES; NO
>    still only comes from the deadline-passed default). Adding a
>    semantic NO-detection path is deliberately deferred: distinguishing
>    "confirmed this did NOT happen" from "just uncertain/unconfirmed" as
>    a THIRD semantic category is a meaningfully harder classification
>    problem than the two-way split this task scopes to, and getting it
>    wrong would produce a false NO -- a worse failure than staying
>    UNCLEAR. Not attempted here.
> 6. **Threshold and margin values are NOT specified in this brief --
>    they must be empirically calibrated during implementation**, per
>    this project's established practice (Task 14/18's live-verification
>    steps) of never trusting an unverified number. Step 3 below is
>    structured so the implementer runs the real embedding model against
>    a fixed set of real evidence sentences (the two documented gaps
>    above, plus real evidence sentences from markets already correctly
>    resolved via keywords, used as negative/no-regression controls) and
>    picks the threshold/margin that correctly classifies all of them
>    before writing the final numbers into the constants.

**Files:**
- Modify: `resolution_finder/verdict_engine.py`
- Modify: `tests/test_verdict_engine.py`

**Interfaces:**
- Consumes: `_get_model` (`resolution_finder/relevance_ranker.py`, already used the same way by `resolution_finder/peer_market.py:12`), `util.cos_sim` (`sentence_transformers`, already a project dependency), existing `_subject_terms`, `_sentence_has_hedge`, `_sentence_mentions_other_entity`, `_split_sentences`.
- Produces: `decide()`'s signature and return type are unchanged -- this only adds a new internal fallback step inside `_decide_binary`.

- [ ] **Step 1: Write the failing tests (mocked model -- deterministic)**

Add to `tests/test_verdict_engine.py`. These test the THRESHOLD/MARGIN comparison logic and its integration with the existing safety checks deterministically, by mocking the embedding model -- the same pattern `tests/test_relevance_ranker.py` already uses for `_get_model`/`util.cos_sim`:

```python
from unittest.mock import MagicMock

SANOFI_DESCRIPTION = (
    "This market will resolve to \"Yes\" if the FDA approves Sanofi's "
    "Subcutaneous Sarclisa by the specified date. Otherwise, this market "
    "will resolve to \"No\"."
)

SANOFI_MARKET = Market(
    id="fda-approves-sanofi-subcutaneous-sarclisa",
    title="FDA approves Sanofi's Subcutaneous Sarclisa?",
    description=SANOFI_DESCRIPTION,
    options=[],
    close_date=date.today() + timedelta(days=30),
)


@patch("resolution_finder.verdict_engine.util.cos_sim")
@patch("resolution_finder.verdict_engine._get_model")
def test_semantic_fallback_resolves_yes_above_threshold_and_margin(mock_get_model, mock_cos_sim):
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    # sentence-vs-positive-template, then sentence-vs-negative-template
    mock_cos_sim.side_effect = [[[0.75]], [[0.30]]]

    evidence = [make_ranked(
        "Regulators granted approval for the subcutaneous formulation this week.",
        url="https://example.com/x", source_type="credible_backup",
    )]
    verdict = decide(SANOFI_MARKET, evidence)
    assert verdict.outcome == "YES"


@patch("resolution_finder.verdict_engine.util.cos_sim")
@patch("resolution_finder.verdict_engine._get_model")
def test_semantic_fallback_stays_unclear_below_threshold(mock_get_model, mock_cos_sim):
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.side_effect = [[[0.40]], [[0.35]]]  # below whatever threshold gets calibrated

    evidence = [make_ranked(
        "The FDA is expected to review the application sometime next quarter.",
        url="https://example.com/x", source_type="credible_backup",
    )]
    verdict = decide(SANOFI_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


@patch("resolution_finder.verdict_engine.util.cos_sim")
@patch("resolution_finder.verdict_engine._get_model")
def test_semantic_fallback_stays_unclear_when_margin_too_thin(mock_get_model, mock_cos_sim):
    # High absolute similarity to BOTH templates (an ambiguous sentence)
    # must not resolve YES just because it clears the absolute bar --
    # the margin between positive and negative similarity matters too.
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.side_effect = [[[0.80]], [[0.78]]]

    evidence = [make_ranked(
        "The situation around the approval remains fluid.",
        url="https://example.com/x", source_type="credible_backup",
    )]
    verdict = decide(SANOFI_MARKET, evidence)
    assert verdict.outcome == "UNCLEAR"


def test_semantic_fallback_never_runs_when_keyword_match_already_found():
    # CLARITY Act already resolves via BINARY_YES_KEYWORDS -- the semantic
    # path must not even be reached (proven by not mocking the model at
    # all here; if the code tried to call the real model unexpectedly in
    # a test environment without the mock, this test's own setup gives no
    # semantic signal, so a false regression would surface as this test
    # timing out or erroring on a real model load, not silently passing).
    evidence = [make_ranked("The bill was signed into law by the President today.")]
    verdict = decide(CLARITY_MARKET, evidence)
    assert verdict.outcome == "YES"
    assert "signed into law" in verdict.evidence_snippet


@patch("resolution_finder.verdict_engine.util.cos_sim")
@patch("resolution_finder.verdict_engine._get_model")
def test_semantic_fallback_still_respects_hedge_guard(mock_get_model, mock_cos_sim):
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.side_effect = [[[0.95]], [[0.10]]]  # would clearly pass if reached

    evidence = [make_ranked(
        "The FDA could approve the drug if trial data holds up, analysts say.",
        url="https://example.com/x", source_type="credible_backup",
    )]
    verdict = decide(SANOFI_MARKET, evidence)
    assert verdict.outcome != "YES"  # hedge ("could") must reject before semantic scoring runs


@patch("resolution_finder.verdict_engine.util.cos_sim")
@patch("resolution_finder.verdict_engine._get_model")
def test_semantic_fallback_still_respects_wrong_subject_veto(mock_get_model, mock_cos_sim):
    mock_model = MagicMock()
    mock_model.encode.return_value = "embedding"
    mock_get_model.return_value = mock_model
    mock_cos_sim.side_effect = [[[0.95]], [[0.10]]]  # would clearly pass if reached

    evidence = [make_ranked(
        "Regulators at the European Medicines Agency approved a different drug, Xarelto, this week.",
        url="https://example.com/x", source_type="credible_backup",
    )]
    verdict = decide(SANOFI_MARKET, evidence)
    assert verdict.outcome != "YES"  # wrong-subject veto must reject before semantic scoring runs
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_verdict_engine.py -v`
Expected: FAIL -- no semantic fallback exists yet, so every threshold/margin test fails, and the guard tests currently pass for the wrong reason (nothing semantic runs at all yet) rather than the right one (hedge/veto correctly blocking it).

- [ ] **Step 3: Implement the semantic fallback, calibrating the threshold empirically**

First, calibrate empirically -- run this against the REAL evidence sentences that motivated this task, using the REAL model (not mocked), to find threshold/margin values that correctly classify all of them:

```python
python -c "
from resolution_finder.relevance_ranker import _get_model
from sentence_transformers import util

model = _get_model()

cases = [
    # (market_title, sentence, expected_confirms)
    ('FDA approves Sanofi\'s Subcutaneous Sarclisa?',
     'The FDA has approved Sanofi\'s subcutaneous formulation of Sarclisa for multiple myeloma patients.',
     True),
    ('World Cup: Most Player Goals Record Broken?',
     'The tournament record for most goals by a single player was broken on Tuesday.',
     True),
    ('FDA approves Sanofi\'s Subcutaneous Sarclisa?',
     'The FDA is expected to make a decision on the application sometime next quarter.',
     False),
    ('World Cup: Most Player Goals Record Broken?',
     'Fans are debating whether the tournament record could be broken this year.',
     False),
]
for title, sentence, expected in cases:
    positive = f'{title} This has been confirmed and has already happened.'
    negative = f'{title} This has not happened yet and remains unconfirmed or uncertain.'
    s_emb = model.encode(sentence, convert_to_tensor=True)
    p_emb = model.encode(positive, convert_to_tensor=True)
    n_emb = model.encode(negative, convert_to_tensor=True)
    p_sim = float(util.cos_sim(s_emb, p_emb)[0][0])
    n_sim = float(util.cos_sim(s_emb, n_emb)[0][0])
    print(f'expected={expected} positive_sim={p_sim:.3f} negative_sim={n_sim:.3f} margin={p_sim-n_sim:.3f}')
"
```

Inspect the printed `positive_sim`/`margin` values for the two `expected=True` cases vs the two `expected=False` cases. Pick `SEMANTIC_CONFIRMATION_THRESHOLD` and `SEMANTIC_MARGIN` constants that separate them -- a threshold at or just below the lowest `positive_sim` among the `True` cases, and a margin at or just below the lowest margin among the `True` cases, both still clearing every `False` case. Report the actual printed numbers and the chosen constants in your report -- do not guess without running this.

Then, in `resolution_finder/verdict_engine.py`, add the imports and new code:

```python
from sentence_transformers import util
from resolution_finder.relevance_ranker import _get_model
```

```python
# Calibrated empirically against real evidence sentences (Task 23 Step 3)
# -- do not change these without re-running that calibration.
SEMANTIC_CONFIRMATION_THRESHOLD = <VALUE FROM YOUR CALIBRATION RUN>
SEMANTIC_MARGIN = <VALUE FROM YOUR CALIBRATION RUN>


def _semantic_yes_signal(sentence: str, market: Market) -> Optional[float]:
    """A confidence score if `sentence` semantically confirms `market`'s
    subject has been resolved true, or None otherwise. Fallback net for
    when BINARY_YES_KEYWORDS finds nothing -- reuses the same free local
    embedding model already running for relevance ranking, not a new
    dependency. The market's own title is embedded into BOTH templates so
    the comparison reflects subject-plus-confirmation together, not
    confirmation-tone alone (see Task 23 design note point 3)."""
    model = _get_model()
    positive_template = f"{market.title} This has been confirmed and has already happened."
    negative_template = f"{market.title} This has not happened yet and remains unconfirmed or uncertain."
    sentence_emb = model.encode(sentence, convert_to_tensor=True)
    positive_emb = model.encode(positive_template, convert_to_tensor=True)
    negative_emb = model.encode(negative_template, convert_to_tensor=True)
    positive_sim = float(util.cos_sim(sentence_emb, positive_emb)[0][0])
    negative_sim = float(util.cos_sim(sentence_emb, negative_emb)[0][0])
    if positive_sim >= SEMANTIC_CONFIRMATION_THRESHOLD and (positive_sim - negative_sim) >= SEMANTIC_MARGIN:
        logger.info(
            "Semantic fallback fired for market %r: sentence=%r positive_sim=%.3f margin=%.3f",
            market.id, sentence, positive_sim, positive_sim - negative_sim,
        )
        return positive_sim
    return None
```

Deliberate design choice, not an oversight: this only logs, it does NOT auto-promote the matched sentence into `BINARY_YES_KEYWORDS`. A semantic match is a similarity score for a whole sentence, not a clean reusable phrase, and every existing keyword got there through a human judging whether it generalizes safely -- automating that step risks a single noisy match becoming a permanent, unconditional literal rule with none of the threshold/margin nuance that made the semantic match safe. The log exists so a human can periodically review real fallback hits and manually decide what's worth promoting, not so the system rewrites its own rules unsupervised. Add `import logging` and `logger = logging.getLogger(__name__)` near the top of the file if not already present (check first -- `evidence_retriever.py` and `pipeline.py` already use this exact pattern, `verdict_engine.py` may not yet).

Then modify `_decide_binary` to add the fallback pass, inserted after the existing keyword loop and before the `default_outcome` check:

```python
def _decide_binary(market: Market, ranked_evidence: list[RankedArticle]) -> Verdict:
    subject_terms = _subject_terms(market)
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            if _sentence_mentions_other_entity(sentence, subject_terms):
                continue
            if any(_contains_keyword(sentence.lower(), keyword) for keyword in BINARY_YES_KEYWORDS):
                return Verdict(
                    outcome="YES",
                    confidence=item.similarity,
                    evidence_snippet=sentence.strip()[:280],
                    source_url=item.article.url,
                    source_type=item.article.source_type,
                )

    # Semantic fallback: only reached when keyword matching found nothing
    # at all above. Keyword matching stays primary/more precise.
    for item in ranked_evidence:
        for sentence in _split_sentences(item.text):
            if _sentence_has_hedge(sentence):
                continue
            if _sentence_mentions_other_entity(sentence, subject_terms):
                continue
            semantic_score = _semantic_yes_signal(sentence, market)
            if semantic_score is not None:
                return Verdict(
                    outcome="YES",
                    confidence=min(item.similarity, semantic_score),
                    evidence_snippet=sentence.strip()[:280],
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
```

Update the mocked tests from Step 1 if their hardcoded `mock_cos_sim.side_effect` values don't straddle your actual calibrated threshold/margin correctly -- adjust the mocked values, not the test assertions, so the tests still exercise "above bar" vs "below bar" vs "high-but-thin-margin" against your real chosen constants.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_verdict_engine.py -v`
Expected: PASS -- all Task 23 tests, plus the full existing Task 1-22 suite unaffected (any market that already resolves via keyword matching never reaches the semantic fallback at all, since it's only tried after the keyword loop exhausts with no match).

Run: `pytest -v`
Expected: full suite passes, no regressions.

- [ ] **Step 5: Empirically re-verify against the two real gaps that motivated this task**

```python
python -c "
from datetime import date, timedelta
from resolution_finder.models import Market, ArticleRef, RankedArticle
from resolution_finder.verdict_engine import decide

sanofi = Market(
    id='fda-approves-sanofi-subcutaneous-sarclisa', title=\"FDA approves Sanofi's Subcutaneous Sarclisa?\",
    description='This market will resolve to \"Yes\" if the FDA approves Sanofi\'s Subcutaneous Sarclisa. Otherwise, this market will resolve to \"No\".',
    options=[], close_date=date.today() + timedelta(days=30),
)
ranked = [RankedArticle(
    article=ArticleRef(url='https://example.com/x', title='t', source_type='credible_backup_secondary'),
    text='The FDA has approved Sanofi\'s subcutaneous formulation of Sarclisa for multiple myeloma patients, the company announced Thursday.',
    similarity=0.75,
)]
print('sanofi:', decide(sanofi, ranked).outcome)

worldcup = Market(
    id='world-cup-most-goals-record-broken', title='World Cup: Most Player Goals Record Broken?',
    description='This market will resolve to \"Yes\" if the record for most goals scored by a single player is broken during the tournament.',
    options=[], close_date=date.today() + timedelta(days=30),
)
ranked2 = [RankedArticle(
    article=ArticleRef(url='https://example.com/y', title='t', source_type='credible_backup_secondary'),
    text='The all-time tournament scoring record was broken on Tuesday when the striker netted his 16th goal of the competition.',
    similarity=0.68,
)]
print('world cup:', decide(worldcup, ranked2).outcome)
"
```

Expected: both resolve to `YES`, not `UNCLEAR` -- confirms the fix closes the two specific real gaps found in live testing. Report the actual output honestly either way; if either stays `UNCLEAR`, the calibrated threshold/margin need revisiting before this task is done, not shipped as-is.

- [ ] **Step 6: Commit**

```bash
git add resolution_finder/verdict_engine.py tests/test_verdict_engine.py
git commit -m "feat: semantic verdict classification as a keyword-matching fallback

BINARY_YES_KEYWORDS is a hand-curated phrase list that structurally
can't generalize -- live testing kept finding new domains (FDA
approvals, sports records) where real, on-topic evidence was found
and still couldn't resolve because no keyword matched. This adds a
fallback (only tried when keyword matching finds nothing) that reuses
the free local embedding model already running for relevance ranking
to judge whether a sentence semantically confirms the market's own
subject, by comparing it to two per-market template sentences built
from the market's own title. Threshold/margin were calibrated
empirically against the real evidence sentences that motivated this
task, not guessed. Binary markets only, YES-only (no semantic NO
detection), gated by the same hedge and wrong-subject-veto checks
every other path in this file already uses."
```

---

## Backlog / Deferred Future Work (not yet scoped as SDD tasks)

These are real, evidence-backed gaps found during live testing (2026-08-22/23,
after Task 23) that are deliberately NOT yet turned into numbered implementation
tasks -- either the design isn't settled enough to be actionable yet, or the
user has explicitly deprioritized the vertical for now. Captured here so they
aren't re-investigated from scratch later.

### Numeric-threshold market shape considerations (research done, mostly not a gap)

Prompted by a user question about whether the numeric-threshold path (Task 22)
correctly handles non-dollar markets and threshold+date combinations.

- **Non-dollar numeric-threshold markets** (sales counts, vote tallies, "reach
  1M users"): CONFIRMED already supported by the existing architecture. Both
  `_extract_threshold_condition` (the market's own threshold) and
  `_extract_latest_number` (evidence-side) already treat `$` as optional --
  verified live via a new `SALES_MARKET` test fixture in
  `tests/test_verdict_engine.py` (`test_numeric_threshold_still_extracts_bare_non_dollar_count`).
  Not a gap.
- **Markets combining a numeric threshold AND a date/deadline** in the same
  title/description (e.g. "reach 1M sales by 2026"): CONFIRMED no clash in the
  common phrasing `[trigger word] [NUMBER] ... by [DATE]`, since
  `_THRESHOLD_CONDITION_PATTERNS` requires the trigger word to sit immediately
  before the number, and `.search()` returns the first (leftmost) match --
  verified by reasoning through the regex mechanics against realistic
  phrasings, not (yet) a live-reproduced bug.
- **Remaining latent, NOT yet reproduced against real data:**
  `_extract_threshold_condition` (decides whether to route a market into the
  numeric-threshold path at all) does not have the same bare-year exclusion
  guard that `_extract_latest_number` got in the 2026-08-22 fix
  (`_looks_like_bare_year`). A title/description phrased like "...extended to
  at least 2026" (a trigger phrase immediately followed by a bare year in a
  non-threshold sense) could be wrongly routed into the numeric-threshold path
  with threshold=2026. No market in the current test set reproduces this --
  flagged for symmetry with the evidence-side fix; apply the same guard if/when
  a real market surfaces it.

### Submarket price-history tracking for numeric-threshold markets -- real feature gap, not built

Live-confirmed 2026-08-23, two concrete failures on real markets in the same
session:

- `bitcoin-above-64k-on-august-17-2026` (ground truth Yes): resolved WRONG
  (confidence 0.551) because the top-ranked evidence sentence was about
  Binance's futures-to-spot **volume ratio** ("ratio now stands at 7.82"), not
  price -- 7.82 got compared against the $64,000 threshold as if it were the
  BTC price.
- `will-the-odyssey-5th-weekend-box-office...` (earlier finding, same failure
  mode in a different vertical): a real, correctly-parsed dollar figure ($1.2
  million) was extracted, but it described the film's **limited-release total
  gross**, not its **5th-weekend gross** -- the specific metric the market
  actually asks about.

Both are the same underlying problem: `_decide_numeric_threshold` has no way
to verify an extracted number is measuring the same metric/time-window the
market's own threshold condition refers to -- it just takes the first
plausible dollar figure in ranked news evidence. A real fix likely means a
different resolution *strategy* for these markets, not a better regex:
tracking the market's actual price history (high/low over its resolution
window) from a structured price-data source, instead of incidental
news-article mentions.

User-specified requirements for this (not yet designed in detail):

- Track the **best (high) and lowest (low) price** over the market's real
  resolution window, not just a single point-in-time mention.
- **Submarket reopening**: some Polymarket submarkets close and then reopen.
  When that happens, the resolution window should run from the most recent
  **reopening** to today, not the market's original open date. How a reopen
  event is actually represented in the source platform's own data (Polymarket
  Gamma API market/event history) has not been investigated at all.
- Data source: the market's own named resolution source is often a structured
  price-data page already tagged `"primary"` (e.g. `pythdata.app`,
  `binance.com` trade pages, via `TIER1_DOMAINS`/`resolve_named_source`) rather
  than a news outlet. Whether that source (or another free one) exposes a real
  historical high/low API -- and whether it's free/keyless, per this project's
  zero-marginal-cost constraint -- has not been investigated.

Explicitly deprioritized for now (crypto/finance vertical, user has prioritized
sports/politics) and explicitly excluded from regular `run_eval.py` batches
until this flow exists -- testing crypto price-threshold markets against the
current news-article-based flow just produces known-bad noise, not a useful
signal. `will-spcx-reach-145-in-august-2026` and `will-xauusd-reach-4400-in-august-2026`
are the same class of market and additionally have a `query_builder.py`
entity-extraction bug producing a visibly garbled second query -- documented,
not investigated, same deprioritization applies.

### Evidence date/event-instance disambiguation -- real gap confirmed, not built

Confirmed by direct code inspection (`verdict_engine.py`,
`relevance_ranker.py`, `evidence_retriever.py`, 2026-08-23): there is
currently NO mechanism anywhere in the pipeline that verifies a piece of
retrieved evidence actually refers to the correct calendar instance of a
recurring event. The wrong-subject veto (`_sentence_mentions_other_entity`)
only catches a **different named entity** (a different bill/person/
institution); it has no date/year awareness at all.

This has never been exposed live because every market in the test set so far
has a uniquely-identifiable subject (a specific bill, a specific person, "the
2026 World Cup" as a named tournament) that doesn't recur under the same name
across different years/seasons. A recurring "Team A vs Team B" market is
exactly the shape that would expose it: the same two teams playing in a
*different* season produces evidence with identical entity names that the
current entity-based veto cannot distinguish.

`nba-playoffs-who-will-win-series-lakers-vs-rockets` (added to
`data/markets.json` 2026-08-23, real Polymarket data, ground truth "Lakers",
close_date 2026-05-04) was added specifically to test this -- Lakers vs.
Rockets is a recurring NBA matchup, so a news search could plausibly surface
an article about a *different* Lakers-Rockets game from another season.

**UPDATE 2026-08-23: FIXED.** Real search results didn't reproduce this by
luck, so it was demonstrated deliberately (constructed evidence describing
the actual 2009 Lakers-Rockets series) and confirmed live: the market wrongly
resolved YES. Fixed with `_market_expected_year()` +
`_sentence_mentions_conflicting_year()`, gating all winner/elimination
matches in `_decide_multi_outcome`. A second, narrower version of the same
problem was found in the same investigation -- two teams can meet more than
once in the SAME year (a regular-season game vs. a separate playoff series)
-- and fixed the same way with `_market_is_playoff_context()` +
`_sentence_mentions_conflicting_phase()`. Both scoped deliberately narrow
(checked only on a sentence that already matched a candidate phrase, not a
blanket pre-filter) after a broader version was tried and rejected for
breaking legitimate evidence. See ledger 2026-08-23 entries and commit
`cd4926b`. Deliberately NOT solved: a full round/context taxonomy (group
stage vs. knockout, First Round vs. other rounds) beyond the single
playoff-vs-regular-season distinction -- flagged by the user as a real,
narrower residual gap, not attempted without a concrete case to design
against.

### Keyword-match-as-verdict is fundamentally unscalable -- architecture change, in progress

User pushed back directly (2026-08-23) after watching real live false
positives get found one after another in the same session (BoomBoys crowned
tournament champion off a losing win-loss record; a not-yet-played match
treated as decided; a constructed "analysts say... eliminated" opinion
sentence treated as fact) -- each one traced back to the same root cause: a
keyword/phrase match was being trusted AS the verdict, with each new false
positive "fixed" by adding more keywords. That's real whack-a-mole against
the infinite variety of real English phrasing, not a fix.

**Architecture change built in response** (commit `6a3f89f`): in
`_decide_multi_outcome` and `_decide_date_thresholds`, a keyword/phrase match
is now only a CANDIDATE -- kept cheap deliberately, for efficiency, so the
embedding model only runs once a candidate has already cleared the free
keyword filter -- which must then pass semantic verification
(`_verify_winner_candidate` / `_verify_head_to_head_candidate` /
`_verify_elimination_candidate`) before being trusted. Empirically
calibrated: MARGIN (positive_sim - negative_sim), not positive_sim alone,
separates real confirmations from false positives (a false "eliminated"
opinion sentence scored a HIGHER raw positive_sim than a true "wins the
race" sentence -- margin still separated them cleanly). Two templates
needed: a general one (works for tournament/election/award-shaped markets)
and an opponent-aware one specifically for head-to-head "Team A vs Team B"
markets, whose title shape ("Who Will Win Series? - X vs. Y") doesn't score
well against the general template at all.

**Explicitly NOT yet done, and NOT the destination** (user's own framing):
this is still a generic, off-the-shelf sentence-embedding model with a
calibrated threshold -- "mix and match," not a purpose-built classifier.
`_decide_binary`'s existing semantic fallback (Task 23) was NOT touched by
this change; it still only runs when keyword matching finds nothing, not as
a verification layer on keyword matches. Extending the same
candidate-then-verify pattern to `_decide_binary` and
`_decide_numeric_threshold` is a natural next step, not yet done -- no live
false positive has been found in those paths yet to calibrate against.

### A real, purpose-trained resolution classifier -- longer-term vision, not started

Raised directly by the user (2026-08-23) in the same conversation as the
verification-layer work above: the real destination isn't a generic sentence
embedding model bolted onto keyword lists -- it's something trained
specifically to understand "is this actually evidence a market resolved a
specific way," which the user explicitly acknowledged requires labeled
training data ("evidence sentence -> correct resolution" pairs) that doesn't
exist yet.

User's explicit refinement on the input shape: NOT a minimal
"article text + description -> outcome" pair -- the classifier should take
the FULL structured context a human reviewer would actually use: article
text, market description, the options list, and the market's own type/shape
(binary / multi-outcome / numeric-threshold / date-threshold), not just raw
text. This matters because the correct answer genuinely depends on shape --
e.g. "wins over X" means something different for a head-to-head market than
for an 8-option tournament bracket -- the same generic embedding template
had to be split into two different templates for exactly this reason during
the verification-layer work above (see that section's calibration notes).
A real trained classifier should have that structure available as input,
not just prose.

The natural path there: `run_eval.py`'s `data/eval_history.jsonl` (added
2026-08-23) already started accumulating exactly this shape of data --
real evidence, real verdicts, real ground truth, tagged to a code version --
almost incidentally, before this conversation connected it to that purpose.
Once there's enough real accumulated history, training or fine-tuning a
purpose-built classifier (still plausibly cheap/local, not necessarily a
paid LLM -- e.g. a lightweight classifier on top of embeddings) becomes a
real option instead of a speculative one. Not started; flagged here so the
eval-history data collection is understood to serve this future purpose,
not just today's regression tracking.

### Relevance-ranker threshold (`SIMILARITY_THRESHOLD = 0.35`) -- not empirically calibrated, agreed to fix next

Raised by the user directly (2026-08-23) while discussing reliability:
unlike the verdict-engine thresholds (0.72 semantic-fallback, 0.025
multi-outcome verification margin), `SIMILARITY_THRESHOLD` in
`resolution_finder/config.py` was never empirically calibrated against real
accept/reject article pairs -- it's an inherited default. There is already
direct evidence it's imperfect: a real World Cup market had a genuinely
relevant article score below 0.35 and get silently dropped, which is what
motivated building `best_below_threshold()`/`_promote_best_below_threshold()`
earlier this session as a safety net, not a fix to the threshold itself.

User's explicit design principle, stated directly and applicable to this
calibration: **the system does not need to always give an answer, but if it
gives one, it needs to be correct.** Not resolving (UNCLEAR/NO_EVIDENCE) is
an acceptable, even preferred outcome; a confidently wrong YES/NO is not.
Applied to this threshold specifically: when a real accept/reject pair is
ambiguous, bias the calibration toward the STRICTER value (exclude a
borderline article -- worst case, UNCLEAR/NO_EVIDENCE, safe) rather than the
looser one (include a barely-relevant article -- worst case, it feeds a
wrong verdict). User has explicitly asked for this calibration to be done
next, using the same empirical methodology as the verdict-engine thresholds
(real article/market pairs, measured similarity, threshold set from the
real gap found -- not guessed).
