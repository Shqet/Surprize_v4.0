from __future__ import annotations

from typing import Optional, Sequence

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
from PyQt6.QtCore import Qt

import numpy as np  # pyqtgraph/opengl always installed per your note
import pyqtgraph as pg  # type: ignore
import pyqtgraph.opengl as gl  # type: ignore
from pyqtgraph.Vector import Vector  # type: ignore


class Trajectory3DView(QWidget):
    """
    Stable 3D widget (no layout replacement):
      - GLViewWidget is created once and never removed
      - all text states are shown as overlay
      - clear/render only changes OpenGL items, not Qt layout
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        self._view = gl.GLViewWidget()
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._layout.addWidget(self._view)

        self._grid: Optional[object] = None
        self._line: Optional[object] = None

        # overlay status
        self._status = QLabel("", self)
        self._status.setObjectName("lbl_vis_status_overlay")
        self._status.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._status.setWordWrap(True)
        self._status.setVisible(True)
        self._status.setStyleSheet(
            "QLabel { color: white; background-color: rgba(0,0,0,140); padding: 6px; border-radius: 4px; }"
        )
        self._status.move(12, 12)
        self._status.setMaximumWidth(400)

        # initial state
        self.set_status("Визуализация траектории (3D)\nДанные отсутствуют")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._status.move(12, 12)
        self._status.setMaximumWidth(max(260, self.width() // 2))

    def set_status(self, text: Optional[str]) -> None:
        """
        Overlay text; does not touch layout.
        """
        if not text:
            self._status.setVisible(False)
            self._status.setText("")
            return

        self._status.setText(text)
        self._status.adjustSize()
        self._status.setVisible(True)

    def clear(self) -> None:
        """
        Clear OpenGL items only.
        """
        try:
            for it in list(getattr(self._view, "items", [])):
                self._view.removeItem(it)
        except Exception:
            pass
        self._grid = None
        self._line = None

    def show_failed(self, details: Optional[str] = None) -> None:
        self.clear()
        if details:
            self.set_status(f"Failed\n{details}")
        else:
            self.set_status("Failed")

    def set_points(self, points: Sequence[tuple[float, float, float]]) -> None:
        """
        Render polyline and fit camera. Does not touch Qt layout.
        """
        self.clear()

        if not points:
            self.set_status("Визуализация траектории (3D)\nНет точек")
            return

        pos = np.asarray(points, dtype=float)

        mn = pos.min(axis=0)
        mx = pos.max(axis=0)
        center = (mn + mx) / 2.0
        span = mx - mn
        size = float(span.max()) if float(span.max()) > 0 else 1.0

        # grid (как было у тебя)
        grid = gl.GLGridItem()
        try:
            grid.setSize(x=size, y=size)
            grid.setSpacing(x=max(size / 10.0, 1e-6), y=max(size / 10.0, 1e-6))
        except Exception:
            pass
        self._view.addItem(grid)

        # line_strip (исправленный режим)
        line = gl.GLLinePlotItem(
            pos=pos,
            mode="line_strip",
            antialias=True,
            width=2,
            color=(1.0, 0.4, 0.2, 1.0),
        )
        self._view.addItem(line)

        self._grid = grid
        self._line = line

        # camera fit
        try:
            self._view.opts["center"] = Vector(float(center[0]), float(center[1]), float(center[2]))
        except Exception:
            pass

        dist = float(size) * 3.0
        try:
            self._view.setCameraPosition(distance=dist, elevation=20, azimuth=45)
        except Exception:
            try:
                self._view.opts["distance"] = dist
            except Exception:
                pass

        # hide overlay after successful render
        self.set_status(None)
