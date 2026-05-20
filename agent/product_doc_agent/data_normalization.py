from __future__ import annotations

import re

from agent.product_doc_agent.models import ExtractedFact


class DataNormalizer:
    def normalize(self, facts: list[ExtractedFact]) -> list[ExtractedFact]:
        for fact in facts:
            if isinstance(fact.value, str):
                fact.value = re.sub(r"\s+", " ", fact.value).strip()
            if fact.domain == "fee" and isinstance(fact.value, str):
                fact.value = {"raw_text": fact.value, "amounts": extract_amounts(fact.value)}
        return facts


def extract_amounts(text: str) -> list[dict[str, str]]:
    amounts: list[dict[str, str]] = []
    for match in re.finditer(r"(?P<amount>\d+(?:\.\d+)?)\s*元\s*/?\s*(?P<unit>月|年|2年|线|次|分钟|条|GB|MB|台)?", text):
        amounts.append({"amount": match.group("amount"), "unit": match.group("unit") or ""})
    return amounts
