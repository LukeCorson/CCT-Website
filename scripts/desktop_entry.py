"""Entry point for the native desktop bundle; normal users can use -m cct.app."""

from cct.app import main

if __name__ == "__main__":
    raise SystemExit(main())
