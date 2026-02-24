Developer Report → Architect Review

Meta

Version / Scope: MayakSpindleService v1 (emulator-first, transport-injected)
Related Docs:
- SERVICE_TEMPLATE_v4.md
- SERVICES.md (registry)

Status

Кратко:
Добавлен новый сервис mayak_spindle: управление 2 шпинделями (через D-ячейки) + публикация телеметрии.
Сервис тестируется без сети через DictTransport (эмулятор памяти).

What Was Implemented

Core:
- MayakSpindleService (start/stop/status; worker loop; fail-fast config validation)
- Transport abstraction MayakTransport + in-memory DictTransport for unit tests (no network)

Services:
- mayak_spindle (new)

Events:
- MayakSpindleTelemetryEvent (per-spindle snapshot)
- MayakSpindleCommandEvent (emit-only; для UI/debug)

Profiles:
- services.mayak_spindle:
  - d_map (обязательный): ключи в стиле emulator.py D_MAP
  - publish_period_ms (default 50)
  - global_enable (optional)

Logs (fact):
- SERVICE_START service=mayak_spindle
- SERVICE_RUNNING service=mayak_spindle period_ms=<.>
- SERVICE_STOP service=mayak_spindle
- SERVICE_STOPPED service=mayak_spindle
- SERVICE_ERROR service=mayak_spindle error=<.>
- MAYAK_TX_ERROR / MAYAK_RX_ERROR (non-fatal, retry next tick)

Verification (fact):
- pytest: tests/test_mayak_spindle_service.py
  - start() → RUNNING
  - stop() → STOPPED
  - idempotent start/stop
  - fail-fast → ERROR (missing d_map)
  - command write-through to D cells via DictTransport

Architectural Compliance Checklist:
[x] No UI imports
[x] No service-to-service calls
[x] Idempotent start/stop
[x] Proper logging (k=v via LogEvent)
[x] Backoff safe (no restart storm; simple periodic loop)
[x] Tests green (no Qt / no network / no orchestrator)
