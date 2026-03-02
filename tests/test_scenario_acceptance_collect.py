from __future__ import annotations

from pathlib import Path

from tools.scenario_acceptance_collect import _checklist, _extract_timeline, _parse_log, _pick_scenario_id, _scenario_start_time


def test_acceptance_collect_extracts_timeline_and_checklist(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text(
        "\n".join(
            [
                "2026-03-02 10:00:00 | INFO | orchestrator | SCENARIO_ID scenario_id=scn_1 source=mayak_test",
                "2026-03-02 10:00:01 | INFO | orchestrator | SERVICE_STATUS service=mayak_spindle status=RUNNING",
                "2026-03-02 10:00:02 | INFO | mayak_spindle | MAYAK_READY_STATE service=mayak_spindle ready=1",
                "2026-03-02 10:00:03 | INFO | orchestrator | MAYAK_TEST_START scenario_id=scn_1 profile=linear duration_sec=5.0",
                "2026-03-02 10:00:04 | INFO | mayak_spindle | MAYAK_SPINDLE_STATE spindle=sp1 prev=READY new=MOVING",
                "2026-03-02 10:00:05 | INFO | orchestrator | MAYAK_TEST_STOP scenario_id=scn_1",
                "2026-03-02 10:00:06 | INFO | orchestrator | SERVICE_STATUS service=ballistics_model status=RUNNING",
                "2026-03-02 10:00:07 | INFO | orchestrator | SERVICE_STATUS service=ballistics_model status=STOPPED",
                "2026-03-02 10:00:08 | INFO | orchestrator | ORCH_STATE_CHANGE from=RUNNING to=IDLE",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows = _parse_log(log_path)
    scenario_id = _pick_scenario_id(rows, forced="")
    assert scenario_id == "scn_1"
    start_ts = _scenario_start_time(rows, scenario_id)
    timeline = _extract_timeline(rows, scenario_id, start_ts)
    checks = _checklist(timeline)

    assert checks["daemon_up"] is True
    assert checks["mayak_ready"] is True
    assert checks["start_test"] is True
    assert checks["telemetry_changes"] is True
    assert checks["stop_test"] is True
    assert checks["trajectory_run"] is True
    assert checks["idle"] is True

