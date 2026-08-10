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
