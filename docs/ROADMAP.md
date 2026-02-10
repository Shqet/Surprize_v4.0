# ROADMAP

- EventBus
- Logger
- Orchestrator skeleton
- ExeRunnerService
- Minimal UI

v0 — CLOSED (см. docs/acceptance/ACCEPT_v0.md)

v1 — CLOSED (см. docs/acceptance/ACCEPT_v1.md)

v2 — CLOSED (см. docs/acceptance/ACCEPT_v2.md)

## v1 — Functional Hardening
ROADMAP v1 — Functional Hardening & Readiness
Context

Версия v0 архитектурно закрыта (см. docs/acceptance/ACCEPT_v0.md).
v0 доказала жизнеспособность каркаса (UI + Orchestrator + Services),
но содержит осознанные допущения, недопустимые для реальной эксплуатации.

Версия v1 направлена на устранение ключевых допущений v0
и подготовку системы к подключению реальных сервисов и устройств.

Goal v1

Сделать каркас надёжным и предсказуемым:

корректная синхронизация состояний

минимальная тестируемость ядра

готовность Orchestrator и Services к реальным нагрузкам

сохранение архитектурных границ v0 без расширения scope

In Scope (делаем в v1)
1) Orchestrator State Hardening

Синхронизация перехода STOPPING → IDLE
только после получения ServiceStatus(STOPPED) от всех сервисов

Явная обработка:

сервис завис

сервис завершился с ошибкой

Поведение задокументировано в ARCH.md / CONTRACTS.md

2) Minimal Unit Tests (Core Only)

Добавить минимальный набор тестов как код:

EventBus:

publish/subscribe

несколько подписчиков

Profiles loader:

валидный профиль

невалидный профиль (ошибка)

Orchestrator:

IDLE → RUNNING → STOPPING → IDLE

ERROR path

Цель тестов:

зафиксировать контракт

не тестировать UI

3) First Real Service (Production-like)

Подключить один реальный сервис:

либо реальный exe

либо реальное устройство

Сервис реализуется строго по BaseService

UI и Orchestrator не меняются

4) Error & Recovery Semantics

Формализовать:

что считается recoverable error

что переводит систему в ERROR

Документировать в CONTRACTS.md

Out of Scope (осознанно НЕ делаем)

Расширение UI (графики, настройки, сложные панели)

Многопрофильная работа

Автоматический restart сервисов

Distributed / agent-based архитектура

Полное покрытие тестами

Оптимизация производительности

Любые пункты из этого списка не принимаются в v1, даже если “почти готово”.

Key Changes vs v0

STOPPING становится синхронным по событиям сервисов

Появляются первые тесты как код

Каркас проверяется на реальном сервисе

Поведение при ошибках становится формализованным

Exit Criteria (DoD v1)

Версия v1 считается завершённой, если:

Orchestrator корректно ждёт ServiceStatus(STOPPED)

Минимальные unit tests проходят стабильно

Один реальный сервис успешно интегрирован

UI не блокируется ни в одном сценарии

Все изменения отражены в:

Developer Report v1

Architect Acceptance v1

Version Discipline

Любые изменения v1:

оформляются отдельными задачами

завершаются Developer Report

Закрытие v1:

только через ACCEPT_v1.md

Изменение scope:

требует обновления ROADMAP_v1.md

Final Note

ROADMAP v1 определяет границы следующей архитектурной фазы.
Нарушение scope без обновления ROADMAP считается архитектурным дефектом

v3 — UI: Input & Trajectory Visualization
Context

v2 закрыта: вычислительный сервис ballistics_model интегрирован, выдаёт артефакты (trajectory/diagnostics/plots), UI пока показывает только логи и статусы. 

ROADMAP

Goal v3

Замкнуть пользовательский цикл:

ввод начальных параметров

запуск расчёта через существующий оркестратор/сервис

отображение результата в UI (таблица/график/открытие артефактов)

In Scope (делаем в v3)

Ballistics UI Panel

форма ввода параметров (минимальный набор)

кнопки Run / Stop

отображение текущего run_id и out_dir

Result Viewer

загрузка и отображение trajectory.csv (таблица)

простой график траектории (например X–Z или X–Y)

кнопка “Открыть папку результата”

Glue без нарушения архитектуры

UI не запускает subprocess напрямую

UI не вызывает модель напрямую

UI работает через уже существующие механизмы (оркестратор/сервис/ивенты)

Out of Scope

очереди запусков/история runs

редактирование всех полей config_json “как в JSON”

сложные графики/аналитика/3D

расширение архитектуры и контрактов (если можно избежать)

Exit Criteria (DoD v3)

пользователь задаёт параметры в UI

выполняется расчёт (ballistics_model)

UI показывает результат (таблица + 1 график + ссылка на папку)

UI остаётся неблокирующимся

подготовлены Developer Report v3 и ACCEPT_v3.md