# UI_GUIDE.md
## UI Architecture & Implementation Rules (Surprize_v3.0)

Version: v1  
Purpose: Зафиксировать правила разработки UI-слоя для корректной интеграции с Orchestrator и сервисами.

---

# 1. UI Responsibility Model

UI слой отвечает только за:

1. Отображение состояния системы
2. Сбор пользовательского ввода
3. Формирование run-intent (overrides)
4. Вызов Orchestrator.start() / stop()
5. Чтение артефактов (read-only) после публикации событий

UI НЕ является источником бизнес-логики.

---

# 2. Архитектурные запреты (Non-Negotiable)

UI строго запрещено:

- ❌ Запускать subprocess
- ❌ Делать сетевые запросы
- ❌ Работать с RTSP
- ❌ Вызывать ServiceManager напрямую
- ❌ Вызывать service.start() напрямую
- ❌ Писать конфигурационные файлы
- ❌ Хранить глобальное состояние сервисов
- ❌ Блокировать main thread

Все взаимодействие с backend происходит через:

- Orchestrator (управление)
- EventBus → UI Bridge (наблюдение)

---

# 3. Layout Philosophy

Каждый логический слой UI должен иметь:

- собственный layout (например: vl_rtsp_visible, vl_rtsp_thermal)
- собственный controller (Python-модуль)
- собственный локальный UI-state

Это позволяет:

- изменять форму в Qt Designer без изменения архитектуры
- подключать/отключать слой независимо
- изолировать баги

---

# 4. UI Controller Pattern

Для каждого UI-слоя:

app/ui/<feature>/controller.py

Controller:

- подписывается на нужные события
- хранит минимальный state (dataclass)
- обновляет виджеты
- НЕ содержит бизнес-логики сервисов

Пример:

RtspMonitorController:
- on_health_event(...)
- on_ingest_stats_event(...)
- update_view()

MainWindow должен оставаться thin wiring.

---

# 5. Event-Driven Model

UI обновляется только через события:

- ServiceStatusEvent
- RtspChannelHealthEvent
- RtspIngestStatsEvent
- OrchestratorStateEvent
- LogEvent

UI никогда не "спрашивает" сервис о состоянии напрямую.

---

# 6. Threading Rules

UI Main Thread:

- только виджеты
- только лёгкая логика

Тяжёлые операции:

- загрузка CSV
- чтение изображений
- анализ файлов

должны выполняться через:

- QRunnable
- QThreadPool

Запрещено:

- time.sleep() в UI
- блокирующие file I/O без worker-а

---

# 7. Snapshot / File Access Rules

UI может читать только:

- trajectory.csv
- latest.jpg
- другие output-артефакты

Но:

- только после соответствующих событий
- только read-only
- желательно асинхронно
- желательно с защитой от stale-load (run_seq)

---

# 8. Logging (UI Layer)

Все UI-логи:

- начинаются с UI_
- в формате k=v

Примеры:

UI_GENERATE_CLICKED
UI_RUN_REQUESTED service=ballistics_model
UI_VIS_LOAD_REQUEST run_dir=...
UI_RTSP_SNAPSHOT_LOAD channel=visible
UI_RTSP_SNAPSHOT_FAIL error=...

UI не логирует внутренности сервисов.

---

# 9. State Handling Rules

UI не делает выводы на основе догадок.

Например:

- состояние RTSP берётся только из RtspChannelHealthEvent
- ingest состояние — только из RtspIngestStatsEvent
- run завершён — только через OrchestratorStateEvent или ServiceStatusEvent

---

# 10. Acceptance Checklist (UI Changes)

Любое изменение UI принимается если:

- [ ] Нет прямого доступа к сервисам
- [ ] Нет subprocess
- [ ] Нет сетевой логики
- [ ] Нет блокировок main thread
- [ ] Используются события
- [ ] Логи соответствуют стандарту
- [ ] Layout из Designer используется корректно
- [ ] Controller изолирован

---

# 11. Design Rule

UI — это наблюдатель и диспетчер intent.

Сервисы — владельцы ресурсов.

Orchestrator — единственный управляющий центр.

Нарушение этих ролей запрещено.

---

# 12. Persisted UI Settings (v2)

Settings tab (`l_options`) stores user preferences between sessions via Qt settings storage:

- `auto_stop_after_gps_sec` (default `10.0`)
- `monitor_anim_without_test` (default `true`)
- `gps_nav_default_path` (default `data/ephemerides/brdc0430.25n`)

Rules:

- settings are applied on startup before user actions
- changing a setting updates runtime behavior immediately when safe
- "Reset to defaults" restores all keys to defaults and re-applies them
