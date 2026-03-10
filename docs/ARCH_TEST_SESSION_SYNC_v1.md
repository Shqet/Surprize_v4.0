# Test Session Sync v1 (No Mayak)

Status: draft  
Date: 2026-03-06

## Goal

Define behavior of the `Start test` / `Stop test` flow when Mayak is unavailable.
During a test session, the system must:

- record both video channels (`visible`, `thermal`)
- run GPS transmission (PlutoPlayer using prepared IQ)
- run trajectory animation in monitoring UI
- keep all three streams time-aligned as much as possible

## Scope

In scope:

- session lifecycle and state transitions
- runtime artifacts contract
- synchronization contract (clock model, timestamps, alignment rules)
- minimum failure policy

Out of scope (v1):

- hard real-time synchronization guarantees
- external hardware clock / PTP sync
- post-processing frame interpolation

## Session Lifecycle

### Preconditions for `Start test`

- readiness check passed (`ready_to_start=true`)
- prepared scenario exists
- GPS preflight artifacts exist (`nmea`, `iq`)

### `Start test` actions (ordered)

1. Create `session_id` and session directory.
2. Capture master start time:
   - `t0_unix` (wall clock, seconds)
   - `t0_monotonic` (monotonic clock for local delta)
3. Start video recording workers for:
   - `visible`
   - `thermal`
4. Start GPS TX worker (PlutoPlayer) with prepared IQ.
5. Start trajectory timeline ticker (UI/runtime), using the same session clock.
6. Write `SESSION_START` event.

### `Stop test` actions (ordered)

1. Stop trajectory timeline ticker.
2. Stop GPS TX worker.
3. Stop video recording workers.
4. Capture `t1_unix` and finalize manifest.
5. Write `SESSION_STOP` event.

## Runtime Artifacts Contract

All artifacts are stored under:

- `outputs/sessions/<session_id>/`

Required files:

- `session_manifest.json`
- `events.log`
- `trajectory_timeline.csv`
- `video/visible.mp4` (or container selected by recorder)
- `video/thermal.mp4`
- `video/visible_frames.csv`
- `video/thermal_frames.csv`
- `gps/pluto_stdout.log`
- `gps/pluto_stderr.log`
- `gps/plutoplayer.cmdline.txt`

## Time and Synchronization Contract

## Master clock

Session master time is:

- `t_rel = now_monotonic - t0_monotonic`

All runtime producers must emit timestamps with:

- `unix_ts` (absolute wall clock)
- `t_rel_sec` (relative to session start)

### Video stream alignment

For each recorded frame (or sampled checkpoint), store:

- `frame_index`
- `unix_ts`
- `t_rel_sec`

File:

- `video/<channel>_frames.csv`

### GPS TX alignment

GPS TX start/stop and major events must be logged with `t_rel_sec` in `events.log`.
If Pluto exits early, log event and mark degraded session status.

### Trajectory alignment

Trajectory animation uses session master time:

- animation cursor time = `t_rel_sec`
- trajectory sample = nearest or interpolated point at `t_rel_sec`

`trajectory_timeline.csv` must contain at least:

- `t_rel_sec,x_m,y_m,z_m,speed_mps`

## Replay Alignment Rules

Given playback cursor `t_rel_sec`:

1. Pick nearest frame from `visible_frames.csv`.
2. Pick nearest frame from `thermal_frames.csv`.
3. Pick nearest/interpolated trajectory sample from `trajectory_timeline.csv`.
4. Show all three in one synchronized UI state.

If one source has a gap, continue playback and show source as degraded, without stopping replay.

## Failure Policy (v1)

Blocking (test should not start):

- no prepared scenario
- no IQ artifact
- readiness failed

Non-blocking during active session (degraded mode):

- one camera disconnected during run
- temporary frame gaps

Blocking during active session (stop session with error):

- both cameras unavailable for prolonged period (threshold configurable later)
- GPS TX failed to start

## Logging (minimum)

Required event codes:

- `SESSION_START`
- `SESSION_STOP`
- `SESSION_DEGRADED`
- `SESSION_ERROR`
- `SESSION_VIDEO_FRAME`
- `SESSION_GPS_TX_START`
- `SESSION_GPS_TX_STOP`
- `SESSION_TRAJECTORY_TICK`

All log messages should include `session_id` and `t_rel_sec` when applicable.

## UI Behavior Contract

`Start test` button:

- disabled while no prepared scenario
- disabled while readiness not passed
- disabled during active session

`Stop test` button:

- enabled only during active session

Monitoring animation:

- starts with session start
- follows session master clock
- pauses/stops with session stop

### User Settings (persisted)

Runtime-related options are configured in `Настройки` and persisted between app sessions:

- `auto_stop_after_gps_sec`:
  - delay before automatic test stop after GPS TX process exits
  - default: `10.0 sec`
- `monitor_anim_without_test`:
  - allow trajectory animation without active test session
  - default: `true`
- `gps_nav_default_path`:
  - default ephemerides path for GPS SDR Sim input field
  - applied to UI on startup

The settings panel must provide:

- immediate apply for runtime-safe options
- `Reset to defaults` action restoring the defaults above

## Acceptance (v1)

1. Starting test creates a new session folder with manifest and logs.
2. Two video recordings and GPS TX start within one session lifecycle.
3. `trajectory_timeline.csv` is generated and indexed by `t_rel_sec`.
4. Replay can align visible+thermal+trajectory by one timeline cursor.
5. On partial channel failure, session continues in degraded mode with explicit logs.
