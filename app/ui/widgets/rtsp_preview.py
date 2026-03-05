from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QImageReader, QPainter, QPixmap
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget, QSizePolicy, QHBoxLayout


class RtspPreviewWidget(QWidget):
    """
    Polls a latest.jpg file and renders it in a QLabel.
    Designed for low-overhead preview updates (no streaming decoder in UI).
    """

    def __init__(
        self,
        image_path: str,
        title: str,
        poll_ms: int = 200,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._path = Path(image_path)
        self._poll_ms = int(poll_ms)
        self._last_mtime: Optional[float] = None
        self._last_update_ts: Optional[float] = None
        self._startup_wall_ts: float = time.time()
        self._status_dead_sec = 2.0
        self._pixmap: Optional[QPixmap] = None

        self._title = QLabel(title, self)
        self._title.setStyleSheet("QLabel { color: #ddd; font-weight: 600; }")
        self._status = QLabel("не подключено", self)
        self._status.setStyleSheet("QLabel { color: #f5a623; }")

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self._title, stretch=1)
        head.addWidget(self._status, stretch=0)

        self._canvas = _PreviewCanvas(self)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(head)
        layout.addWidget(self._canvas, stretch=1)

        self._timer = QTimer(self)
        self._timer.setInterval(max(100, self._poll_ms))
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_path(self, image_path: str) -> None:
        self._path = Path(image_path)
        self._last_mtime = None
        self._last_update_ts = None

    def _tick(self) -> None:
        now = time.monotonic()
        try:
            if not self._path.exists():
                self._update_status(False, now)
                return
            mtime = self._path.stat().st_mtime
        except Exception:
            self._update_status(False, now)
            return

        # Ignore stale preview file from previous app runs until it is refreshed.
        if self._last_mtime is None and mtime < (self._startup_wall_ts - 0.5):
            self._last_mtime = mtime
            self._update_status(False, now)
            return

        if self._last_mtime is not None and mtime <= self._last_mtime:
            self._update_status(self._last_update_ts is not None, now)
            return

        self._last_mtime = mtime
        self._last_update_ts = now

        try:
            reader = QImageReader(str(self._path))
            image = reader.read()
            if image.isNull():
                self._update_status(False, now)
                return
            pix = QPixmap.fromImage(image)
        except Exception:
            self._update_status(False, now)
            return

        if pix.isNull():
            self._update_status(False, now)
            return

        self._pixmap = pix
        self._canvas.set_pixmap(pix)
        self._update_status(True, now)

    def _update_status(self, connected: bool, now: float) -> None:
        is_alive = False
        if self._last_update_ts is not None:
            is_alive = (now - self._last_update_ts) <= self._status_dead_sec
        if connected or is_alive:
            self._status.setText("подключено")
            self._status.setStyleSheet("QLabel { color: #7ed321; }")
        else:
            self._status.setText("не подключено")
            self._status.setStyleSheet("QLabel { color: #f5a623; }")


class _PreviewCanvas(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(320, 180)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)

        if self._pixmap is None or self._pixmap.isNull():
            painter.end()
            return

        w = max(1, self.width())
        h = max(1, self.height())
        scaled = self._pixmap.scaled(
            w,
            h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (w - scaled.width()) // 2
        y = (h - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()
