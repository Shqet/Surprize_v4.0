# SCENARIO_E2E_v1.md

Версия: v1  
Дата: 2026-03-01  
Назначение: единый целевой e2e-сценарий для отладки UI и демонстрационного прогона с записью артефактов.

---

## 1. Scope

Сценарий покрывает:
- подъем daemon-сервисов (`video_visible`, `video_thermal`, `mayak_spindle`)
- precheck готовности `mayak_spindle`
- запуск job-сервиса расчета траектории (`ballistics_model`)
- наблюдение статусов в UI
- сохранение артефактов прогона

Вне scope:
- полноценный TrialRunner (автосценарий с моторными фазами)
- финальная операторская UX-полировка

---

## 1.1 Mayak Test Contract (v1)

UI не управляет транспортом/`D`-ячейками напрямую.  
UI передаёт параметры теста в Orchestrator, Orchestrator проксирует их в `mayak_spindle`.

Команды v1:
1. `start_mayak_test(head_start_rpm, head_end_rpm, tail_start_rpm, tail_end_rpm, profile_type, duration_sec)`
2. `stop_mayak_test()`
3. `emergency_stop()`

Параметры теста:
- `head_start_rpm`, `head_end_rpm` — стартовая/конечная скорость головного шпинделя
- `tail_start_rpm`, `tail_end_rpm` — стартовая/конечная скорость хвостового шпинделя
- `profile_type` — тип закона изменения (`linear`, `step`, ...)
- `duration_sec` — длительность теста (обычно определяется из траектории)

Ключевое правило:
- алгоритм изменения скорости выполняется на стороне Маяка;
- UI/Orchestrator только задают параметры и командуют старт/стоп;
- обратная связь в UI только через события (`MayakHealthEvent`, `MayakSpindleTelemetryEvent`, `ServiceStatusEvent`).
- все D-ячейки задаются только через `services.mayak_spindle.d_map` (без хардкода адресов в коде).

---

## 2. Preconditions

1. Профиль `default` валиден и содержит `services.mayak_spindle`, `video_visible`, `video_thermal`, `ballistics_model`.
2. Запущен эмулятор Маяка (`majak_sim`) и отвечает по UDP-портам из профиля.
3. RTSP-источники доступны или заменены тестовыми потоками.
4. Приложение запускается через `python -m app.main`.

---

## 3. Режимы прогона

1. `full-e2e`:
- строгий precheck `mayak_spindle` (ошибка готовности блокирует run-cycle job)
- оба видео-канала и маяк обязательны

2. `ui-dev`:
- UI отлаживается даже при неполной инфраструктуре
- допускается degraded окружение (но фиксируется в логах)

---

## 4. Целевой поток (Happy Path)

1. Старт приложения.
2. Orchestrator вызывает `start_daemons("default")`.
3. `video_visible`, `video_thermal`, `mayak_spindle` переходят в `RUNNING`.
4. `mayak_spindle` публикует `MayakHealthEvent(ready=True)`.
5. Оператор нажимает "Сгенерировать траекторию".
6. UI вызывает `orch.start("default", overrides=...)` для `ballistics_model`.
7. `ballistics_model` проходит `STARTING -> RUNNING -> STOPPED`.
8. Orchestrator закрывает run-cycle и возвращается в `IDLE`.
9. UI показывает результат и пути к артефактам.

---

## 5. Negative Path (минимум)

1. `mayak_spindle` не готов в пределах `mayak_ready_timeout_sec`:
- Orchestrator публикует ошибку precheck
- run-cycle jobs не стартует
- UI показывает понятную причину

2. Ошибка `ballistics_model`:
- сервис публикует `ERROR`
- Orchestrator переходит в `ERROR`
- UI показывает статус ошибки и лог-код/сообщение

3. Потеря RTSP:
- daemon видео остается живым (reconnect/backoff)
- run-cycle траектории не должен падать от этого факта сам по себе

---

## 6. UI Checklist

1. Виден общий state Orchestrator (`IDLE/PRECHECK/RUNNING/ERROR`).
2. Видны статусы сервисов (`video_*`, `mayak_spindle`, `ballistics_model`).
3. Видна готовность маяка (`ready/not-ready`, причина).
4. Кнопка генерации блокируется во время `ballistics_model=RUNNING`.
5. После завершения снова доступен запуск.
6. Визуализация траектории обновляется по завершению.

---

## 7. Recording / Artifacts

Для каждого прогона создается `scenario_id` (например `YYYYMMDD_HHMMSS`).

Минимальный набор:
1. `outputs/<scenario_id>/app.log` (или ссылка на диапазон в общем логе)
2. `outputs/<scenario_id>/timeline.jsonl` (ключевые события e2e)
3. `outputs/<scenario_id>/trajectory/` (`trajectory.csv`, `diagnostics.csv`, optional plots)
4. `outputs/<scenario_id>/video_preview/visible/latest.jpg`
5. `outputs/<scenario_id>/video_preview/thermal/latest.jpg`
6. `outputs/<scenario_id>/summary.json`:
- результат (`PASS/FAIL`)
- причина fail (если есть)
- длительность
- версии/commit hash

---

## 8. Acceptance Criteria

Сценарий считается пройденным, если:
1. Daemon-сервисы поднялись без фатальных ошибок.
2. `mayak_spindle` достиг `ready=True`.
3. `ballistics_model` завершился `STOPPED` и выдал артефакты.
4. Orchestrator вернулся в `IDLE` после run-cycle.
5. UI отобразил статусы и результат без зависаний.
6. Артефакты прогона сохранены и достаточны для разбора.

---

## 9. Команды прогона (черновик)

1. Smoke маяка с эмулятором:
`python -m pytest -q tests/test_mayak_spindle_smoke.py::test_mayak_spindle_with_real_emulator -s`

2. Полный тестовый набор:
`python -m pytest -q`

3. Запуск UI:
`python -m app.main`

---

## 10. Next Step

После утверждения этого документа:
1. завести task-list по UI (state panel + mayak health panel + scenario timeline panel),
2. добавить генерацию `scenario_id` и запись `timeline.jsonl`,
3. провести первый формальный `full-e2e` прогон с фиксацией артефактов.
