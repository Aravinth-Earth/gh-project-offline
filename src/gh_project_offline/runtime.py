# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console

from .config import log_timestamp

CONSOLE = Console()


@dataclass(slots=True)
class RuntimeLogger:
    log_path: Path

    def emit(self, message: str) -> None:
        line = f"[{log_timestamp()}] {message}"
        self._write_line(line)
        CONSOLE.print(line)

    def write_only(self, message: str) -> None:
        line = f"[{log_timestamp()}] {message}"
        self._write_line(line)

    def write_exception(self, context: str, exc: BaseException) -> None:
        details = traceback.format_exception(type(exc), exc, exc.__traceback__)
        for line in "".join(details).rstrip().splitlines():
            self.write_only(line)

    def _write_line(self, line: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def create_run_logger(logs_dir: Path) -> RuntimeLogger:
    logs_dir.mkdir(parents=True, exist_ok=True)
    filename = datetime.now().strftime("session-%Y%m%d-%H%M%S.log")
    return RuntimeLogger(log_path=logs_dir / filename)
