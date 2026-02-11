# SERVICE_DEV_GUIDE.md
## How to Build a Service That Integrates Cleanly (Surprize_v3.0)

**Audience:** Developer Chat / Fixer Chat  
**Goal:** Define the minimal, strict contract for services so they integrate into the Orchestrator-driven desktop app without architectural drift.

---

## 0) Core Principles (non-negotiable)

1) **UI never talks to the service directly.**  
   UI interacts only with Orchestrator (start/stop) and observes events via UI Bridge.

2) **Services never call each other directly.**  
   All cross-cutting communication is via EventBus events (publish/subscribe), not function calls.

3) **A service owns its resource.**  
   If it manages a subprocess/device/socket/file-handle, that ownership stays inside the service.

4) **Start/Stop must be idempotent.**  
   Multiple start/stop calls must not crash or leak resources.

5) **No silent background global hacks.**  
   No monkey-patching, no global state mutation outside service boundaries.

---

## 1) Service Definition

A service is a self-contained component with:
- name (unique string id)
- config section (dict) provided by profile (or runtime overrides)
- lifecycle: `start(cfg)` / `stop()`
- publishes lifecycle state via `ServiceStatusEvent`
- publishes logs via `emit_log` → `LogEvent`
- (optionally) publishes output text via `ProcessOutputEvent` when running subprocess-like work

---

## 2) Mandatory Lifecycle Contract

### 2.1 States (minimum)
Service must publish **its own** status transitions (the ServiceManager does not “fake” final states):

- `STARTING`
- `RUNNING`
- `STOPPING`
- `STOPPED`
- `ERROR`

**Rule:** if `start()` succeeds, service must eventually publish `RUNNING`.  
When stopped (by itself or by stop request), service must publish `STOPPED` (or `ERROR`).

### 2.2 Idempotency rules
- `start()` when already RUNNING/STARTING:
  - do nothing or log and return safely
- `stop()` when already STOPPED/STOPPING:
  - do nothing or log and return safely

### 2.3 Threading rules
- `start()` must return quickly (do not block UI thread).
- Heavy work must run in:
  - worker thread, or
  - subprocess, or
  - async I/O loop (encapsulated inside service)

---

## 3) Configuration Contract

### 3.1 Input shape
Service reads its configuration from profile dict:

`profile_cfg[profile_name]["services"][service_name]`

Runtime overrides may overlay this dict **in-memory** via Orchestrator `start(..., overrides=...)`.

### 3.2 Validation
Service must fail-fast on invalid config:
- validate required fields
- validate types and basic ranges
- on validation error:
  - publish `ERROR`
  - log a clear `SERVICE_CONFIG_INVALID` with details
  - do not partially start resources

### 3.3 No UI config writes
Service is allowed to write **its own runtime artifacts** (outputs), but UI must not write service configs to disk.

---

## 4) Logging Standards for Services

All service logs must:
- use structured `k=v` fields
- include `service=<name>`
- have stable event codes

### 4.1 Required log events (minimum)
- `SERVICE_START service=<name>`
- `SERVICE_RUNNING service=<name>`
- `SERVICE_STOP service=<name>`
- `SERVICE_STOPPED service=<name>`
- `SERVICE_ERROR service=<name> error=<...>`

### 4.2 If service runs a subprocess (recommended pattern)
Emit:
- `PROCESS_START service=<name> stage=<calc|plots|...> pid=<...>`
- `PROCESS_STDOUT service=<name> line=<...>`
- `PROCESS_STDERR service=<name> line=<...>`
- `PROCESS_EXIT service=<name> rc=<int>`

And ensure:
- stdout/stderr streaming is line-based
- process termination sequence: terminate → wait(timeout) → kill
- publish `PROCESS_EXIT` exactly once per process run

---

## 5) Resource Ownership & Cleanup

### 5.1 Subprocess services
Service must:
- create subprocess inside service
- manage its lifetime
- kill it on stop/shutdown
- close pipes cleanly
- not leak threads

### 5.2 Hardware/device services
Service must:
- open/close device session entirely within service
- handle device disconnects (publish ERROR, attempt safe close)
- never block UI thread on I/O

---

## 6) EventBus Integration

### 6.1 Publish-only minimum
Every service must publish:
- `ServiceStatusEvent(service, status, meta?)`
- `LogEvent(code, message, kv...)`

### 6.2 Subscribe only if necessary
If a service subscribes to events:
- subscriptions must be created/removed in a controlled way (start/stop or init/deinit)
- callbacks must be thread-safe
- service must not assume event ordering beyond contracts

---

## 7) Outputs & Artifacts

If a service produces output files:
- output location must be controlled by config:
  - `out_root` + `run_id` (recommended)
- service should log:
  - `out_dir=<path>` and/or `run_id=<id>` in a structured log
- service should guarantee consistent naming where possible:
  - `outputs/<service>/<run_id>/...`

UI visualization must read outputs after `STOPPED`.

---

## 8) Minimal Tests (service-level)

Each new service should include at least:
1) **Config validation test** (invalid config → ERROR)
2) **Lifecycle test with fake backend**:
   - start() publishes RUNNING
   - stop() publishes STOPPED
3) If subprocess-based:
   - use a tiny harmless command (or a fake runner) for tests
   - do not depend on Qt in tests

Tests must be fast and deterministic.

---

## 9) Integration Checklist (for “Integration Phase”)

A service is “integration-ready” if:
- [ ] Service has unique name and config section defined
- [ ] start/stop idempotent
- [ ] publishes ServiceStatus transitions correctly
- [ ] logs follow k=v and stable codes
- [ ] outputs are under out_root/run_id (if applicable)
- [ ] stop/shutdown works mid-run without UI freeze
- [ ] minimal tests exist
- [ ] no direct UI/service coupling
- [ ] no service→service direct calls

---

## 10) Developer Report Template (for service delivery)

Service developer must deliver:
- Scope & version
- What implemented
- Config keys used (with example)
- Log sample for: start → running → stop
- Output artifacts (if any) and where they are stored
- Tests run (`pytest -q`) and results
- Known limitations

---

## Appendix A — Recommended Config Section Skeleton

```yaml
services:
  <service_name>:
    enabled: true
    out_root: "outputs"
    timeout_sec: 120
    # service-specific keys...
Appendix B — Recommended Service Output Layout
php-template
Копировать код
outputs/
  <service_name>/
    <run_id>/
      run.json
      ...