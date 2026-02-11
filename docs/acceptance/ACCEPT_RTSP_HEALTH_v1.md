# ACCEPT_RTSP_HEALTH_v1.md

## Acceptance — RTSP Health Service v1
**Project:** Surprize_v3.0  
**Service:** rtsp_health  
**Scope:** v1 — Health Monitoring Only (no decoding, no ingest)  
**Date:** 2026-02-11  
**Accepted by:** Architect Chat  

---

# 1. Purpose

Подтвердить, что сервис `rtsp_health`:

- реализует корректный health-мониторинг RTSP-каналов,
- соблюдает архитектуру Surprize_v3.0 (UI → Orchestrator → Services),
- публикует события строго через EventBus,
- имеет предсказуемую и зафиксированную семантику состояний.

---

# 2. Scope of v1

### Included
- Периодический probe RTSP-каналов через `ffprobe`
- Worker-потоки (по одному на канал)
- Backoff + jitter при недоступности канала
- Публикация:
  - `ServiceStatusEvent`
  - `RtspChannelHealthEvent`
  - `LogEvent`
- Fail-fast проверка окружения (ffprobe)

### Explicitly NOT Included
- Декодирование видео
- Захват кадров
- RTSP ingest
- OFFLINE состояние канала
- Метрики latency
- UI-логика

---

# 3. Service-Level Semantics

## 3.1 ServiceStatusEvent

### RUNNING
Сервис мониторинга запущен и выполняет цикл probe/backoff.

Недоступность RTSP НЕ переводит сервис в ERROR.

### ERROR (fatal only)

Сервис публикует `ServiceStatus=ERROR` если:

- `ffprobe` отсутствует или не запускается
- конфигурация некорректна (нет channels, неверные типы и т.д.)

В этом случае:
- worker-потоки не создаются
- дальнейший мониторинг не выполняется

---

# 4. Channel-Level Semantics (v1)

## RtspChannelHealthEvent

Допустимые состояния:

- `CONNECTED`
- `RECONNECTING`

`OFFLINE` в v1 не используется.

### State transitions

Initial state:
RECONNECTING (attempt=0)

yaml
Копировать код

Transitions:

- probe success → `CONNECTED`, attempt=0
- probe fail → `RECONNECTING`, attempt++

### Важно

Недоступность канала — это transient condition.  
Сервис остаётся `RUNNING`.

---

# 5. Configuration (Accepted)

```yaml
services:
  rtsp_health:
    channels:
      visible:
        url: "rtsp://192.168.0.10:8554/visible"
      thermal:
        url: "rtsp://192.168.0.10:8554/thermal"
    probe_timeout_sec: 3
    period_ok_sec: 2
    backoff:
      base_ms: 300
      max_ms: 5000
      jitter_ms: 200
Requirements
Нет захардкоженных URL

Конфигурация читается только через profile loader

Некорректная конфигурация → ServiceStatus=ERROR

6. Logging Contract (Accepted)
Стабильные коды логов:

SERVICE_STATUS service=rtsp_health status=...

RTSP_PROBE_OK channel=<name> url=<...>

RTSP_PROBE_FAIL channel=<name> url=<...> error=<...>

RTSP_BACKOFF channel=<name> delay_ms=<...> attempt=<...>

SERVICE_ERROR service=rtsp_health error=ffprobe_not_found

Все логи в формате k=v.

7. Architectural Compliance
Подтверждено:

UI не содержит RTSP-логики

UI не запускает subprocess

Нет service → service вызовов

Все события публикуются через EventBus

start()/stop() идемпотентны

Worker-потоки корректно завершаются

Нет глобальных эффектов

8. Tests
Покрытие v1:

ffprobe missing → ERROR

bad config → ERROR

probe fail → RUNNING + RECONNECTING

probe success → CONNECTED + attempt=0

OFFLINE никогда не публикуется

Тесты:

без Qt

без сети

без реального ffprobe

deterministic

pytest: зелёный

9. Known Limitations (Accepted)
Нет live-update URL без рестарта сервиса

Нет OFFLINE состояния

Нет latency/response time метрик

Нет rate limiting health heartbeat

Все пункты допустимы для v1.

10. Acceptance Decision
RTSP Health Service v1 соответствует архитектуре Surprize_v3.0.

Семантика состояний зафиксирована.
Контракты соблюдены.
Сервис безопасен для интеграции в UI-мониторинг.

Status: ACCEPTED