# CONTRACTS

## BaseService

Methods:
- start()
- stop()
- status()

---

## ServiceStatus

IDLE
STARTING
RUNNING
ERROR
STOPPED
STOPPING

---

## Orchestrator States

IDLE
PRECHECK
RUNNING
STOPPING
ERROR

---

## Orchestrator Phase API (current)

Runtime phase enum (UI/monitoring flow):

- PREPARING
- PREPARED
- MONITORING
- READY
- TEST_RUNNING
- PHASE_ERROR

---

## Readiness Report Contract (current)

`check_readiness() -> dict[str, Any]`

Required fields:

- `ready_to_start: bool`
- `blocking_errors: list[str]`
- `warnings: list[str]`
- `artifacts: dict[str, str]`

Typical blocking keys:

- `trajectory_missing`
- `gps_nav_missing`
- `mayak_not_ready`
- `sdr_not_ready`
- `pluto_input_failed:<ErrType>`

Typical warning keys:

- `video_visible_not_ready`
- `video_thermal_not_ready`
- `sdr_probe:<detail>`

---

## Test Session API (skeleton, current)

`start_test_session() -> dict[str, str]`

- precondition: prepared scenario exists
- creates `outputs/sessions/<session_id>/`
- writes:
  - `session_manifest.json`
  - `events.log` with `SESSION_START`

`stop_test_session() -> dict[str, str]`

- precondition: active test session exists
- appends `SESSION_STOP` to `events.log`
- finalizes `session_manifest.json` (`status=STOPPED`, `t1_unix`, `duration_sec`)

Session id format:

- `sess_<epoch_ms>_<seq>`

Runtime Profile Overrides (v3)
Orchestrator поддерживает передачу runtime-overrides при запуске:

start(profile_name: str, overrides: dict | None = None)


Overrides применяются:

только in-memory

поверх загруженного профиля

через безопасный deep-merge

без записи на диск

UI и другие клиенты не имеют права модифицировать YAML-профили напрямую.

---

## Event Types

LogEvent:
  level
  source
  code
  message

ServiceStatusEvent:
  service_name
  status

OrchestratorStateEvent:
  state

RtspChannelHealthEvent:
  service_name: str
  channel: "visible" | "thermal"
  url: str
  state: "CONNECTED" | "RECONNECTING"
  attempt: int
  last_error: str | None

### RtspChannelHealthEvent (v1)

**Назначение:** публикация health-состояния RTSP-канала.  
**Источник:** `rtsp_health` service.  
**Потребители:** UI и любые наблюдатели (без управления сервисом напрямую).

**Обязательные поля:**
- `service_name: str` — всегда `"rtsp_health"`
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
---
### RtspIngestStatsEvent (v1)

**Назначение:** телеметрия ingest-потока (декодирование/получение кадров) по RTSP-каналу.  
**Источник:** `rtsp_ingest` service.  
**Потребители:** UI/мониторинг (read-only), логика принятия решений (опционально).

**Обязательные поля:**
- `service: str` — всегда `"rtsp_ingest"`
- `channel: str` — идентификатор канала (например `"visible"`, `"thermal"`)
- `state: str` — одно из: `INGESTING | RESTARTING | STALLED`
- `fps_est: float` — оценка частоты обновления `latest.jpg` (0 если кадров нет)
- `last_frame_age_sec: float` — возраст последнего кадра в секундах (∞/large если нет)
- `restarts: int` — количество рестартов ffmpeg с момента старта сервиса
- `ts: float` — timestamp (unix seconds)

**Семантика v1:**
- `INGESTING` — кадры поступают (last_frame_age_sec <= max_frame_age_sec)
- `RESTARTING` — ffmpeg перезапускается / backoff-ожидание перед рестартом
- `STALLED` — ffmpeg жив, но кадры не обновляются дольше порога `max_frame_age_sec` (если задан)

**Важно:**
- Событие носит мониторинговый характер; UI не управляет сервисом через него.
- Отсутствие сети/камеры не обязательно переводит сервис в `ERROR` (ожидается reconnect/backoff).
- Fatal errors (например, отсутствует ffmpeg или неверная конфигурация) переводят сервис в `ServiceStatus=ERROR`.



## Profiles

YAML/JSON

profile_name:
  services:
    exe_runner:
      path: "tool.exe"
      args: "--test"

---

# Bootstrap Contracts v0

## Enums

### OrchestratorState
- IDLE
- PRECHECK
- RUNNING
- STOPPING
- ERROR

### ServiceStatus
- IDLE
- STARTING
- RUNNING
- STOPPED
- ERROR

---

## BaseService Interface

Methods:
- name: str
- start() -> None
- stop() -> None
- status() -> ServiceStatus

Rules:
- start() и stop() идемпотентны
- ошибки публикуются как LogEvent + ServiceStatusEvent(ERROR)

---

## Event Types

### LogEvent
- level: str
- source: str
- code: str
- message: str

### ServiceStatusEvent
- service_name: str
- status: ServiceStatus

### OrchestratorStateEvent
- state: OrchestratorState

