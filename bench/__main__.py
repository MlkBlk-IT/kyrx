"""Run the reference bench and print the comparative report.

    python -m bench
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from bench.report import build_report
from bench.scenario import run_scenario


def main() -> int:
    """Run the bench and print the report. Returns the process exit code."""
    with tempfile.TemporaryDirectory(prefix="kyrx-bench-") as directory:
        measurements = asyncio.run(run_scenario(Path(directory)))
    report = build_report(measurements)
    print(report.to_markdown())
    print()
    print(report.to_json())
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
