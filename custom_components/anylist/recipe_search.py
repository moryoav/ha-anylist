"""Local, word-based recipe ranking without changing recipe retrieval."""

from __future__ import annotations

from collections.abc import Iterable
import unicodedata
from typing import Any

from .client import Recipe

DEFAULT_SEARCH_LIMIT = 15
MAX_SEARCH_LIMIT = 50


def normalize_search_text(text: str) -> str:
    """Fold Unicode/case/accents and separate punctuation into words."""
    text = unicodedata.normalize("NFKD", text).casefold()
    return " ".join(
        "".join(
            char if char.isalnum() else " "
            for char in text
            if not unicodedata.category(char).startswith("M")
        ).split()
    )


def _is_minor_typo(first: str, second: str) -> bool:
    """Allow one insertion, deletion, substitution or adjacent transposition.

    Short words require exact matches to avoid broad false positives. This
    compares individual words, so extra title words never reduce relevance.
    """
    if min(len(first), len(second)) < 4 or abs(len(first) - len(second)) > 1:
        return False
    if len(first) > len(second):
        first, second = second, first
    index = next(
        (index for index, char in enumerate(first) if char != second[index]),
        len(first),
    )
    if len(first) != len(second):
        return first[index:] == second[index + 1 :]
    return first[index + 1 :] == second[index + 1 :] or (
        index + 1 < len(first)
        and first[index] == second[index + 1]
        and first[index + 1] == second[index]
        and first[index + 2 :] == second[index + 2 :]
    )


def _take_matches(query: set[str], words: set[str], *, fuzzy: bool = False) -> set[str]:
    """Consume distinct matching words in stable order, without double counting."""
    matched = set()
    for word in sorted(query):
        match = next(
            (
                candidate
                for candidate in sorted(words)
                if (_is_minor_typo(word, candidate) if fuzzy else word == candidate)
            ),
            None,
        )
        if match is not None:
            matched.add(word)
            words.remove(match)
    query.difference_update(matched)
    return matched


def search_recipes(
    recipes: Iterable[Recipe],
    query: str,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    include_ingredients: bool = False,
) -> dict[str, Any]:
    """Return compact candidates; scores are relevance, not confidence.

    Exact normalized titles score 100, all exact title words score 90, and
    remaining matches score at most 80 by query coverage. Title exact/typo
    weights are 1/0.8; ingredient exact/typo weights are 0.45/0.3. Require at
    least half the unique query words and never pad to the requested limit.
    """
    normalized_query = normalize_search_text(query)
    if not normalized_query:
        raise ValueError("Query must contain at least one letter or number")
    query_words = set(normalized_query.split())
    total = len(query_words)
    candidates = []
    for recipe in recipes:
        title = normalize_search_text(recipe.name)
        title_words = set(title.split())
        remaining = query_words.copy()
        title_exact = _take_matches(remaining, title_words)
        title_fuzzy = _take_matches(remaining, title_words, fuzzy=True)
        ingredient_exact: set[str] = set()
        ingredient_fuzzy: set[str] = set()
        if include_ingredients and remaining:
            ingredient_words = {
                word
                for ingredient in recipe.ingredients
                for word in normalize_search_text(ingredient.name or "").split()
            }
            ingredient_exact = _take_matches(remaining, ingredient_words)
            ingredient_fuzzy = _take_matches(remaining, ingredient_words, fuzzy=True)
        if (total - len(remaining)) * 2 < total:
            continue

        if title == normalized_query:
            score = 100.0
            explanation = "Exact normalized title"
        elif len(title_exact) == total:
            score = 90.0
            explanation = "All query words in title"
        else:
            score = round(
                80
                * (
                    len(title_exact)
                    + 0.8 * len(title_fuzzy)
                    + 0.45 * len(ingredient_exact)
                    + 0.3 * len(ingredient_fuzzy)
                )
                / total,
                2,
            )
            explanation = (
                f"Title: {len(title_exact) + len(title_fuzzy)}/{total} words"
                f" ({len(title_fuzzy)} typo matches)"
            )
            if ingredient_exact or ingredient_fuzzy:
                explanation += (
                    f"; ingredients: {len(ingredient_exact) + len(ingredient_fuzzy)}"
                    f"/{total} words ({len(ingredient_fuzzy)} typo matches)"
                )
        candidates.append(
            {
                "id": recipe.id,
                "name": recipe.name,
                "score": score,
                "match_explanation": explanation,
                "collections": [
                    {"id": value.id, "name": value.name}
                    for value in (getattr(recipe, "collections", []) or [])
                ],
            }
        )

    candidates.sort(
        key=lambda candidate: (
            -candidate["score"],
            normalize_search_text(candidate["name"]),
            candidate["id"],
        )
    )
    return {
        "recipes": candidates[:limit],
        "count": min(len(candidates), limit),
        "has_more": len(candidates) > limit,
    }
