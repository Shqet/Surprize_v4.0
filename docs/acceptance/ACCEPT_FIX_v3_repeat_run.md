# ACCEPT_FIX_v3_repeat_run.md

## Acceptance — Fix: Repeat Run After Natural Completion

**Project:** Surprize_v3.0  
**Scope:** Orchestrator v3 — State Machine Correction  
**Date:** 2026-02-10  
**Accepted by:** Architect Chat

---

## 1. Problem Description

После успешного завершения расчёта:

RUNNING → SERVICE_STATUS(ballistics_model, STOPPED)

повторный запуск через UI (Generate) не инициировал новый цикл.

Наблюдаемое поведение:

- UI логировал `UI_GENERATE_CLICKED`
- UI логировал `UI_RUN_REQUESTED`
- `ORCH_START_REQUEST` отсутствовал
- Новый расчёт не запускался
- Работоспособность восстанавливалась только после перезапуска приложения

Это нарушало основной пользовательский цикл UI v3.

---

## 2. Root Cause

Orchestrator не выполнял переход состояния:

RUNNING → IDLE

при естественном завершении главного сервиса (`ballistics_model`).

Переход в `IDLE` существовал только в сценарии `ORCH_STOP_REQUEST`,  
но отсутствовал при штатном завершении сервиса.

Следствие:

- Orchestrator оставался в состоянии `RUNNING`
- Guard внутри `start()` блокировал повторный запуск
- State machine была логически незамкнутой

---

## 3. Implemented Fix

Файл:
app/orchestrator/orchestrator.py

makefile
Копировать код

Метод:
_on_service_status_event()

markdown
Копировать код

Добавлена логика:

Если:

- `state == RUNNING`
- `service == "ballistics_model"`
- `status in (STOPPED, ERROR)`

То:

- публикуется `ORCH_RUN_FINISHED`
- выполняется переход:
  - STOPPED → IDLE
  - ERROR → ERROR

---

## 4. State Machine Correction

### До фикса

IDLE → PRECHECK → RUNNING
RUNNING → (service STOPPED) → RUNNING ❌

shell
Копировать код

### После фикса

IDLE → PRECHECK → RUNNING
RUNNING → (service STOPPED) → IDLE ✅

yaml
Копировать код

State machine стала корректно замкнутой и детерминированной.

---

## 5. Validation

Проверено:

- Первый запуск — корректен
- Второй запуск — корректен
- Третий запуск — корректен
- Неограниченное количество последовательных запусков работает без перезапуска приложения
- STOP / shutdown работают как ранее

Логи подтверждают повторные циклы:

ORCH_START_REQUEST
ORCH_STATE_CHANGE IDLE → PRECHECK → RUNNING
...
SERVICE_STATUS ballistics_model STOPPED
ORCH_RUN_FINISHED
ORCH_STATE_CHANGE RUNNING → IDLE

yaml
Копировать код

---

## 6. Architectural Assessment

Подтверждено:

- Архитектура не изменена
- Контракты не нарушены
- Lifecycle сервиса не модифицирован
- UI не содержит компенсирующих костылей
- Runtime overrides не затронуты

Исправление локализовано и минимально.

---

## 7. Acceptance Result

**Fix ACCEPTED.**

Orchestrator v3 state machine признана корректной.

Повторный запуск расчёта после естественного завершения полностью восстановлен.

---

## 8. Impact

Данный фикс завершает стабилизацию UI v3.

Система теперь поддерживает:

- Неограниченные последовательные расчёты
- Корректное замыкание run-cycle
- Предсказуемое поведение Orchestrator

Готово к дальнейшему развитию или фазе стабилизации.