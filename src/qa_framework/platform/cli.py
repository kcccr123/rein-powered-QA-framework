"""Command line entry point for a Unity training/evaluation DAG."""

from __future__ import annotations

import argparse
from pathlib import Path
import signal
import sys

from qa_framework.platform.config import load_dag
from qa_framework.platform.runner import RunManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Unity training/evaluation DAG on one Docker host.")
    parser.add_argument("suite", type=Path, help="DAG YAML file")
    args = parser.parse_args()
    try:
        config = load_dag(args.suite)
    except (OSError, TypeError, ValueError) as error:
        parser.error(str(error))

    def cancel(signum, frame):
        raise KeyboardInterrupt

    previous_handlers = {}
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            sig = getattr(signal, name)
            previous_handlers[sig] = signal.signal(sig, cancel)
    try:
        return RunManager(config).run()
    except KeyboardInterrupt:
        return 130
    except OSError as error:
        print(f"Cannot save suite output: {error}", file=sys.stderr)
        return 1
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
