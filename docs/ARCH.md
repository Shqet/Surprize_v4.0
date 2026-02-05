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
