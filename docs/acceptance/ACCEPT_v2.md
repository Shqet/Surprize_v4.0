Architect Acceptance → Version Closure
Meta

Accepted Version / Scope:
v2 — Integration of BallisticsModelSubprocessService (first real computational service)

Date:
2026-02-09

Architect:
Architect Chat

Based on:

Developer Report v2 (2026-02-09)

ARCH.md — Event-driven desktop architecture

CONTRACTS.md — v1 STOPPING semantics

SERVICES.md — BallisticsModelSubprocessService

model_ballistics/ii_contract.md — vkr_config.json contract

Acceptance Decision

Status: ACCEPTED

Версия v2 принята.
Первый реальный вычислительный сервис успешно интегрирован в систему без изменения архитектуры, контрактов и UI-слоя.

Accepted Scope

В рамках версии v2 считается завершённым и зафиксированным:

Реализация сервиса BallisticsModelSubprocessService

Интеграция внешней баллистической модели как subprocess

Генерация vkr_config.json из профиля (config_json)

Изоляция вычислительной модели в model_ballistics/

Корректная работа lifecycle:

start / stop / shutdown

terminate → wait → kill

Корректная работа STOPPING-семантики v1 при реальной нагрузке

Best-effort этап визуализации (visualization.py)

Создание и валидация артефактов:

trajectory.csv

diagnostics.csv

plots/* (опционально)

Неблокирующая работа UI

Указанный scope считается архитектурно закрытым.

Architectural Compliance

Подтверждено архитектором:

Архитектура ARCH.md соблюдена

Контракты CONTRACTS.md (v1) соблюдены

SERVICES.md реализован строго по спецификации

UI:

не запускает subprocess

не взаимодействует с вычислительной моделью напрямую

Orchestrator остаётся единственным координатором lifecycle

Сервисы изолированы и не вызывают друг друга напрямую

Вся коммуникация проходит через EventBus

Логирование соответствует Logging Standards v0

Known Limitations (Accepted)

Следующие ограничения приняты осознанно и не считаются дефектами версии v2:

Этап построения графиков выполняется в режиме best-effort и не влияет на итоговый статус сервиса

Валидация config_json ограничена обязательными полями модели

Автоматическое тестирование сервиса отсутствует (допустимо для v2)

Deferred / Out of Scope

Следующие пункты явно вынесены за рамки версии v2:

UI-форма ввода параметров модели

Встроенная визуализация траектории в UI

Очереди запусков и история расчётов

Расширенные recovery / retry-механизмы

Формализация отдельного progress-event

Impact on Documentation

ARCH.md — без изменений

CONTRACTS.md — без изменений

SERVICES.md — дополнен сервисом ballistics_model

ROADMAP.md — v2 считается CLOSED

Документация соответствует фактической реализации версии v2.

Next Phase Direction

Рекомендуемое направление следующей версии:

v3 — User Interaction & Visualization Layer

Возможные фокусы v3:

UI-форма задания начальных параметров

Отображение траектории и диагностик

Улучшение пользовательского цикла работы с расчётами

Final Note

Версия v2 считается архитектурно закрытой.
Любые изменения вычислительного сервиса или пользовательского взаимодействия
должны оформляться как новая версия (v3) с отдельным циклом проектирования и приёмки.

Architect Signature:
Architect Chat