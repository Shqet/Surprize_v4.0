# UI Prepare-Test Button (v1, current behavior)

Status: active  
Date: 2026-03-06

## Scope

This document defines current behavior for button:

- `Подготовиться к тесту` (`btn_prepare_test`)

## Goal

Run preparation stage only:

- validate operator inputs
- create prepared scenario snapshot
- generate GPS preflight artifacts (NMEA + IQ)

Important:

- this action does **not** run SDR readiness probe
- this action does **not** run Pluto transmission probe

SDR readiness probe is executed only by:

- `Проверить готовность систем` (`btn_check_readiness_m`)

## Click Flow

When operator clicks `Подготовиться к тесту`, UI executes:

1. confirmation dialog
2. input validation (trajectory + ephemerides file)
3. orchestrator preparation:
   - `prepare_mayak_test(...)`
   - `generate_gps_signal_preflight(...)`
4. success/fail summary dialog

Execution runs in background task and reports progress to UI.

## Input Checks (blocking)

- trajectory for current session exists
- ephemerides path is set
- ephemerides file exists

If checks fail:

- show readable error list
- do not start preparation

## Output Artifacts

Primary artifacts are created under scenario folder:

- `outputs/scenarios/<scenario_id>/gps_preflight/nmea_strings.txt`
- `outputs/scenarios/<scenario_id>/gps_preflight/gpssim_iq.bin`
- `outputs/scenarios/<scenario_id>/gps_preflight/gps_preflight_meta.json`

## UI Progress and State

While running:

- `btn_prepare_test` is disabled
- progress bar is visible

On success:

- success dialog shows `scenario_id` and IQ path
- monitoring tab becomes active
- trajectory monitoring animation starts

On failure:

- error dialog with exception summary
- progress resets

## Separation of Responsibilities

- UI: collect inputs, show progress/result
- Orchestrator: perform preparation and artifact generation
- UI does not run subprocesses directly

## Related Buttons

- `Проверить готовность систем`:
  - executes `check_readiness()`
  - includes blocking SDR probe
- `Начать испытание`:
  - executes readiness recheck before start

