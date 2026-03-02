# Acceptance - Scenario Iteration v1

- Scope: Mayak scenario flow + timeline/log + acceptance artifacts
- Date: 2026-03-02
- Status: READY FOR EXECUTION

## Target e2e flow

1. `daemon up`
2. `mayak ready`
3. `start test`
4. `telemetry changes`
5. `stop test`
6. `trajectory run`
7. `idle`

## Evidence sources

- `data/app.log`
- `outputs/acceptance/<scenario_id>/timeline.jsonl`
- `outputs/acceptance/<scenario_id>/summary.json`
- `outputs/acceptance/<scenario_id>/checklist.md`
- `outputs/acceptance/<scenario_id>/trajectory/trajectory.csv`
- `outputs/acceptance/<scenario_id>/trajectory/diagnostics.csv`
- `outputs/acceptance/<scenario_id>/video_preview/visible/latest.jpg`
- `outputs/acceptance/<scenario_id>/video_preview/thermal/latest.jpg`

## Timeline contract (must exist in log)

- `SCENARIO_ID`
- `MAYAK_TEST_START`
- `MAYAK_TEST_STOP` or `MAYAK_TEST_ABORT`
- key statuses:
  - `SCENARIO_STATUS`
  - `SERVICE_STATUS`
  - `ORCH_STATE_CHANGE`

## Run steps

1. Start emulator and app (`python -m app.main`).
2. Run target scenario in UI:
   - start daemons
   - run Mayak test start/stop
   - run trajectory generation
3. Collect acceptance artifacts:

```powershell
.\.venv\Scripts\python.exe -m tools.scenario_acceptance_collect --copy-full-log
```

4. Check result:
   - script exit code `0` -> PASS
   - script exit code `2` -> FAIL (inspect checklist/timeline)

## PASS/FAIL checklist (formal)

- [ ] daemon up
- [ ] mayak ready
- [ ] start test
- [ ] telemetry changes
- [ ] stop test
- [ ] trajectory run
- [ ] idle

Final decision:
- [ ] PASS
- [ ] FAIL

Owner notes:
- scenario_id:
- branch:
- commit:
- comments:

