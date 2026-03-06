# RUNBOOK

Status: active  
Date: 2026-03-06

## Quick Start (Windows)

1. Create virtualenv:
   - `py -3.11 -m venv .venv`
   - `.venv\Scripts\activate`
2. Install:
   - `python -m pip install --upgrade pip`
   - `pip install -e .[dev]`
3. Run app:
   - `python -m app.main`

## Test Commands

- Default:
  - `.\.venv\Scripts\python.exe -m pytest -q`
- Safe (ACL workaround):
  - `powershell -ExecutionPolicy Bypass -File .\tools\run_tests_safe.ps1 -q`

## Readiness / SDR Probe Artifacts

Probe cache location:

- `outputs/gps_sdr_sim/probe_cache/`

Key files:

- `probe_iq.bin`
- `probe_plutoplayer.cmdline.txt`
- `probe_pluto_stdout.log`
- `probe_pluto_stderr.log`

## Common Troubleshooting

### Readiness says SDR not ready

1. Check probe command:
   - `outputs/gps_sdr_sim/probe_cache/probe_plutoplayer.cmdline.txt`
2. Check probe logs:
   - `outputs/gps_sdr_sim/probe_cache/probe_pluto_stdout.log`
   - `outputs/gps_sdr_sim/probe_cache/probe_pluto_stderr.log`
3. Verify Pluto network:
   - reachable host `192.168.2.1`
   - no blocking VPN/firewall rules

### Temporary directory / pytest ACL errors

If pytest fails with Windows temp permission errors, run safe wrapper:

- `powershell -ExecutionPolicy Bypass -File .\tools\run_tests_safe.ps1 -q`

