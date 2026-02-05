# CONTRACTS

## BaseService

Methods:
- start()
- stop()
- get_status()

---

## ServiceStatus

IDLE
STARTING
RUNNING
ERROR
STOPPED

---

## Orchestrator States

IDLE
PRECHECK
RUNNING
STOPPING
ERROR

---

## Event Types

LogEvent:
  level
  message

ServiceStatusEvent:
  service_name
  status

OrchestratorStateEvent:
  state

---

## Profiles

YAML/JSON

profile_name:
  services:
    exe_runner:
      path: "tool.exe"
      args: "--test"

---

# Bootstrap Contracts v0

## Enums

### OrchestratorState
- IDLE
- PRECHECK
- RUNNING
- STOPPING
- ERROR

### ServiceStatus
- IDLE
- STARTING
- RUNNING
- STOPPED
- ERROR

---

## BaseService Interface

Methods:
- name: str
- start() -> None
- stop() -> None
- status() -> ServiceStatus

Rules:
- start() и stop() идемпотентны
- ошибки публикуются как LogEvent + ServiceStatusEvent(ERROR)

---

## Event Types

### LogEvent
- level: str
- message: str

### ServiceStatusEvent
- service_name: str
- status: ServiceStatus

### OrchestratorStateEvent
- state: OrchestratorState

### ProcessOutputEvent
- service_name: str
- stream: "stdout" | "stderr"
- line: str

---

## Orchestrator Public API

- start(profile_name: str) -> None
- stop() -> None
- get_state() -> OrchestratorState

---

## Profiles v0

YAML

profile_name:
  services:
    exe_runner:
      path: str
      args: str
      timeout_sec: int


---

# Logging Standards v0

## Log Levels

DEBUG  
INFO  
WARNING  
ERROR  

---

## LogEvent Contract

LogEvent:
- level: LogLevel
- source: str        # имя компонента или сервиса
- code: str          # короткий машинный код события
- message: str       # человекочитаемый текст

---

## Standard Log Codes

### System

SYSTEM_START  
SYSTEM_STOP  

---

### Orchestrator

ORCH_START_REQUEST  
ORCH_STOP_REQUEST  
ORCH_STATE_CHANGE  

---

### Services

SERVICE_REGISTER  
SERVICE_START  
SERVICE_STOP  
SERVICE_STATUS  
SERVICE_ERROR  

---

### Process / Exe

PROCESS_START  
PROCESS_EXIT  
PROCESS_STDOUT  
PROCESS_STDERR  

---

## Log Message Rules

- message — короткая фраза без лишних слов  
- важные параметры выносятся в message как key=value  

Пример:

level=INFO  
source=ExeRunnerService  
code=PROCESS_START  
message=path=cmd args="/c ping 127.0.0.1 -n 5"

---

## Mandatory Logging Points

Каждый сервис обязан:

- логировать start  
- логировать stop  
- логировать ошибки  

ExeRunnerService дополнительно:

- PROCESS_START  
- PROCESS_EXIT  
- PROCESS_STDOUT  
- PROCESS_STDERR  

---

# Profiles v0

## Profile File Format

YAML

---

## Root Structure

profile_name:
  services:
    <service_name>:
      <param>: <value>

---

## Minimal Example

default:
  services:
    exe_runner:
      path: "cmd"
      args: "/c ping 127.0.0.1 -n 5"
      timeout_sec: 10

---

## Rules

- Все сервисы читают параметры только из профиля
- Захардкоженные пути запрещены
- Отсутствующий параметр → ошибка старта сервиса
