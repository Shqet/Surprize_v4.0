# ACCEPT_ORCH_v4.md

## Acceptance — Orchestrator v4 (Service Roles: job / daemon)

**Project:** Surprize_v3.0  
**Scope:** Orchestrator lifecycle semantics upgrade  
**Version:** v4  
**Date:** 2026-02-11  
**Accepted by:** Architect Chat  

---

# 1. Purpose

Зафиксировать новую семантику Orchestrator, поддерживающую:

- долгоживущие daemon-сервисы
- одноразовые job-сервисы
- корректную изоляцию run-cycle
- отсутствие "вечного RUNNING" при активных daemon

---

# 2. Introduced Concept — Service Roles

Каждый сервис теперь имеет роль:

- `job`
- `daemon`

Если поле `role` отсутствует → по умолчанию `job` (backward compatible).

---

# 3. Orchestrator Semantics (v4)

## RUNNING

`RUNNING` означает:

> Выполняется run-cycle job-сервисов.

Daemon-сервисы могут быть RUNNING,  
но это **не делает Orchestrator RUNNING**.

---

## Run Completion Rule

Orchestrator переходит:

RUNNING → IDLE

markdown
Копировать код

когда:

- все job-сервисы опубликовали `ServiceStatus=STOPPED`

Если любой job-сервис публикует `ERROR`:

RUNNING → ERROR

yaml
Копировать код

Daemon STOPPED/ERROR не завершает run-cycle.

---

# 4. stop() Semantics

`stop()`:

- останавливает только job-сервисы
- daemon продолжают работать
- после STOPPED всех job → IDLE

Shutdown (через app.main):

- orch.stop() (jobs)
- service_manager.stop_all() (все сервисы)

---

# 5. Architectural Compliance

Подтверждено:

- Нет изменений ServiceManager API
- Нет изменений EventBus контрактов
- UI не изменён
- Backward compatibility сохранена
- start(profile) продолжает работать без role-поля

---

# 6. Tests

Покрыто:

- daemon RUNNING не держит Orchestrator RUNNING
- stop() останавливает только job
- default role=job
- job ERROR → Orchestrator ERROR

pytest: зелёный

---

# 7. Decision

Orchestrator v4 принят.

Система готова к использованию постоянных daemon-сервисов
(rtsp_health, rtsp_ingest, SDR, hardware control и др.)
без нарушения run-cycle логики.

## Status: ACCEPTED