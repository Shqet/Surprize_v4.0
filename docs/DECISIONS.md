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
