"""LexiTrack — PDF Vocabulary Learning & Review.

A local-first desktop application that imports vocabulary from PDF documents
and reviews it one word at a time.

The layering is deliberate and one-directional::

    ui  ->  services  ->  repositories  ->  database
              |
              +-------->  parsers  ->  models
              +-------->  normalization
              +-------->  exporters

Nothing above the repository layer issues SQL, and nothing below the service
layer knows that a UI exists.
"""

__version__ = "0.2.0"
__all__ = ["__version__"]
