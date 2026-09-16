"""Allows ``python -m lexitrack``."""

import sys

from .main import _enable_high_dpi, main

if __name__ == "__main__":
    _enable_high_dpi()
    sys.exit(main())
