# Orchestrator 3-Stage Flow (Draft v1)

Status: draft  
Date: 2026-03-05

## Purpose

Define target operator flow before implementation changes:

1. Preparation stage
2. Monitoring and readiness stage
3. Test run stage

This document also defines temporary Mayak stub behavior and captures future constraints for real Mayak integration.

## Stage 1: Preparation

Operator prepares all test inputs in a dedicated preparation UI.

Required outputs of stage 1:

- trajectory generated and selected
- ephemerides file selected
- start coordinates selected
- Mayak configuration prepared (temporary schema allowed)
- scenario package persisted to disk

`scenario package` is the single source of truth for the next stages.

Suggested artifact:

- `outputs/scenarios/<scenario_id>/scenario_package.json`

## Stage 2: Monitoring and Readiness

Preparation data is loaded into monitoring UI as read-only operational context.

Primary action:

- `Check readiness`

Readiness checks:

- validate scenario package schema and required fields
- validate referenced files (trajectory, nav, intermediate files)
- generate PlutoPlayer input file from prepared data
- check SDR readiness
- check Mayak readiness
- check camera readiness

Policy:

- SDR not ready: blocking
- Mayak not ready: blocking
- cameras not ready: non-blocking warning

When all blocking checks pass:

- enable `Start test` button

## Stage 3: Test Run

Before actual start:

- run short recheck of all blocking readiness checks
- fail fast if any blocking dependency changed to not ready

Then:

- start scenario execution
- emit scenario timeline events and log codes
- keep monitoring view as operational console

## Target Orchestrator Model

Current orchestrator state model is not enough for this flow.  
Target model should introduce explicit phase boundaries.

Proposed states:

- `IDLE`
- `PREPARING`
- `PREPARED`
- `READINESS_CHECK`
- `READY`
- `STARTING_TEST`
- `TEST_RUNNING`
- `STOPPING`
- `ERROR`

Proposed public commands:

- `prepare_scenario(...)`
- `load_prepared_scenario(scenario_id)`
- `check_readiness()`
- `start_test()`
- `stop_test()`
- `emergency_stop()`

## Temporary Mayak Stub

Until real Mayak implementation is finalized, orchestrator should not depend on transport details.

Introduce adapter contract:

- `MayakController` interface (or protocol)
- implementation `MayakStubController`

`MayakStubController` behavior:

- deterministic `is_ready()` result from config
- command methods are no-op with structured logs
- exposes capability flags (what is simulated vs not implemented)

Stub mode must be explicit in profile:

- `services.mayak.mode: stub|real`

## Future Real Mayak Notes

Open design assumptions for future implementation:

- control transport may be G-code based
- tail spindle speed law must be customizable
- head spindle torque law must be customizable

Architecture requirement:

- custom laws must be isolated as data/config or plug-in modules
- orchestrator consumes normalized command API only
- no G-code generation logic in UI

## Non-Goals for This Step

- no immediate orchestrator code refactor in this document-only phase
- no lock-in to specific G-code dialect yet
- no final custom-law runtime engine decision yet

## Acceptance Criteria for Documentation Phase

- 3-stage flow is documented end-to-end
- blocking vs warning readiness policy is documented
- temporary Mayak stub approach is documented
- future G-code and custom-law constraints are captured
