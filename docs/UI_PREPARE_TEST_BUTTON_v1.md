# UI Prepare-Test Button (Draft v1)

Status: draft  
Date: 2026-03-05

## Scope

This document defines UI behavior for a new button in `l_functionalButtons`:

- Button label: `Подготовиться к тесту`

Code implementation is out of scope for this step.  
This is a behavioral contract for upcoming UI/orchestrator work.

## Goal

Before test start, operator triggers an explicit preparation action that:

- validates required inputs
- generates PlutoPlayer input artifact
- informs operator about GPS signal preparation
- shows visible progress during generation

## Placement

- Container: `l_functionalButtons`
- Order (recommended): after trajectory generation controls and before `Начать испытание`

## Click Flow

When operator clicks `Подготовиться к тесту`, UI executes:

1. Pre-warning dialog
2. Input checks
3. PlutoPlayer artifact generation
4. Result summary

## 1) Pre-warning Dialog (mandatory)

Before starting generation, show confirm dialog:

- Title: `Подготовка к тесту`
- Message:
  - GPS signal preparation will start now
  - this may take noticeable time
  - do you want to continue

Buttons:

- `Продолжить`
- `Отмена`

If canceled: stop flow, keep UI unchanged.

## 2) Input Checks (blocking)

Checks required for successful preparation:

- trajectory exists and is valid (generated artifact available)
- ephemerides path is set and file exists

If any check fails:

- show human-readable error list
- do not start generation
- keep `Начать испытание` disabled

## 3) PlutoPlayer Artifact Generation

UI requests orchestrator preflight generation (no direct subprocess from UI).

Expected output artifact (draft):

- `outputs/scenarios/<scenario_id>/pluto_input.json`

During generation:

- show progress bar in UI
- disable repeated click on `Подготовиться к тесту`
- allow cancel only if backend supports cancellation (optional in v1)

Progress model (v1 draft):

- indeterminate progress allowed initially
- determinate progress preferred when backend reports steps

## 4) Result Summary

On success:

- show success message
- display generated artifact path
- mark preparation as completed
- allow transition to monitoring/readiness stage

On failure:

- show error summary
- keep preparation status as failed
- keep `Начать испытание` disabled

## UI State Requirements

State flags (draft):

- `prep_in_progress: bool`
- `prep_done: bool`
- `prep_error: str | None`

Button policy:

- `Подготовиться к тесту` disabled while `prep_in_progress`
- `Начать испытание` enabled only after successful readiness/preparation policy

## Logging Requirements (UI)

Suggested UI log codes:

- `UI_PREPARE_CLICKED`
- `UI_PREPARE_CONFIRM_ACCEPTED`
- `UI_PREPARE_CONFIRM_CANCELLED`
- `UI_PREPARE_VALIDATE_FAIL`
- `UI_PREPARE_PROGRESS`
- `UI_PREPARE_DONE`
- `UI_PREPARE_FAILED`

All messages should follow `k=v` style where possible.

## Non-Goals

- No direct service calls from UI
- No UI-side subprocess execution
- No final visual design decisions in this document

## Acceptance Criteria (documentation phase)

- New button behavior is fully specified
- Blocking checks are explicitly listed
- Operator warning before GPS generation is required
- Progress bar behavior is defined
- Success/failure outcomes are defined