### ProcessOutputEvent
- service_name: str
- stream: "stdout" | "stderr"
- line: str

---

## Orchestrator Public API

- start(profile_name: str) -> None
- stop() -> None
- state: OrchestratorState (property)

---

## Profiles v0

YAML

profile_name:
  services:
    exe_runner:
      path: str
      args: str
      timeout_sec: int


---

# Logging Standards v0

## Log Levels

DEBUG  
INFO  
WARNING  
ERROR  

---

## LogEvent Contract

LogEvent:
- level: LogLevel
- source: str        # имя компонента или сервиса
- code: str          # короткий машинный код события
- message: str       # человекочитаемый текст

---

## Standard Log Codes

### System

SYSTEM_START  
SYSTEM_STOP  

---

### Orchestrator

ORCH_START_REQUEST  
ORCH_STOP_REQUEST  
ORCH_STATE_CHANGE  

---

### Services

SERVICE_REGISTER  
SERVICE_START  
SERVICE_STOP  
SERVICE_STATUS  
SERVICE_ERROR  

---

### Process / Exe

PROCESS_START  
PROCESS_EXIT  
PROCESS_STDOUT  
PROCESS_STDERR  

---

### UI

UI_GENERATE_CLICKED  
UI_RUN_REQUESTED  
UI_RUN_ALREADY_RUNNING  
UI_RUN_START_FAILED  
UI_CONFIG_INVALID  
UI_RUN_FINISHED  
UI_VIS_LOAD_REQUEST  
UI_VIS_LOAD_OK  
UI_VIS_LOAD_FAIL  
UI_VIS_RENDER_OK  

---

## Log Message Rules

- message — короткая фраза без лишних слов  
- важные параметры выносятся в message как key=value  

Пример:

level=INFO  
source=ExeRunnerService  
code=PROCESS_START  
message=path=cmd args="/c ping 127.0.0.1 -n 5"

---

## Mandatory Logging Points

Каждый сервис обязан:

- логировать start  
- логировать stop  
- логировать ошибки  

ExeRunnerService дополнительно:

- PROCESS_START  
- PROCESS_EXIT  
- PROCESS_STDOUT  
- PROCESS_STDERR  

---

# Profiles v0

## Profile File Format

YAML

---

## Root Structure

profile_name:
  services:
    <service_name>:
      <param>: <value>

---

## Minimal Example

default:
  services:
    exe_runner:
      path: "cmd"
      args: "/c ping 127.0.0.1 -n 5"
      timeout_sec: 10

---

## Rules

- Все сервисы читают параметры только из профиля
- Захардкоженные пути запрещены
- Отсутствующий параметр → ошибка старта сервиса

Contracts v1 — STOPPING Synchronization & Error Semantics
Orchestrator STOP Semantics (v1)
Stop completion rule

Переход STOPPING → IDLE разрешён только если выполнено одно из условий:

Получен ServiceStatus=STOPPED от всех зарегистрированных сервисов

Произошёл stop-timeout — тогда Orchestrator переводит систему в ERROR

Stop timeout

stop_timeout_sec — общий таймаут на фазу STOPPING

При истечении таймаута:

публикуется лог ERROR с code=ORCH_STATE_CHANGE или SERVICE_ERROR (см. ниже)

Orchestrator переводится в ERROR

дальнейшее восстановление/повтор — только новой командой (вне scope v1)

Services STOP Contract (v1)
Mandatory status emission

Каждый сервис обязан публиковать ServiceStatusEvent (через EventBus):

при старте: STARTING → RUNNING (или ERROR)

при остановке: STOPPED (или ERROR)

STOP guarantee

При вызове stop() сервис обязан в конечном итоге опубликовать:

ServiceStatus=STOPPED при нормальной остановке, или

ServiceStatus=ERROR если корректная остановка невозможна

Тихая остановка без STOPPED/ERROR запрещена (в v1 это считается дефектом сервиса)

Idempotency

Повторный stop() не должен ломать сервис

Повторный start() в RUNNING запрещён без предварительного STOPPED (сервис должен логировать ошибку и/или игнорировать)

Logging Requirements (v1 additions)
Orchestrator stop wait logs

Во время STOPPING Orchestrator обязан логировать прогресс ожидания через k=v:

code=ORCH_STATE_CHANGE
message=from=RUNNING to=STOPPING

code=SERVICE_STATUS (INFO)
message=service=<name> status=<status>

При таймауте:

level=ERROR

code=SERVICE_ERROR или ORCH_STATE_CHANGE

message=phase=STOPPING timeout_sec=<N> pending=<svc1,svc2,...>

(Список pending допустимо писать как строку)

Profiles v1
Root Structure (unchanged)

profile_name:
orchestrator:
stop_timeout_sec: int
services:
<service_name>:
<param>: <value>

Minimal Example (v1)

default:
orchestrator:
stop_timeout_sec: 10
services:
exe_runner:
path: "cmd"
args: "/c ping 127.0.0.1 -n 5"
timeout_sec: 10

Rules

Если orchestrator.stop_timeout_sec не задан:

