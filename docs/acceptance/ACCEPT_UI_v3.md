# ACCEPT_UI_v3.md

## Acceptance — UI v3 (End-to-End User Flow)

**Project:** Surprize_v3.0  
**Scope:** UI Layer v3 (Steps 1–4)  
**Date:** 2026-02-10  
**Accepted by:** Architect Chat

---

## 1. Scope of Acceptance

Данный документ фиксирует приёмку UI v3 — первого завершённого пользовательского контура приложения:

- ввод параметров расчёта,
- запуск вычислительного сервиса,
- управление состояниями,
- визуализация результата в 3D.

UI v3 считается **функционально завершённым** и архитектурно корректным.

---

## 2. Accepted Steps

### UI Step 1 — Layout Wiring
- UI собран из Qt Designer
- Контейнеры:
  - `gl_trajectory_params`
  - `vl_trajectory_visualization`
- Наполнение производится только кодом
- Архитектурные границы соблюдены

**Status:** ACCEPTED

---

### UI Step 2 — Config JSON Editor (in-memory)
- Полный редактор `config_json`
- Работа только в памяти
- Валидация типов
- Generate — формирование intent без исполнения

**Status:** ACCEPTED

---

### UI Step 3 — Run Intent → Orchestrator
- UI формирует runtime intent
- Передача `config_json` через Orchestrator runtime overrides
- Без записи профилей на диск
- Без monkey-patching
- Управление состояниями RUNNING / STOPPED / ERROR

**Status:** ACCEPTED

---

### UI Step 4 — 3D Trajectory Visualization
- Загрузка результата строго после `SERVICE_STATUS=STOPPED`
- Источник пути результата — `LogEvent (k=v)`
- Асинхронная загрузка CSV
- Реальный 3D-рендер через `pyqtgraph.opengl`
- Стабильный UX без пересоздания виджетов

**Status:** ACCEPTED

---

## 3. Architectural Compliance

Подтверждено:

- UI не запускает subprocess
- UI не вызывает `visualization.py`
- UI не пишет конфиги на диск
- UI не︎→ Service — только через Orchestrator
- Все события проходят через EventBus
- UI работает только через UI Bridge
- Orchestrator — единственный authority запуска
- Runtime overrides применяются только in-memory

Нарушений архитектурных контрактов не выявлено.

---

## 4. Logging & Observability

UI и система в целом предоставляют корректную трассировку:

- UI_* — пользовательские намерения и UX-состояния
- ORCH_* — orchestration
- SERVICE_STATUS — фактическое состояние сервиса
- UI_VIS_* — загрузка и рендер результата

Поведение системы полностью реконструируется по логам.

---

## 5. Known Limitations (Accepted)

Осознанно принятые ограничения UI v3:

- UI не анализирует результаты расчёта (только визуализация)
- Нет истории запусков (отображается последний результат)
- Нет post-processing поверх траектории
- Нет валидации бизнес-ограничений модели (ответственность сервиса)

Данные ограничения не считаются дефектами текущей версии.

---

## 6. Acceptance Result

**UI v3 ACCEPTED.**

Система предоставляет:
- замкнутый пользовательский цикл,
- устойчивое поведение,
- расширяемую архитектурную базу.

Готово к:
- стабилизации / демонстрации,
- расширению визуализации,
- добавлению новых сервисов,
- следующей фазе UI или orchestration.

---

## 7. Next Steps (Out of Scope)

- UI Step 5: post-processing / overlays
- UI history / multi-run comparison
- Additional services
- Formal release / demo packaging
