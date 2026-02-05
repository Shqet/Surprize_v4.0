# Architect Acceptance → Version Closure

---

## Meta

- **Accepted Version / Scope:**  
  v0 — Desktop application bootstrap (UI + Orchestrator + Services skeleton)

- **Date:**  
  2026-02-05

- **Architect:**  
  Architect Chat

- **Based on:**  
  - Developer Report v0 (2026-02-05)  
  - ARCH.md — Architecture Version v0 (Bootstrap Skeleton)  
  - CONTRACTS.md — Bootstrap Contracts v0, Logging Standards v0, Profiles v0  

---

## Acceptance Decision

- **Status:** ACCEPTED

Версия v0 принята.  
Каркас реализован корректно, архитектура и контракты соблюдены, поведение соответствует Definition of Done v0.

---

## Architectural Compliance

Подтверждаю:

- [x] Архитектура соответствует ARCH.md (Architecture Version v0)  
- [x] Контракты CONTRACTS.md соблюдены  
- [x] Архитектурные запреты не нарушены:  
  - UI не вызывает subprocess  
  - UI не трогает устройства  
  - нет прямых вызовов service → service  
  - EventBus используется как единственный механизм коммуникации  
  - доставка событий в UI осуществляется только через Qt bridge  
- [x] Scope версии не превышен  

---

## Accepted Scope

В рамках версии v0 считается завершённым и зафиксированным:

- UI shell (Start/Stop, state indicator, log/output view, services table)  
- EventBus (thread-safe publish/subscribe)  
- Контрактные события:  
  - LogEvent  
  - ServiceStatusEvent  
  - OrchestratorStateEvent  
  - ProcessOutputEvent  
- Logging subsystem по Logging Standards v0  
- Orchestrator v0 (state machine: IDLE → PRECHECK → RUNNING → STOPPING → IDLE)  
- ServiceManager (регистрация и lifecycle сервисов)  
- ExeRunnerService (demo-сервис v0)  
- Profiles v0 (YAML, минимальная валидация)  
- Composition root (app.main, корректный startup/shutdown)  

Указанный scope считается архитектурно закрытым.

---

## Known Limitations (Accepted)

Следующие ограничения приняты осознанно и не считаются дефектами версии v0:

- Orchestrator v0 переходит в IDLE сразу после вызова `stop_all()`,  
  без ожидания асинхронного `ServiceStatus(STOPPED)` от сервисов.

- Log view в UI не ограничен по размеру (append-only, без ротации).

- ExeRunnerService реализован как демонстрационный сервис (v0),  
  без расширенной политики рестартов и health-check’ов.

Все ограничения признаны допустимыми для bootstrap-версии.

---

## Deferred / Out of Scope

Следующие пункты явно вынесены за рамки версии v0:

- Синхронизация STOPPING → IDLE по событиям ServiceStatus(STOPPED)  
- Реальные сервисы устройств и оборудования  
- Расширенная валидация профилей  
- Автоматические тесты как код (pytest)  
- Ограничение / ротация UI-логов  
- Механизмы recovery и restart policy  

---

## Impact on Documentation

- [x] Обновление ARCH.md — не требуется  
- [x] Обновление CONTRACTS.md — не требуется  
- [x] Запись в DECISIONS.md — не требуется  

Документация соответствует фактической реализации v0.

---

## Next Phase Direction

Рекомендованное направление следующего этапа:

**v1 — Functional Hardening**

- синхронизация Orchestrator STOPPING по ServiceStatusEvent  
- усиление state consistency  
- добавление минимальных unit tests (EventBus, Profiles, Orchestrator)  
- подключение первого реального сервиса (устройство или production exe)  

---

## Final Note

Версия v0 считается архитектурно закрытой.  
Любые изменения поведения или архитектуры после данного acceptance  
требуют новой задачи, нового Developer Report и нового Architect Acceptance.

**Architect Signature:**  
Architect Chat