использовать дефолт 10 секунд (v1)

и логировать WARNING, что параметр не задан

Все сервисы по-прежнему читают параметры только из профиля


✅ Зафиксированный UI-контракт (v3 / Step 1)

В .ui файле гарантированно существуют следующие layout’ы:

gl_trajectory_params
Назначение: контейнер для редактора config_json и кнопки запуска расчёта

vl_trajectory_visualization
Назначение: контейнер для виджета визуализации траектории (3D)

Это публичный UI API. Их имена не меняются без отдельного архитектурного решения.

UI-лейаут:

создан в Qt Designer

содержит только геометрию

не содержит логики

наполнение — строго через код

## Service Roles (v4)

Каждый сервис может иметь поле:

services.<name>.role: "job" | "daemon"

markdown
Копировать код

### Default

Если role отсутствует → "job".

### Semantics

job:
- участвует в run-cycle
- его STOPPED завершает run
- его ERROR переводит Orchestrator в ERROR

daemon:
- может работать постоянно
- не влияет на RUNNING/IDLE
- его ERROR не завершает run-cycle

### Stop Behavior

stop():
- останавливает только job

shutdown:
- останавливает все сервисы


📄 CONTRACTS — VideoChannelDaemonService v1
1. Назначение

VideoChannelDaemonService — daemon-сервис Surprize, инкапсулирующий процессный RTSP-клиент (vendor) и обеспечивающий:

непрерывное подключение к RTSP (self-healing),

управление записью видео (start/stop),

публикацию preview (latest.jpg),

публикацию событий и логов через EventBus,

строгую интеграцию в lifecycle Orchestrator v4.

Сервис не содержит UI-логики и не нарушает архитектурные границы.

2. Lifecycle контракт
2.1 Статусы

Единственный источник истины для статусов —
ServiceStatus из:

app/services/base.py


Допустимые значения:

STARTING

RUNNING

STOPPING

STOPPED

ERROR

2.2 Публикация статусов

Сервис обязан публиковать:

ServiceStatusEvent(
    service=<name>,
    status=ServiceStatus.<X>.value  # строка
)


ServiceStatusEvent.status — строка.

Дополнительных enum в events слое не допускается.

3. Fail-Fast контракт
3.1 start(config)

start() не выбрасывает исключения наружу.

При ошибке конфигурации или зависимостей:

сервис публикует ServiceStatus.ERROR

публикует LogEvent:

SERVICE_ERROR service=<name> error=<text>


воркеры не создаются

Исключения внутри метода ловятся и преобразуются в ERROR.

4. stop() контракт

При вызове stop() если сервис активен:

публикуется ServiceStatus.STOPPING

выполняется корректное завершение воркеров/процессов

публикуется ServiceStatus.STOPPED

STOPPING является обязательным промежуточным статусом.

5. Логирование контракт

Сервис обязан публиковать LogEvent через EventBus.

Используется k=v формат.

Минимально обязательные коды:

VIDEO_START
VIDEO_STOP
VIDEO_RECORD_START
VIDEO_RECORD_STOP
VIDEO_PLACEHOLDER on=1/0
VIDEO_ERROR
SERVICE_ERROR


Запись в стандартный logging допустима,
но EventBus-лог обязателен.

6. Worker контракт (vendor integration)
6.1 Factory

По умолчанию:

self._worker_factory = ProcessStreamWorker


Worker создаётся через kwargs:

_worker_factory(
    stream=...,
    url=...,
    log=...,
    heartbeat_seconds=...,
    preview_width=...,
    preview_height=...
)


Тесты могут патчить _worker_factory.

6.2 Контракт стабильности

Набор kwargs считается стабильным API v1.

ProcessWorkerOptions dataclass не используется в v1.

Расширение допускается только backward-compatible.

7. Preview контракт
7.1 Ответственность

Preview (latest.jpg) — ответственность daemon-сервиса, но фактическая запись выполняется child-процессом.

Vendor child:

не пишет preview самостоятельно,

получает IPC команду SAVE_PREVIEW и выполняет atomic replace (tmp → latest.jpg).

Daemon:

запускает preview loop (thread),

период 200–500 ms,

вызывает IPC SAVE_PREVIEW.

7.2 Пути

Preview хранится в:

outputs/video_preview/<channel>/latest.jpg


Путь определяется конфигурацией Surprize,
vendor не знает о структуре outputs.

8. Recording контракт

Сервис обязан поддерживать:

start_record(...)
stop_record()


Recording:

не прерывается при reconnect,

пишет mkv,

пишет timeline.jsonl,

поддерживает placeholder при потере сигнала.

9. EventBus ограничения

Сервис:

не вызывает UI напрямую

не вызывает другие сервисы напрямую

публикует события только через EventBus

10. Архитектурные ограничения

UI не вызывает subprocess

UI не управляет потоками напрямую

vendor не знает о Surprize

Orchestrator управляет lifecycle

сервис идемпотентен по start/stop

11. Версия

Contract version: VideoChannelDaemonService v1
Совместим с Orchestrator v4 (roles: daemon)
