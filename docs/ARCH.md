# ARCHITECTURE

## Layers

UI
↓
Orchestrator
↓
Services
↓
Drivers / Adapters

---

## UI

- Только отображение
- Только пользовательские команды
- Нет subprocess
- Нет доступа к устройствам

---

## Orchestrator

- State Machine
- Управляет жизненным циклом сервисов
- Проверяет готовность

---

## Services

- Каждый сервис изолирован
- Отвечает за один ресурс
- Общается через EventBus

---

## Threading Model

- UI thread: только UI
- Services: worker threads
- Блокирующие операции запрещены в UI

---

## Forbidden

- UI → subprocess
- UI → устройство
- Service → Service прямые вызовы
---

# Architecture Version v0 (Bootstrap Skeleton)

## Purpose

Зафиксировать минимальный каркас системы, на котором строится весь проект.

Каркас обеспечивает:
- запуск приложения
- управление состояниями
- управление сервисами
- событийную коммуникацию
- UI-оболочку

Каркас не содержит бизнес-логики устройств.

---

## Components

### UI Shell
Минимальное окно оператора.

Responsibilities:
- отображение состояния системы
- отображение логов
- кнопки Start / Stop
- отображение статусов сервисов

Forbidden:
- subprocess
- доступ к устройствам
- блокирующие операции

---

### EventBus

Центральная шина событий.

Responsibilities:
- publish(event)
- subscribe(event_type, handler)

Properties:
- потокобезопасный
- не знает про UI

---

### UI Event Bridge

Адаптер между EventBus и Qt.

Responsibilities:
- подписка на EventBus
- преобразование событий в Qt signals

---

### Logger

Responsibilities:
- запись в файл
- публикация LogEvent в EventBus

---

### Orchestrator

State machine верхнего уровня.

Responsibilities:
- хранит состояние системы
- управляет жизненным циклом сервисов
- реализует команды Start / Stop

Does NOT:
- знать детали сервисов

---

### ServiceManager

Responsibilities:
- регистрация сервисов
- start_all()
- stop_all()
- доступ к сервисам по имени

---

### BaseService

Абстрактный интерфейс для всех сервисов.

---

### ExeRunnerService (v0)

Демонстрационный сервис.

Responsibilities:
- запуск внешнего процесса
- чтение stdout/stderr
- публикация вывода как событий

---

## Data Flow

UI
 → Orchestrator
 → ServiceManager
 → Service
 → EventBus
 → UI Event Bridge
 → UI

---

## Threading Model

- UI: главный поток
- каждый сервис имеет worker thread
- EventBus потокобезопасный

---

## Stability Rule

Изменение каркаса:
- только через обновление ARCH.md
- запись в DECISIONS.md обязательна

Architecture Version v1 (Functional Hardening)
Purpose

v1 усиливает каркас v0, чтобы система была готова к реальным сервисам/железу:

синхронизация остановки по фактическим статусам сервисов

формализация поведения при ошибках и зависаниях

минимальная тестируемость ядра (без UI)

Scope v1

Добавляется/усиливается:

STOPPING synchronization

Переход STOPPING → IDLE допускается только тогда, когда:

все сервисы подтвердили ServiceStatus=STOPPED, или

истёк общий timeout остановки (и это трактуется как ERROR/DEGRADED согласно контракту v1)

Service lifecycle observability

Статусы сервисов становятся источником истины для оркестратора при остановке.

Оркестратор ведёт внутреннюю “карту статусов” сервисов.

Error semantics

В v1 вводится формализация:

какие ошибки переводят систему в ERROR

какие ошибки допускают возвращение в IDLE после stop/recover

Все ошибки должны быть трассируемы через события и логи.

First real service readiness

Добавление “первого реального сервиса” не меняет UI и не меняет слои.

Сервис реализуется строго через BaseService и EventBus.

Orchestrator v1 Responsibilities

В дополнение к v0:

Управляет остановкой как координацией, а не как “вызвал stop_all и ушёл в IDLE”.

В состоянии STOPPING:

инициирует остановку всех сервисов

ждёт подтверждений ServiceStatus(STOPPED) от каждого сервиса

публикует прогресс остановки (через события/логи)

При таймауте остановки:

фиксирует ошибку (лог + event)

переводит систему в ERROR (или IDLE с отметкой деградации — только если так решено в CONTRACTS v1)

ServiceManager v1 Responsibilities

Уточнение (без расширения роли):

ServiceManager по-прежнему отвечает за вызовы start/stop и регистрацию.

ServiceManager не является источником истины о “успешной остановке” — статус подтверждает сервис через EventBus.

ServiceManager должен предоставлять список зарегистрированных сервисов (для ожидания STOPPED оркестратором).

Services v1 Responsibilities

Уточнение требований к сервисам:

Сервис обязан публиковать ServiceStatusEvent:

при старте: STARTING → RUNNING (или ERROR)

при остановке: STOPPED (или ERROR)

Сервис обязан быть идемпотентным по start/stop.

“Гарантия STOPPED”:

При нормальной остановке сервис обязан довести статус до STOPPED.

Если сервис не может корректно остановиться — он обязан публиковать ERROR.

Threading Model v1 (no change, but tightened)

UI thread: только UI.

Все блокировки/ожидания статусов STOPPED выполняются вне UI thread.

UI получает обновления состояния через Qt bridge.

Non-Goals v1

Переписывание на asyncio

Дистрибутивная архитектура/агент

Рестарт-политики и supervision (кроме базовой диагностики)

Большой UI-рефактор

v1 Exit Criteria (architecture)

v1 считается архитектурно готовой, если:

STOPPING → IDLE зависит от STOPPED статусов сервисов

таймаут остановки определён и формализован

поведение при ошибках задокументировано и наблюдаемо через события/логи

## Orchestrator v4 — Service Roles

Начиная с v4, Orchestrator поддерживает роли сервисов:

- job — участвует в run-cycle
- daemon — долгоживущий сервис

### RUNNING State

Orchestrator находится в состоянии RUNNING
только во время выполнения job-сервисов.

Daemon-сервисы могут быть RUNNING,
при этом Orchestrator может быть в IDLE.

### Lifecycle Isolation

- Завершение job-сервисов завершает run-cycle.
- daemon-сервисы не влияют на RUNNING/IDLE.
- stop() останавливает только job.
- shutdown останавливает все сервисы.

Это позволяет системе поддерживать
постоянные фоновые сервисы (RTSP, SDR, hardware)
и при этом выполнять отдельные вычислительные задачи.
