"""Retrieves the coding-standard sections relevant to the code under review (bonus requirement).

Deliberately simple keyword retrieval: each section lists its keywords, and a section's score is how
many of them appear in the query text (code + lint codes + test failures).
"""
import re

from . import config


def load_sections() -> list[dict]:
    text = config.STANDARD_PATH.read_text(encoding="utf-8")
    sections = []
    for block in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        header, body = block.split("\n", 1)
        keywords_line = re.search(r"^Keywords:(.*)$", body, flags=re.MULTILINE)
        keywords = [k.strip().lower() for k in keywords_line.group(1).split(",")] if keywords_line else []
        sections.append({
            "id": header.split(":")[0].strip(),
            "title": header.strip(),
            "text": re.sub(r"^Keywords:.*$", "", body, flags=re.MULTILINE).strip(),
            "keywords": keywords,
        })
    return sections


def retrieve(query: str, k: int = 4) -> list[dict]:
    """Return the k best-matching sections (only those with at least one keyword hit)."""
    query = query.lower()
    scored = []
    for section in load_sections():
        score = sum(1 for kw in section["keywords"] if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", query))
        if score:
            scored.append((score, section))
    scored.sort(key=lambda pair: -pair[0])
    return [section for _, section in scored[:k]]
