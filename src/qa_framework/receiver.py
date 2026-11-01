"""Send a TCP readiness handshake, then receive newline-delimited unit reports."""

from __future__ import annotations

import json
import os
import socket

from qa_framework.evaluation.runner import EVALUATION_SCHEMA_VERSION, UnitReport


class QAReportReceiver:
    """Announce readiness to each connected sender and decode completed unit reports."""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 8800) -> None:
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._listener.bind((host, port))
            self._listener.listen(1)
        except OSError:
            self._listener.close()
            raise
        self._client = None
        self._stream = None

    @property
    def port(self) -> int:
        return self._listener.getsockname()[1]

    def receive(self) -> UnitReport:
        """Wait for one report, accepting a new Unity connection after a clean disconnect."""
        while True:
            if self._client is None:
                self._client, _ = self._listener.accept()
                try:
                    handshake = {
                        "type": "ready",
                        "schema_version": EVALUATION_SCHEMA_VERSION,
                        "process_id": os.getpid(),
                        "session_id": os.environ.get("QA_RECORDER_SESSION", ""),
                    }
                    self._client.sendall((json.dumps(handshake) + "\n").encode("utf-8"))
                except OSError:
                    self._close_client()
                    continue
                self._stream = self._client.makefile("rb")
            line = self._stream.readline()
            if line:
                return UnitReport.from_dict(json.loads(line))
            self._close_client()

    def _close_client(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        if self._client is not None:
            self._client.close()
            self._client = None

    def close(self) -> None:
        self._close_client()
        self._listener.close()

    def __enter__(self) -> QAReportReceiver:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
