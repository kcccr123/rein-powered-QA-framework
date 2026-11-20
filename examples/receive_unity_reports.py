"""Receive Unity unit reports and print their evidence as JSON lines."""

import argparse
import json

from qa_framework import QAReportReceiver


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8800)
    args = parser.parse_args()

    with QAReportReceiver(port=args.port) as receiver:
        print(f"Waiting for Unity reports on 127.0.0.1:{receiver.port}", flush=True)
        try:
            while True:
                report = receiver.receive()
                print(json.dumps(report.to_dict(), allow_nan=False), flush=True)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
