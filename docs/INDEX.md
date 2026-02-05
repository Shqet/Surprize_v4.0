# Project Documentation Index

## TL;DR
Проект — Desktop приложение (Windows 10/11):
Операторская оболочка + Orchestrator + Services.

UI ничего не знает про устройства и subprocess.
Вся логика только через Orchestrator и Services.

Документация предназначена в первую очередь для ИИ-ассистента.

---

## Sources of Truth (главные документы)

1) VISION.md — цель проекта, границы
2) ARCH.md — архитектура, слои, потоки
3) CONTRACTS.md — контракты событий и сервисов
4) AI_RULES.md — правила для ИИ-реализации

Эти файлы имеют приоритет над всем остальным.

---

## Operational Docs

- RUNBOOK.md — сборка, запуск, логи, типовые проблемы
- ROADMAP.md — очередь задач
- DECISIONS.md — журнал решений
- SERVICES.md — реестр сервисов
- DEV_REPORT_TEMPLATE.md — обязательный шаблон отчёта разработчика для приёмки архитектором
- ARCH_ACCEPTANCE_TEMPLATE.md — шаблон ответа архитектора (акт приёмки версии/этапа по Developer Report)
- acceptance/ — принятые Architect Acceptance (закрытые версии и фазы проекта)


---

## How to feed chats

### Architect Chat
- VISION.md
- ARCH.md
- CONTRACTS.md

### Developer Chat
- ARCH.md
- CONTRACTS.md
- AI_RULES.md
- RUNBOOK.md

### Fixer Chat
- RUNBOOK.md
- CONTRACTS.md
- ссылка на баг-репорт

---

## Core Principles

- UI → Orchestrator → Services → Drivers/Adapters
- Сервисы независимы друг от друга
- Состояние системы управляется Orchestrator
- Всё общение — событиями
