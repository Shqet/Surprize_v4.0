## mayak_spindle (v2 status)

Purpose: isolated spindle domain service for Mayak controller.

It hides raw D-cell protocol and exposes spindle-oriented API:
- `set_global_enable(enabled)`
- `set_spindle_speed("sp1|sp2", direction=-1|0|1, rpm=int)`
- `stop_spindle("sp1|sp2")`
- `fault_reset("sp1|sp2")`
- `get_spindle_state("sp1|sp2")`
- `spindle_ready("sp1|sp2")`
- `is_ready()`
- `get_health_snapshot()`
- operator limit update: `set_operator_limits(...)`
- privileged hard-limit update: `set_hard_limits(..., privileged=True)`

### Config (`services.mayak_spindle`)
- `d_map: dict[str, str]` required:
  - `SP1_ControlWord`, `SP1_TargetSpeed`, `SP1_StatusWord`, `SP1_ActualSpeed`
  - `SP2_ControlWord`, `SP2_TargetSpeed`, `SP2_StatusWord`, `SP2_ActualSpeed`
  - `SP1_ActualTorque`, `SP2_ActualTorque`, `SP1_Angle`
  - `SP1_Connected`, `SP2_Connected`
  - `Global_Enable`, `Sim_Time`, `Error_Code`
- `publish_period_ms: int` (default `50`)
- `global_enable: bool` (optional initial value)
- `transport` (UDP):
  - `cnc_host`, `cnc_port`, `listen_host`, `listen_port`, `machine_size`, `recv_timeout_sec`
- `hard_limits`:
  - `max_rpm_sp1`, `max_rpm_sp2`, `max_accel_rpm_s`, `max_torque`
- `operator_limits`:
  - same keys as hard limits
  - must be `<= hard_limits`
- `runtime.command_timeout_ms`
- `watchdog.cell` (optional heartbeat D-cell)
- `watchdog.max_packet_age_sec` (required for packet freshness control)
- `metrics.log_period_sec`

### Effective limit model
- Effective limits are computed as `min(hard, operator)` for each dimension.
- Operator limits are always editable (inside hard bounds).
- Hard limits require privileged access.
- When hard limits are reduced, operator limits are clamped down automatically.

### Readiness semantics
`is_ready()` returns `True` only when all are true:
- service status is `RUNNING`
- `global_enable != False`
- `error_code == 0`
- `degraded_reason == "none"`
- `sp1_state` and `sp2_state` are in `READY|MOVING|STARTING|STOPPING`

Degraded reasons:
- `io_errors`
- `packet_age`
- `fault_code`
- `offline_spindle`
- `none`

### Events emitted
- `ServiceStatusEvent(service_name="mayak_spindle", status=...)`
- `MayakSpindleTelemetryEvent(service="mayak_spindle", spindle="sp1|sp2", ...)`
- `MayakSpindleCommandEvent(...)`
- `MayakHealthEvent(...)`
- `LogEvent(...)`

### `MayakHealthEvent` payload (current)
- `service_name`, `ready`, `global_enable`, `error_code`
- `io_error_streak`, `io_degraded`, `degraded_reason`
- `sp1_state`, `sp2_state`, `sp1_connected`, `sp2_connected`
- `last_packet_age_ms`
- `effective_max_rpm_sp1`, `effective_max_rpm_sp2`
- `effective_max_accel_rpm_s`, `effective_max_torque`
- `ts`

Note: health-event dedup key includes effective limits and packet age fields, so limit changes are observable by subscribers.

### Logging codes
Base lifecycle:
- `SERVICE_START`
- `SERVICE_RUNNING`
- `SERVICE_STOP`
- `SERVICE_STOPPED`
- `SERVICE_ERROR`

Operational:
- `MAYAK_TX_ERROR`
- `MAYAK_RX_ERROR`
- `MAYAK_IO_DEGRADED`
- `MAYAK_IO_RECOVERED`
- `MAYAK_CMD_TIMEOUT`
- `MAYAK_SPINDLE_STATE`
- `MAYAK_READY_STATE`
- `MAYAK_METRICS`
- `MAYAK_OPERATOR_LIMITS`
- `MAYAK_HARD_LIMITS`

### Integration smoke
Real UDP + emulator smoke test:

```powershell
python -m pytest -q tests/test_mayak_spindle_smoke.py::test_mayak_spindle_with_real_emulator -s
```

This starts `majak_sim` subprocess, connects `MayakUdpTransport`, waits for spindle connectivity and verifies a movement command transition (`STARTING|MOVING`).
