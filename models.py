"""
models.py

Core data structures shared across the pipeline.
"""

from dataclasses import dataclass, field
from enum import Enum


class MappingDirection(Enum):
    TARGET_TO_SOURCE = "target_to_source"   # English-primary, source annotation (current)
    SOURCE_TO_TARGET = "source_to_target"   # source-primary, English annotation (forward interlinear)


# Source token classes that are prefixes/particles, not the stem of a display-word.
NON_STEM_CLASS = {'art', 'cj', 'prep', 'om', 'ptcl', 'rel'}


@dataclass
class SourceToken:
    """One token from the source language TSV (Hebrew/Aramaic/Greek)."""
    id: str
    text: str
    strongs: str        # normalized: always prefixed + zero-padded, e.g. H0776, G0976
    gloss: str
    token_class: str    # 'class' column: noun, verb, adj, prep, art, cj, etc.
    pos: str            # 'pos' column: noun, verb, adjective, preposition, suffix, etc.
    noun_type: str      # 'type' column: 'common', 'proper', or ''
    morph: str
    lang: str
    lemma: str = ""
    after: str = " "    # '' = join to next token (same display-word); ' ' = word boundary


@dataclass
class SourceWord:
    """One display-word in the source language: one or more SourceTokens joined
    by after=''. Carries the concatenated script, the stem token's Strong's number,
    and the lang for transliteration and link generation."""
    tokens: list            # list[SourceToken], in order
    stem: SourceToken       # token whose Strong's number is used for the dictionary link
    text: str               # concatenated script of all tokens
    lang: str               # lang of the stem token ('H', 'A', or 'G')
    is_proper: bool = False # True if stem is a proper noun (pos=noun, type=proper)


@dataclass
class TargetToken:
    """One token from the BSB target TSV."""
    id: str
    verse_id: str
    text: str
    skip_space_after: bool = False
    exclude: bool = False


@dataclass
class AlignmentRecord:
    """One alignment record mapping source token IDs to target token IDs."""
    source_ids: list
    target_ids: list
    record_id: str


@dataclass
class AlignedToken:
    """One output token: English text with aligned source language annotations.

    source_words is a list of SourceWord — one per display-word in the source.
    Each SourceWord carries its own script, Strong's number, and lang.
    """
    english: str
    skip_space_after: bool
    source_words: list = field(default_factory=list)    # list[SourceWord]
    is_plain_text: bool = False
    notes: list = field(default_factory=list)           # list of {noteId, text} dicts
