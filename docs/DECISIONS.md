# DECISIONS

Формат:

## YYYY-MM-DD Title
Decision:
Why:
Alternatives:
Consequences:

## 2026-02-05 Adopt Bootstrap Architecture v0

Decision:
Принят каркас: UI Shell + Orchestrator + ServiceManager + Services + EventBus.

Why:
Нужно устойчивое основание для расширяемой системы.

Alternatives:
Монолитное приложение с прямыми вызовами из UI.

Consequences:
Все новые функции реализуются как сервисы.
2026-02-05 — Adopt v1 STOPPING Synchronization Semantics

Decision:
Принята архитектурная семантика v1 для остановки системы (STOPPING):

Переход STOPPING → IDLE разрешён только после получения ServiceStatus=STOPPED от всех сервисов

Введён общий stop_timeout_sec на фазу STOPPING

При истечении stop-timeout система переводится в ERROR

Why:
В v0 остановка считалась завершённой сразу после вызова stop_all(),
что неприемлемо для реальных сервисов и устройств.

Для обеспечения:

предсказуемости поведения,

наблюдаемости состояния,

готовности к интеграции железа,

остановка должна подтверждаться фактическим состоянием сервисов.

Alternatives Considered:

Немедленный переход в IDLE (v0 поведение)
→ отвергнуто: скрывает зависшие/некорректно остановленные сервисы

Переход в IDLE с флагом деградации
→ отвергнуто в v1 для упрощения семантики

Автоматический restart сервисов
→ вынесено за scope v1

Consequences:

Orchestrator обязан отслеживать статусы всех сервисов во время STOPPING

Каждый сервис обязан публиковать ServiceStatus=STOPPED или ERROR

Таймаут остановки становится архитектурно значимым параметром

Ошибки остановки становятся наблюдаемыми и не могут быть “проглочены”

Scope:

Применяется начиная с версии v1

Не влияет на v0 (v0 закрыта через ACCEPT_v0.md)

## 2026-02-11 — Adopt Service Roles (job / daemon)

### Context

Система начала включать постоянные сервисы
(rtsp_health, rtsp_ingest, hardware control),
которые должны работать независимо от run-cycle вычислительных задач.

В Orchestrator v3 RUNNING отражал активность любых сервисов,
что приводило к "вечному RUNNING" при наличии daemon.

### Decision

Ввести роли сервисов:

- job — участвует в run-cycle
- daemon — долгоживущий сервис

Orchestrator RUNNING теперь отражает только job run-cycle.

### Consequences

+ Поддержка постоянных сервисов
+ UI корректно отображает состояние
+ Масштабируемость системы

- Появляется необходимость фиксировать role в профилях