#!/usr/bin/env python3
"""Backward-compatible NDSS ingest entrypoint."""

from fetch_security_conferences import CONFERENCES, write_conference

import datetime as dt
import pathlib


def main() -> None:
    current_year = dt.datetime.utcnow().year
    years = list(range(current_year, current_year - 5, -1))
    write_conference(CONFERENCES["ndss"], years, pathlib.Path("public/data"))


if __name__ == "__main__":
    main()
