from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.events import LogEvent, OrchestratorStateEvent, ProcessOutputEvent, ServiceStatusEvent
from app.core.ui_bridge import UIBridge
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.states import OrchestratorState


class MainWindow(QMainWindow):
    def __init__(self, orchestrator: Orchestrator, bridge: UIBridge) -> None:
        super().__init__()
        self._orch = orchestrator
        self._bridge = bridge

        self.setWindowTitle("Surprize v3.0 (v0)")
        self.resize(980, 640)

        # --- widgets ---
        self._btn_start = QPushButton("Start")
        self._btn_stop = QPushButton("Stop")
        self._lbl_state = QLabel("State: ?")

        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)

        self._svc_table = QTableWidget(0, 2)
        self._svc_table.setHorizontalHeaderLabels(["Service", "Status"])
        self._svc_table.horizontalHeader().setStretchLastSection(True)

        # --- layout ---
        top_row = QHBoxLayout()
        top_row.addWidget(self._btn_start)
        top_row.addWidget(self._btn_stop)
        top_row.addSpacing(16)
        top_row.addWidget(self._lbl_state)
        top_row.addStretch(1)

        layout = QVBoxLayout()
        layout.addLayout(top_row)
        layout.addWidget(QLabel("Log / Output"))
        layout.addWidget(self._log_view, 2)
        layout.addWidget(QLabel("Services"))
        layout.addWidget(self._svc_table, 1)

        root = QWidget()
        root.setLayout(layout)
        self.setCentralWidget(root)

        # --- connections (UI -> orchestrator) ---
        self._btn_start.clicked.connect(self._on_start_clicked)
        self._btn_stop.clicked.connect(self._on_stop_clicked)

        # --- connections (bridge -> UI thread) ---
        self._bridge.log_event.connect(self._on_log_event)
        self._bridge.process_output_event.connect(self._on_process_output_event)
        self._bridge.service_status_event.connect(self._on_service_status_event)
        self._bridge.orch_state_event.connect(self._on_orch_state_event)

        # initial UI state
        self._apply_orch_state(OrchestratorState.IDLE.value)

    # ---------------- UI handlers ----------------

    def _on_start_clicked(self) -> None:
        # v0 uses fixed profile name
        self._orch.start("default")

    def _on_stop_clicked(self) -> None:
        self._orch.stop()

    # ---------------- Event handlers ----------------

    def _on_orch_state_event(self, e: OrchestratorStateEvent) -> None:
        self._apply_orch_state(e.state)

    def _apply_orch_state(self, state: str) -> None:
        self._lbl_state.setText(f"State: {state}")

        # Button enable/disable rules per spec:
        # Stop disabled when IDLE
        # Start disabled when RUNNING/STOPPING
        if state == OrchestratorState.IDLE.value:
            self._btn_start.setEnabled(True)
            self._btn_stop.setEnabled(False)
        elif state in (OrchestratorState.RUNNING.value, OrchestratorState.STOPPING.value):
            self._btn_start.setEnabled(False)
            self._btn_stop.setEnabled(True)
        else:
            # PRECHECK / ERROR etc.
            self._btn_start.setEnabled(False)
            self._btn_stop.setEnabled(True)

    def _on_log_event(self, e_obj: object) -> None:
        e = e_obj  # type: ignore[assignment]
        if not isinstance(e, LogEvent):
            return
        self._append_line(f"[{e.level}] {e.source} {e.code} {e.message}")

    def _on_process_output_event(self, e_obj: object) -> None:
        e = e_obj  # type: ignore[assignment]
        if not isinstance(e, ProcessOutputEvent):
            return
        # Keep it compact; user can see stream
        self._append_line(f"[{e.service_name}:{e.stream}] {e.line}")

    def _on_service_status_event(self, e_obj: object) -> None:
        e = e_obj  # type: ignore[assignment]
        if not isinstance(e, ServiceStatusEvent):
            return
        self._upsert_service_row(e.service_name, e.status)

    # ---------------- helpers ----------------

    def _append_line(self, line: str) -> None:
        # append-only; keep UI responsive
        self._log_view.append(line)

    def _upsert_service_row(self, service_name: str, status: str) -> None:
        row = self._find_service_row(service_name)
        if row is None:
            row = self._svc_table.rowCount()
            self._svc_table.insertRow(row)
            self._svc_table.setItem(row, 0, QTableWidgetItem(service_name))
            self._svc_table.setItem(row, 1, QTableWidgetItem(status))
        else:
            item = self._svc_table.item(row, 1)
            if item is None:
                self._svc_table.setItem(row, 1, QTableWidgetItem(status))
            else:
                item.setText(status)

        # Align status column
        status_item = self._svc_table.item(row, 1)
        if status_item is not None:
            status_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))

    def _find_service_row(self, service_name: str) -> Optional[int]:
        for r in range(self._svc_table.rowCount()):
            item = self._svc_table.item(r, 0)
            if item is not None and item.text() == service_name:
                return r
        return None
