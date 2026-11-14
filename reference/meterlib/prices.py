"""Price lookup and the cost of one event in integer nano-dollars (SPEC.md §5)."""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal

from .util import scale_percent

DEFAULT_PRICES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "prices.json")

_DATE_SUFFIX = re.compile(r"-[0-9]{8}\Z")


def rate(price) -> int:
    """USD per 1M tokens to whole nano-dollars per token (prices have at most 3 decimals)."""
    return int((Decimal(str(price)) * 1000).to_integral_value())


class Prices:
    def __init__(self, data: dict):
        self.data = data
        self.models = data.get("models", {})
        self.checked = data.get("checked")
        self.web_search = {k: rate(v) * 1000 for k, v in (data.get("web_search_per_1000") or {}).items()}
        # rate() gives nano-dollars per token for a per-million price; a per-1,000 price is 1,000x that per item.
        self.aliases = {}
        for model_id, row in self.models.items():
            for alias in row.get("aliases", []) or []:
                self.aliases[alias] = model_id

    @classmethod
    def load(cls, path: str | None = None) -> "Prices":
        with open(path or DEFAULT_PRICES, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def lookup(self, model: str):
        """The price row's id for a model id: exact, then an alias, then without a -YYYYMMDD suffix. Never fuzzy."""
        for candidate in (model, _DATE_SUFFIX.sub("", model or "")):
            if candidate in self.models:
                return candidate
            if candidate in self.aliases:
                return self.aliases[candidate]
        return None

    def cost_nano(self, ev: dict) -> int | None:
        """The API-equivalent cost of one event, or None when its model has no price."""
        model_id = self.lookup(ev["model"])
        if model_id is None:
            return None
        row = self.models[model_id]
        prompt = ev["input"] + ev["cache_read"] + ev["cache_write"] + ev["cache_write_1h"]
        rates = row
        tier = row.get("long_context")
        if tier and prompt > tier["above_input_tokens"]:
            rates = tier
        r_in = rate(rates["input"])
        r_cw = rate(rates["cache_write"]) if rates.get("cache_write") is not None else r_in
        r_cw1h = rate(rates["cache_write_1h"]) if rates.get("cache_write_1h") is not None else r_cw
        base = (
            ev["input"] * r_in
            + ev["cache_read"] * rate(rates["cache_read"])
            + ev["cache_write"] * r_cw
            + ev["cache_write_1h"] * r_cw1h
            + ev["output"] * rate(rates["output"])
        )
        if ev.get("speed") == "fast" and row.get("fast_percent"):
            base = scale_percent(base, int(row["fast_percent"]))
        if ev.get("geo") == "us" and row.get("us_geo_percent"):
            base = scale_percent(base, int(row["us_geo_percent"]))
        searches = ev.get("web_search") or 0
        if searches:
            base += searches * self.web_search.get(row.get("provider", ""), 0)
        return base
