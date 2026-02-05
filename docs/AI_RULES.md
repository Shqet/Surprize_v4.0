# AI RULES

- Архитектуру не менять без решения в DECISIONS.md
- Всегда сначала правим контракты, потом код
- Один сервис = один файл
- Патчи небольшими порциями
- Никаких блокировок в UI
- Все ошибки логируются


---

# Code Style & Tooling Rules

## Formatting
- Используется **black**
- line-length = 100
- target-version = py311
- Форматирование обязательно перед любым коммитом

## Linting
- Используется **ruff**
- Активные группы правил: E, F, I, UP, B, SIM
- E501 (line too long) игнорируется — за это отвечает black
- autofix разрешён

## Typing
- Используется **mypy**
- typing мягкий (soft):
  - disallow_untyped_defs = false
  - check_untyped_defs = true
- Новые публичные методы **желательно** аннотировать

## General Code Rules
- Один класс = один файл (исключения запрещены без решения в DECISIONS.md)
- Один сервис = один файл
- Никакой бизнес-логики в UI
- Все ошибки обязаны логироваться через LogEvent
- Магические числа запрещены (выносить в константы)

## Enforcement
- Developer Chat обязан следовать этим правилам
- Нарушение правил = баг архитектуры
- Изменение правил:
  - только через обновление AI_RULES.md
  - с записью в DECISIONS.md
---

# Process & Responsibility Rules

## Documentation Update Policy

- Документация обновляется **только** при изменении:
  - архитектуры
  - контрактов
  - форматов данных
- Реализационные изменения без изменения поведения
  **не требуют** обновления ARCH.md или CONTRACTS.md
- Все архитектурные решения фиксируются в DECISIONS.md

---

## Developer Chat Assignment Policy

- Один Developer Chat обслуживает **одну архитектурную фазу**
  (v0, v1, v2, …)
- Developer Chat не меняется на каждую задачу
- Смена Developer Chat — **осознанное архитектурное решение**,
  а не ротация “по задаче”

---

## Work Acceptance Rules

### Task Acceptance (any task)
- Любая завершённая задача принимается архитектором
  **только при наличии отчёта**,
  оформленного по шаблону DEV_REPORT_TEMPLATE.md
- Отсутствие отчёта = работа не принята,
  независимо от качества кода

### Version / Phase Closure (v0, v1, v2, …)
- Закрытие версии/фазы выполняется только через
  **Architect Acceptance** по шаблону ARCH_ACCEPTANCE_TEMPLATE.md
- Acceptance основывается на Developer Report и фиксирует:
  - статус (ACCEPTED / ACCEPTED WITH NOTES / REJECTED)
  - принятый scope
  - принятые допущения (не баги)
  - что вынесено в следующую фазу
- После acceptance версия считается **архитектурно закрытой**:
  любые изменения требуют новой задачи и нового отчёта
### Acceptance Storage Rule

- Заполненные Architect Acceptance **обязаны храниться** в каталоге:
  docs/acceptance/
- Имена файлов:
  ACCEPT_<version>.md
  (например: ACCEPT_v0.md, ACCEPT_v1.md)
- Шаблон ARCH_ACCEPTANCE_TEMPLATE.md **не используется** как место для фактической приёмки
- Отсутствие acceptance-файла для версии означает,
  что версия **не считается архитектурно закрытой**
