Назначение

Этот документ описывает обязательные требования к реализации любого нового сервиса в проекте.

Цель:

предсказуемая интеграция через Orchestrator

отсутствие архитектурных пробоин

корректная работа в режиме job или daemon

тестируемость без Qt / сети / реальных subprocess

1. Архитектурная позиция сервиса

Сервис находится на уровне:

UI → Orchestrator → ServiceManager → Service


Сервис:

не знает о UI

не знает о Orchestrator

не вызывает другие сервисы

не читает профили напрямую

не стартует другие сервисы

Единственный вход — start(profile_section)
Единственный выход — публикация событий через EventBus.

2. Обязательный публичный API

Каждый сервис обязан реализовать:

class MyService:
    name = "my_service"

    def start(self, profile_section: dict) -> None: ...
    def stop(self) -> None: ...
    def status(self) -> str: ...

2.1 start(profile_section)

получает уже провалидированную секцию профиля

не читает YAML напрямую

не делает глубокий merge профиля

публикует:

SERVICE_STATUS status=STARTING

затем SERVICE_STATUS status=RUNNING

должен быть идемпотентным:

повторный вызов start() при RUNNING ничего не ломает

2.2 stop()

идемпотентен

корректно завершает:

worker threads

subprocess (terminate → wait → kill)

публикует:

SERVICE_STATUS status=STOPPING

затем SERVICE_STATUS status=STOPPED

2.3 status()

Возвращает текущее внутреннее состояние сервиса:

STARTING
RUNNING
STOPPING
STOPPED
ERROR

3. Role: job vs daemon (Orchestrator v4)

В профиле:

services:
  my_service:
    role: job | daemon


Сервис НЕ знает о своей роли.
Роль обрабатывается только Orchestrator.

Важно:

job-сервисы участвуют в run-cycle

daemon-сервисы могут жить постоянно

daemon НЕ переводит Orchestrator в RUNNING

Сервис не должен содержать условную логику “если daemon”.

4. Event Publishing Rules

Все события публикуются через EventBus.

Обязательные:
ServiceStatusEvent
LogEvent

Дополнительные (по контракту):

RtspChannelHealthEvent

RtspIngestStatsEvent

ProcessOutputEvent

etc.

5. Logging Standard

Используется emit_log(...)

Формат:

EVENT_CODE key=value key=value


Пример:

SERVICE_START service=my_service
SERVICE_STATUS service=my_service status=RUNNING
MY_EVENT channel=visible state=CONNECTED

Запрещено:

print()

произвольный текст без k=v

логировать огромные stderr построчно

6. Subprocess Rules (если используется)

Разрешено:

subprocess.Popen

чтение stdout/stderr в worker thread

bounded stderr ring-buffer

Обязательный stop-паттерн:

terminate()
wait(timeout)
kill()


Обязательно:

no zombie processes

no blocking in UI thread

no busy loops

7. Restart / Backoff Rules (для daemon)

Если сервис — долгоживущий (например ingest):

restart допускается

backoff обязателен

минимум 1000ms

cap 10000ms

jitter допустим

Запрещено:

restart storm (millisecond loop)

8. State Machine Guidelines

Сервис обязан иметь явную внутреннюю state machine.

Пример (ingest):

STARTING
RUNNING
NO_FRAMES
INGESTING
STALLED
RESTARTING
ERROR


Важно:

STALLED не должен срабатывать до first-frame grace

ERROR — только при фатале (invalid config / missing binary)

9. Fail-Fast Policy

Фатальные ошибки (до worker-start):

invalid config

missing binary (ffmpeg / ffprobe)

invalid required fields

Поведение:

SERVICE_STATUS status=ERROR
emit_log SERVICE_ERROR ...


Workers не создаются.

10. Forbidden

Сервису запрещено:

импортировать UI

импортировать Orchestrator

вызывать другой сервис

читать profile YAML напрямую

блокировать поток start()

обращаться к Qt

делать сетевые проверки из UI слоя

11. Output / Filesystem Policy

Если сервис пишет файлы:

путь формируется из profile_section

нет захардкоженных путей

директории создаются явно

ошибки прав логируются

12. Developer Report Template (для сервиса)

Каждый новый сервис обязан сопровождаться отчётом:

Developer Report → Architect Review

Meta:
Version / Scope:
Related Docs:

Status:
Кратко:

What Was Implemented:
Core:
Services:
Events:
Profiles:

Logs (fact):
Verification (fact):

Architectural Compliance Checklist:
[ ] No UI imports
[ ] No service-to-service calls
[ ] Idempotent start/stop
[ ] Proper logging
[ ] Backoff safe (if daemon)
[ ] Tests green

13. Unit Testing Requirements

Тесты сервиса:

без Qt

без сети

без реального ffmpeg (mock)

без Orchestrator

Проверяется:

start() → RUNNING

stop() → STOPPED

fail-fast → ERROR

restart/backoff логика

корректная публикация событий

14. Definition of Done (Service)

Сервис считается готовым если:

pytest зелёный

start/stop идемпотентны

нет утечек потоков

нет zombie subprocess

нет архитектурных нарушений

корректно работает в роли job

корректно работает в роли daemon

15. Минимальный Skeleton
class MyService:
    name = "my_service"

    def __init__(self, bus):
        self._bus = bus
        self._state = "STOPPED"
        self._lock = threading.Lock()

    def start(self, profile_section: dict):
        with self._lock:
            if self._state in ("RUNNING", "STARTING"):
                return
            self._state = "STARTING"
            self._bus.publish(ServiceStatusEvent(...))
            # validate config
            # start worker
            self._state = "RUNNING"
            self._bus.publish(ServiceStatusEvent(...))

    def stop(self):
        with self._lock:
            if self._state in ("STOPPED", "STOPPING"):
                return
            self._state = "STOPPING"
            self._bus.publish(ServiceStatusEvent(...))
            # stop worker
            self._state = "STOPPED"
            self._bus.publish(ServiceStatusEvent(...))

    def status(self):
        return self._state

Итог

Это шаблон уже под твою текущую архитектуру:

Orchestrator v4

daemon auto-start

restart/backoff

UI isolation

event-driven модель