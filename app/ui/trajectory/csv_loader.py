from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import csv

from PyQt6.QtCore import QObject, pyqtSignal, QRunnable, QThreadPool


class TrajectoryLoadSignals(QObject):
    ok = pyqtSignal(int, object)   # seq, points: list[tuple[float,float,float]]
    fail = pyqtSignal(int, str)    # seq, error


@dataclass(frozen=True)
class TrajectoryCsvSpec:
    filename: str = "trajectory.csv"


class TrajectoryCsvLoadTask(QRunnable):
    def __init__(self, seq: int, csv_path: Path) -> None:
        super().__init__()
        self.seq = seq
        self.csv_path = csv_path
        self.signals = TrajectoryLoadSignals()

    @staticmethod
    def _detect_xyz_indices(headers: list[str]) -> tuple[int, int, int]:
        norm = [h.strip().lower() for h in headers]

        # supports x,y,z and X,Y,Z due to lower()
        if "x" in norm and "y" in norm and "z" in norm:
            return norm.index("x"), norm.index("y"), norm.index("z")

        if "pos_x" in norm and "pos_y" in norm and "pos_z" in norm:
            return norm.index("pos_x"), norm.index("pos_y"), norm.index("pos_z")

        raise ValueError(f"Unsupported CSV columns: {headers!r}")

    def run(self) -> None:
        try:
            if not self.csv_path.exists():
                raise FileNotFoundError(str(self.csv_path))

            points: list[tuple[float, float, float]] = []
            with self.csv_path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if not header:
                    raise ValueError("Empty CSV (no header)")

                ix, iy, iz = self._detect_xyz_indices(header)

                for row in reader:
                    if not row:
                        continue
                    if len(row) <= max(ix, iy, iz):
                        continue
                    try:
                        points.append((float(row[ix]), float(row[iy]), float(row[iz])))
                    except Exception:
                        # skip malformed rows
                        continue

            if not points:
                raise ValueError("No valid points parsed from trajectory.csv")

            self.signals.ok.emit(self.seq, points)

        except Exception as ex:
            self.signals.fail.emit(self.seq, f"{type(ex).__name__}: {ex}")


class TrajectoryCsvLoader:
    """
    Async loader around QRunnable/QThreadPool.
    """

    def __init__(self, pool: QThreadPool | None = None, spec: TrajectoryCsvSpec | None = None) -> None:
        self._pool = pool or QThreadPool.globalInstance()
        self._spec = spec or TrajectoryCsvSpec()

    def start(self, seq: int, run_dir: str, on_ok, on_fail) -> None:
        csv_path = Path(run_dir) / self._spec.filename
        task = TrajectoryCsvLoadTask(seq=seq, csv_path=csv_path)
        task.signals.ok.connect(on_ok)
        task.signals.fail.connect(on_fail)
        self._pool.start(task)
