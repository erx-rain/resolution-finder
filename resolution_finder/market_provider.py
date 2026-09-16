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
                # A real market can genuinely have no close_date in its
                # source data (some pulled March Madness markets --
                # Market.close_date is Optional for exactly this reason,
                # see models.py). Found live 2026-09-16: this loader still
                # called date.fromisoformat() unconditionally, so the real
                # production entrypoint (run_scan.py) crashed on the very
                # first get_unresolved_markets() call against the current
                # data/markets.json -- before the pipeline's own per-
                # market try/except ever got a chance to run, taking down
                # every market in the batch, not just the ones missing a
                # close_date. run_eval.py's loader got the equivalent fix
                # on 2026-09-07 (commit ff6369d); this one was missed.
                close_date=date.fromisoformat(item["close_date"]) if item.get("close_date") else None,
            )
            for item in raw
        ]
