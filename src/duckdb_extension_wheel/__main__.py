"""Entry point for python -m duckdb_extension_wheel."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
