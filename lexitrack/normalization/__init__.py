"""Word normalization and runtime deduplication."""

from .deduplicator import deduplicate, iter_unique
from .word_normalizer import display_form, is_wordlike, normalize_word

__all__ = ["deduplicate", "display_form", "is_wordlike", "iter_unique", "normalize_word"]
