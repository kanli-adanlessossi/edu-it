"""Verification checks on generated text (Requirements 7.7 to 7.10, 7.12).

Pure functions. Duplicate detection is exact on normalised text only;
near-similarity is covered solely by :func:`similarity`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Final

SHINGLE_SIZE: Final[int] = 5

_WORD = re.compile(r"\w+", re.UNICODE)
_SENTENCE_END = re.compile(r"[.!?]+")
_VOWEL_GROUP = re.compile(r"[aeiouyàâäéèêëîïôöùûüœæ]+")


def normalise(text: str) -> str:
    """Unicode-normalise (NFKC), casefold and collapse whitespace."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def is_duplicate(text: str, existing: Iterable[str]) -> bool:
    """True when the normalised text equals the normalised text of an existing item."""
    target = normalise(text)
    return any(normalise(other) == target for other in existing)


@dataclass(frozen=True, slots=True)
class Coverage:
    """Share of concepts covered and the concepts with no item, in input order."""

    share: float
    uncovered: tuple[str, ...]


def coverage(concepts: Iterable[str], covered: Collection[str]) -> Coverage:
    """Compute concept coverage; an empty concept set counts as fully covered."""
    unique = tuple(dict.fromkeys(concepts))
    uncovered = tuple(c for c in unique if c not in covered)
    share = 1.0 if not unique else (len(unique) - len(uncovered)) / len(unique)
    return Coverage(share=share, uncovered=uncovered)


def _words(text: str) -> list[str]:
    return _WORD.findall(text)


def _syllables(word: str) -> int:
    return max(1, len(_VOWEL_GROUP.findall(word.casefold())))


def _readability(
    text: str, *, base: float, sentence_weight: float, syllable_weight: float
) -> float:
    words = _words(text)
    if not words:
        return 0.0
    sentences = max(1, len([s for s in _SENTENCE_END.split(text) if _words(s)]))
    syllables = sum(_syllables(w) for w in words)
    return (
        base
        - sentence_weight * (len(words) / sentences)
        - syllable_weight * (syllables / len(words))
    )


def readability_en(text: str) -> float:
    """Flesch reading ease; 0.0 for text without words."""
    return _readability(text, base=206.835, sentence_weight=1.015, syllable_weight=84.6)


def readability_fr(text: str) -> float:
    """Kandel-Moles reading ease (French Flesch); 0.0 for text without words."""
    return _readability(text, base=207.0, sentence_weight=1.015, syllable_weight=73.6)


def _shingles(text: str) -> set[tuple[str, ...]]:
    tokens = _words(normalise(text))
    return {tuple(tokens[i : i + SHINGLE_SIZE]) for i in range(len(tokens) - SHINGLE_SIZE + 1)}


def similarity(text: str, corpus: Iterable[str]) -> float:
    """Share of the text's 5-word shingles found in the corpus (containment, 0..1).

    Text shorter than one shingle has similarity 0.0.
    """
    own = _shingles(text)
    if not own:
        return 0.0
    known: set[tuple[str, ...]] = set()
    for source in corpus:
        known |= _shingles(source)
    return len(own & known) / len(own)


__all__ = [
    "SHINGLE_SIZE",
    "Coverage",
    "coverage",
    "is_duplicate",
    "normalise",
    "readability_en",
    "readability_fr",
    "similarity",
]
