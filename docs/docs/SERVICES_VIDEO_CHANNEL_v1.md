# SERVICES_VIDEO_CHANNEL_v1.md
## Video Channel Service v1 (RTSP + Record + Placeholder)

Версия: v1  
Дата: 2026-02-13  
Назначение: Единый сервис работы с видеопотоком для одного канала (visible или thermal).
Сервис заменяет старые подходы вида rtsp_health + rtsp_ingest (несколько клиентов),
чтобы не “перебивать” RTSP-сервер.

---

# 1) Роль в архитектуре

- Сервис является **daemon**.
- Должен существовать **в двух экземплярах** (2 канала): `video_visible`, `video_thermal`.
- UI не обращается к сервису напрямую.
- Управление режимами (start_record/stop_record/apply_config) осуществляется через:
  - Orchestrator runtime overrides + restart, или
  - специализированные события-команды (если введём позже).
  В v1 допустим вариант “restart with updated config”, если он надёжен.

Сервис НЕ знает:
- про траекторию
- про t0
- про trial semantics

Сервис знает:
- rtsp url
- запись в файл
- placeholder при потере
- статусы/метрики

---

# 2) Обязательные функции сервиса (public API)

## 2.1 start(profile_section: dict) -> None
- валидирует конфиг (fail-fast)
- поднимает worker(ы)
- публикует:
  - SERVICE_STATUS STARTING → RUNNING
- идемпотентно

## 2.2 stop() -> None
- останавливает процессы/потоки
- закрывает запись (finalize)
- публикует:
  - SERVICE_STATUS STOPPING → STOPPED
- идемпотентно

## 2.3 status() -> str
- STARTING/RUNNING/STOPPING/STOPPED/ERROR

## 2.4 apply_config(new_section: dict) -> None (опционально v1)
Если apply_config реализуется:
- не блокирует UI
- обновляет url/параметры записи
- делает controlled restart внутреннего reader-процесса
Иначе в v1 допускается: смена конфигурации только через restart сервиса.

---

# 3) Конфигурация профиля (v1)

Пример (на один сервис-канал):

services:
  video_visible:
    role: daemon
    channel: visible
    url: "rtsp://<pi>:8554/visible"
    transport: "tcp"          # tcp|udp|auto
    connect_timeout_sec: 20
    read_watchdog_sec: 3

    preview:
      enabled: true
      latest_jpg_path: "outputs/preview/visible/latest.jpg"
      latest_write_period_ms: 200

    record:
      enabled_default: false  # в idle записи нет
      container: "mkv"        # mkv recommended
      record_fps: 15          # частота записи (не обязана = fps потока)
      width: 0                # 0 = как в потоке
      height: 0
      codec: "h264"           # или "copy" если пишем без декода (опционально)
      placeholder:
        enabled: true
        mode: "black"         # black
        max_gap_sec: 3600     # сколько максимум пишем плейсхолдеры без потока

    outputs:
      root: "outputs"
      channel_dir: "video"    # outputs/<channel_dir>/<channel>/...
      keep_last_n: 0          # 0 = не чистим

    debug:
      log_stderr_tail: true
      stderr_tail_lines: 200

---

# 4) Выходные файлы (v1)

В режиме trial запись должна идти в trial_dir.
Так как trial_dir создаёт TrialRunner, в v1 задаём запись через config:

record:
  enabled: true
  out_dir: "<trial_dir>/video"
  filename: "visible.mkv"

Аналогично thermal.

Дополнительно (рекомендуется):
- `video_timeline_<channel>.jsonl` или `.csv` рядом с видео,
  содержащий соответствие времени и плейсхолдеров.

---

# 5) События (EventBus)

## 5.1 Обязательные
- ServiceStatusEvent (STARTING/RUNNING/STOPPING/STOPPED/ERROR)
- LogEvent с k=v

## 5.2 Рекомендуемый новый event (v1)
### VideoChannelStatsEvent
Поля (пример):
- channel: "visible" | "thermal"
- state: "CONNECTED" | "CONNECTING" | "RECONNECTING"
- fps_in: float
- fps_record: float
- last_frame_age_sec: float
- placeholders_written: int
- recording: bool
- out_path: str | None

---

# 6) Поведение при потере соединения (ключевое)

Требование: запись в файл НЕ должна прерываться.

Если поток пропал:
- сервис остаётся RUNNING
- state → RECONNECTING
- запись продолжается:
  - пишем placeholder кадры (чёрный экран)
  - с частотой `record_fps`
- параллельно идёт reconnect с backoff (>=1 sec, cap 10 sec)

При восстановлении:
- запись продолжает писать реальные кадры
- в timeline (или event) фиксируем интервалы потери.

---

# 7) Привязка “кадр ↔ время” (без фиксированного FPS потока)

В v1 обязателен один из вариантов:

## Вариант A (предпочтительно)
Сервис ведёт `video_timeline_<channel>.jsonl`, где каждая запись:
- ts_wall (monotonic or wall clock)
- frame_idx_record
- placeholder 0/1

## Вариант B
Сервис публикует VideoChannelStatsEvent + отдельные события о смене режима:
- VIDEO_GAP_START ts=...
- VIDEO_GAP_END ts=...

TrialRunner складывает это в timeline.json.

---

# 8) Запрещено

- Открывать второй RTSP-клиент для health/ingest.
- UI → subprocess.
- Писать trial timeline в самом видео-сервисе.
- Блокировать start().

---

# 9) DoD v1 (Definition of Done)

Считается выполненным, если:

1) Сервис стабильно держит RTSP соединение (самовосстановление).
2) В idle режиме создаёт preview (latest.jpg) (если enabled).
3) По включению записи создаёт один файл и пишет его непрерывно.
4) При отключении RTSP:
   - файл не закрывается
   - пишутся placeholder кадры
   - после восстановления продолжаются реальные кадры
5) Идемпотентные start/stop.
6) Fail-fast:
   - неверный конфиг → ERROR
   - отсутствует backend/библиотека/кодек → ERROR
7) Unit tests без сети и без реального RTSP:
   - mock worker
   - проверка переходов state + placeholder logic
8) Логи содержат:
   - VIDEO_CONNECT / VIDEO_RECONNECTING / VIDEO_RECORD_START / VIDEO_RECORD_STOP
   - VIDEO_PLACEHOLDER_WRITTEN count=...
   - стабильный путь __file__ в старте (для отлова рассинхрона импорта)

---

# 10) Примечание по реализации (рекомендация)

Для Windows и OpenCV-нестабильности:
- reader делать в отдельном процессе (как в RtspClientService v1)
- запись делать внутри child (или через ffmpeg writer subprocess)
- parent watchdog по heartbeat и “no frames”
