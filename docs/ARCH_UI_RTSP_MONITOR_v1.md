RTSP Monitor UI v1 — Architecture Spec
1) Цель

Добавить в UI отдельный логический слой “RTSP Monitor”, который:

отображает два потока (visible/thermal) и их состояние

показывает превью latest.jpg из rtsp_ingest

показывает метрики ingest (fps/age/restarts/state)

показывает health-статус (CONNECTED/RECONNECTING)

не управляет daemon-сервисами (нет start/stop/RTSP/сети)

не блокирует UI поток

2) UI Layout Contract (Designer)

Ты выделяешь отдельные layout’ы, UI код их находит по objectName.

Обязательные layout names:

vl_rtsp_visible — контейнер “visible stream panel”

vl_rtsp_thermal — контейнер “thermal stream panel”

Опционально (если хочешь общий блок):

vl_rtsp_monitor — общий контейнер, внутри которого уже две панели

Правило: если layout не найден → UI пишет UI_LAYOUT_NOT_FOUND layout=<name> и продолжает работу (без падения).

3) Состав панели канала (внутри каждого layout)

Каждая панель создаётся кодом (widgets), но размещается в твоём layout.

Внутри панели:

Заголовок: Visible / Thermal

Индикатор health: CONNECTED | RECONNECTING

Индикатор ingest state: INGESTING | RESTARTING | STALLED

Preview area: QLabel/QGraphicsView (показывает latest.jpg)

Метрики (в отдельном блоке):

fps_est

last_frame_age_sec

restarts

UI-toggle: “Показывать метрики” (QCheckBox) — локальный, позже заменится/синхронизируется со слоем настроек

4) Controller pattern

Создаём отдельный контроллер (аналогично траектории):

app/ui/rtsp_monitor/controller.py → RtspMonitorController

Главное правило:

MainWindow остаётся тонким (wiring)

вся логика RTSP UI живёт в контроллере

Responsibilities of RtspMonitorController

подписаться на:

RtspChannelHealthEvent

RtspIngestStatsEvent

LogEvent (только для получения out_dir/run_dir, если он публикуется через log k=v)

ServiceStatusEvent (если надо показывать общий статус демона rtsp_ingest)

хранить state по каналам

управлять асинхронной загрузкой latest.jpg

обновлять виджеты

5) Event Inputs and Data Sources
Health source

только RtspChannelHealthEvent

UI не делает выводов по логам/файлам

Ingest stats source

только RtspIngestStatsEvent

Snapshot file path source

Предпочтение (в порядке):

A) LogEvent k=v
Сервис rtsp_ingest логирует:

SERVICE_RUNNING service=rtsp_ingest out_dir=<...> run_id=<...>

UI парсит out_dir и строит путь:
<out_dir>/<channel>/latest.jpg или <out_dir>/<run_id>/<channel>/latest.jpg — зависит от того, что именно сервис публикует.

B) Stable convention
Если out_dir детерминирован и известен из профиля (out_root/rtsp_ingest/<run_id>), UI может брать run_dir из LogEvent и не гадать.

Важно: UI не должен сканировать outputs/ в поисках “последнего run_id”. Только то, что пришло по событию/логу.

6) UI State Model (in-memory)

В контроллере:

RtspMonitorState (dataclass):

channels: dict[str, ChannelState]

ChannelState:

health_state: CONNECTED|RECONNECTING|UNKNOWN

ingest_state: INGESTING|RESTARTING|STALLED|UNKNOWN

fps_est: float

last_frame_age_sec: float

restarts: int

last_snapshot_path: str|None

last_snapshot_mtime: float|None (для дебаунса)

snapshot_seq: int (anti-stale protection)

last_update_ts: float (для UI “серости”, если давно не было событий)

UiPrefs (временно локально):

show_metrics: bool (потом заменится на settings layer)

7) Snapshot loading (async + throttling)

UI должен ограничивать частоту чтения файлов, иначе будет спам диска.

Правило v1:

обновлять preview максимум 2 fps (или 1 fps) на канал

читать файл только если:

ingest_state == INGESTING (или если есть новый mtime)

путь известен

прошло >= 500ms с прошлого чтения

Механизм:

QTimer per channel (tick 500–1000 ms)

внутри tick: если условия выполняются → QRunnable читает файл (bytes) и возвращает QImage/QPixmap в UI thread

защита от stale: snapshot_seq увеличивается при смене run_dir/состояния; результат с устаревшим seq игнорируется

Логи UI:

UI_RTSP_SNAPSHOT_TICK channel=...

UI_RTSP_SNAPSHOT_LOAD channel=... path=...

UI_RTSP_SNAPSHOT_OK channel=... bytes=<N>

UI_RTSP_SNAPSHOT_FAIL channel=... error=...

(Тик логировать можно rate-limited или вообще не логировать, чтобы не шуметь.)

8) UX Rules

Если ingest_state RESTARTING:

preview не очищаем, но показываем overlay “Reconnecting…”

Если STALLED:

overlay “Stalled”

метрики показывают age

Если нет snapshot_path:

placeholder “No snapshot yet”

Переключатель “Показывать метрики”:

скрывает/показывает блок метрик без пересоздания панели

состояние хранится в памяти (позже подключим settings)

9) Acceptance / DoD RTSP Monitor UI v1

Готово, если:

UI стартует даже если layout’ов нет (только лог)

два layout’а корректно наполняются панелями

UI не блокируется при обновлении превью

отображаются состояния:

health (CONNECTED/RECONNECTING)

ingest (INGESTING/RESTARTING/STALLED)

preview обновляется асинхронно (без лагов)

есть toggle “Показывать метрики”

нет нарушений UI_GUIDE.md (нет subprocess, нет сети, нет прямых вызовов сервисов)

10) Что НЕ делаем в v1

кнопки управления daemon (start/stop)

автоскан outputs на “последний run”

сложные графики/историю кадров

настройку частоты обновления из settings layer (пока локально)