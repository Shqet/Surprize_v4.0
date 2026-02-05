Architect Acceptance → Version Closure
Meta

Accepted Version / Scope:
v1 — Functional Hardening (STOPPING synchronization + minimal tests)

Date:
2026-02-05

Architect:
Architect Chat

Based on:

Developer Report v1 (2026-02-05)

ARCH.md — Architecture Version v0, v1 (Functional Hardening)

CONTRACTS.md — Bootstrap Contracts v0; Contracts v1 (STOPPING Synchronization & Error Semantics)

DECISIONS.md — 2026-02-05 Adopt v1 STOPPING Synchronization Semantics

Acceptance Decision

Status: ACCEPTED

Версия v1 принята.
Усиление каркаса выполнено без изменения архитектуры и UI, семантика STOPPING реализована строго по контракту v1, Definition of Done v1 закрыт.

Architectural Compliance

Подтверждаю:

Архитектура соответствует ARCH.md (v0 сохранена, v1 добавлена)

Контракты CONTRACTS.md (v1) соблюдены

Архитектурные запреты не нарушены:

UI не вызывает subprocess

UI не трогает устройства

отсутствуют прямые вызовы service → service

EventBus остаётся единственным каналом коммуникации

Доставка событий в UI осуществляется только через Qt bridge

Scope версии v1 не превышен

Accepted Scope

В рамках версии v1 считается завершённым и зафиксированным:

Синхронизация STOPPING по фактическим ServiceStatusEvent

Переход STOPPING → IDLE только при подтверждении STOPPED от всех сервисов

Введение stop_timeout_sec с переводом системы в ERROR при истечении

Детализированное логирование прогресса STOPPING и timeout-ошибок

Поддержка orchestrator.stop_timeout_sec в профилях (дефолт + WARNING)

Минимальный набор unit tests (EventBus, Profiles loader, Orchestrator v1)

Сохранение неблокирующей UI-модели без изменений интерфейса

Готовность каркаса к подключению реальных сервисов

Указанный scope считается архитектурно закрытым.

Known Limitations (Accepted)

Следующие ограничения приняты осознанно и не считаются дефектами версии v1:

Прогресс STOPPING логируется через логи (k=v), а не через отдельный event-тип
(осознанно, чтобы не расширять enum контрактов в v1)

Unit tests используют fake/заглушечные сервисы и ServiceManager
(осознанно, по ограничениям v1; UI и subprocess не тестируются)

Отсутствуют политики автоматического восстановления/рестарта сервисов

Deferred / Out of Scope

Следующие пункты явно вынесены за рамки версии v1:

Формализация отдельного progress-event для STOPPING

Расширенные recovery/restart policies

Интеграция более одного реального сервиса

Расширение UI (графики, настройки, панели)

Полное тестовое покрытие

Impact on Documentation

Обновление ARCH.md — не требуется

Обновление CONTRACTS.md — не требуется

Запись в DECISIONS.md — не требуется

Документация соответствует фактической реализации v1.

Next Phase Direction

Рекомендованное направление следующего этапа:

v2 — Real Service Integration & Error Semantics Expansion

интеграция одного реального сервиса/устройства

уточнение error/recovery семантики

возможная формализация progress-event

сохранение неизменного UI

Final Note

Версия v1 считается архитектурно закрытой.
Любые изменения поведения или архитектуры после данного acceptance
требуют новой задачи, нового Developer Report и нового Architect Acceptance.

Architect Signature:
Architect Chat