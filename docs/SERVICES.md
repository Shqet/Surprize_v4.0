# SERVICES REGISTRY

Name | Status | Description
-----|--------|------------
ExeRunnerService | planned | Run external exe
Service name: ballistics_model
Implementation: app/services/ballistics_model.py

Purpose

Запуск численной баллистической модели как внешнего вычислителя через subprocess. Модель изолирована в model_ballistics/ и управляется через конфиг vkr_config.json.

Inputs

Из профиля (YAML):

model_root: путь к папке модели (cwd для subprocess), например model_ballistics

python_exe: путь к python интерпретатору (по умолчанию python)

calc_entry: файл расчёта, по умолчанию run_vkr.py

plots_entry: файл визуализации, по умолчанию visualization.py (optional)

out_root: корневая папка для запусков, например outputs/ballistics

timeout_sec: таймаут расчёта (subprocess terminate/kill)

make_plots: bool (если true — после расчёта запускаем plots)

config_json: dict, который сериализуется 1:1 в vkr_config.json

Запуск расчёта: python run_vkr.py --config <run_dir>/vkr_config.json --out <run_dir>

Outputs

Файлы в run_dir:

trajectory.csv

diagnostics.csv

(optional) plots/*.png

Lifecycle

start():

публикует SERVICE_STATUS STARTING

создаёт run_id, run_dir

пишет vkr_config.json из config_json

запускает calc subprocess (stdout/stderr → PROCESS_STDOUT/ERR)

по завершении проверяет наличие csv

(optional) запускает plots subprocess

публикует SERVICE_STATUS STOPPED и лог “готово” с путями

stop():

terminate → wait(timeout) → kill

публикует STOPPED или ERROR (если не удалось корректно остановить)

Logging / Events

Строго по стандартам:

SERVICE_START / SERVICE_STOP / SERVICE_ERROR

PROCESS_START / PROCESS_EXIT / PROCESS_STDOUT / PROCESS_STDERR

SERVICE_STATUS (RUNNING/STOPPED/ERROR)
Результат минимум: LogEvent с code=SERVICE_STATUS и message=run_id=... out_dir=... trajectory=... diagnostics=... plots=....

Edge cases

отсутствует config_json → ERROR (не запускать subprocess)

subprocess exit!=0 → ERROR

нет trajectory.csv/diagnostics.csv после exit=0 → ERROR

stop во время расчёта → корректно завершает процесс и выдаёт STOPPED/ERROR

Service name: rtsp_health
Implementation: app/hw/video/service.py

Purpose
Monitor availability of RTSP mounts (/visible, /thermal)

Inputs (profile)
visible_url: str
thermal_url: str
probe_timeout_sec: int
reconnect_base_ms: int
reconnect_max_ms: int

Outputs (events)
ServiceStatusEvent
RtspChannelHealthEvent

Lifecycle
start(): STARTING → RUNNING; запускает 2 worker threads (visible/thermal)
stop(): останавливает threads; публикует STOPPED/ERROR


### RtspChannelHealthEvent (v1)

**Назначение:** публикация health-состояния RTSP-канала.  
**Источник:** `rtsp_health` service.  
**Потребители:** UI и любые наблюдатели (без управления сервисом напрямую).

**Обязательные поля:**
- `service: str` — всегда `"rtsp_health"`
- `channel: str` — идентификатор канала (например `"visible"`, `"thermal"`)
- `state: str` — одно из: `CONNECTED | RECONNECTING`
- `attempt: int` — номер попытки переподключения (0 при CONNECTED)
- `ts: float` — timestamp (unix seconds)

**Семантика v1:**
- `CONNECTED` — probe успешен (канал доступен сейчас)
- `RECONNECTING` — probe неуспешен, сервис выполняет backoff и будет повторять проверки
- `OFFLINE` в v1 **не используется** (зарезервировано на v2 при необходимости)

**Важно:**
- отсутствие сигнала/недоступность RTSP не означает `ServiceStatus=ERROR`
- fatal-ошибки среды (например, отсутствует `ffprobe`) переводят сервис в `ServiceStatus=ERROR`

## rtsp_ingest (v1)

**Purpose:** Поддерживать ingest RTSP-потока (через ffmpeg subprocess) и генерировать артефакты последнего кадра + телеметрию для UI.

### Config (profile section)
`services.rtsp_ingest`:

- `channels: dict[str, { url: str }]` — обязательный набор каналов
- `ffmpeg_path: str` — опционально (default `"ffmpeg"`)
- `out_root: str` — default `"outputs"`
- `snapshot_fps: float` — частота обновления `latest.jpg` (например 1–5)
- `probe_timeout_sec: int|float` — таймаут на операции/запуск (опционально)
- `restart_backoff: { base_ms: int, max_ms: int, jitter_ms: int }` — обязательный backoff
- `max_frame_age_sec: float` — порог “stalled” (опционально, recommended)

### Outputs / Artifacts
Для каждого запуска формируется run_dir, далее:
<out_root>/rtsp_ingest/<run_id>/<channel>/latest.jpg


Требования:
- `latest.jpg` обновляется атомарно (write temp → rename/replace)
- сервис логирует путь:
  - `out_dir=<...>` или `run_dir=<...>` (k=v)

### Events Emitted
- `ServiceStatusEvent(service="rtsp_ingest", status=...)`
- `RtspIngestStatsEvent(service="rtsp_ingest", channel=..., ...)`
- `LogEvent` (через emit_log)

### Logging (required codes, k=v)
- `SERVICE_START service=rtsp_ingest`
- `SERVICE_RUNNING service=rtsp_ingest`
- `SERVICE_STOP service=rtsp_ingest`
- `SERVICE_STOPPED service=rtsp_ingest`
- `SERVICE_ERROR service=rtsp_ingest error=<...>`

Per-channel:
- `INGEST_START channel=<name> url=<...> pid=<...>`
- `INGEST_EXIT channel=<name> rc=<int>`
- `INGEST_RESTART channel=<name> attempt=<int> delay_ms=<int>`
- `INGEST_STALLED channel=<name> age_sec=<float>` (если используется max_frame_age_sec)

### Lifecycle & Semantics
- Одна subprocess-ветка (ffmpeg) на канал.
- При проблемах сети/камеры сервис выполняет restart с backoff и остаётся RUNNING.
- Fatal errors:
  - ffmpeg отсутствует/не запускается
  - некорректная конфигурация
  → `ServiceStatus=ERROR` (fail-fast), без worker-ов.

### DoD (v1)
- start/stop идемпотентны
- UI не блокируется (всё вне UI thread)
- `latest.jpg` стабильно обновляется при наличии потока
- при падении/обрыве — backoff+restart
- публикуется `RtspIngestStatsEvent` с понятной семантикой
