"""
bible_writer.py

BibleWriter: abstract base for all Bible module writers.
"""

from abc import ABC, abstractmethod
from pathlib import Path


class BibleWriter(ABC):
    """Abstract base for all Bible module writers.

    A writer owns one output file and knows how to persist verses to it.
    Verse rendering is delegated to the VerseFormatter injected at construction.
    """

    @abstractmethod
    def open(self, output_dir: Path) -> None:
        """Open (or create) the output file inside output_dir."""

    @abstractmethod
    def add_verse(self, osis_ref: str, tokens: list,
                  header: str = None, xrefs: dict = None) -> None:
        """Render and persist one verse."""

    @abstractmethod
    def write(self) -> None:
        """Finalize and close the output file."""
