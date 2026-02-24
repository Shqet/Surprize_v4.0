## mayak_spindle (v1)

**Purpose:** Управлять двумя шпинделями через «Маяк» и публиковать телеметрию (скорость/момент/угол).

### Config (profile section)
`services.mayak_spindle`:

- `d_map: dict[str,str]` — обязательная карта D-ячеек, ключи как в emulator.py `D_MAP`:
  - SP1_ControlWord, SP1_TargetSpeed, SP1_StatusWord, SP1_ActualSpeed
  - SP2_ControlWord, SP2_TargetSpeed, SP2_StatusWord, SP2_ActualSpeed
  - SP1_ActualTorque, SP2_ActualTorque, SP1_Angle
  - SP1_Connected, SP2_Connected
  - Global_Enable, Sim_Time, Error_Code
- `publish_period_ms: int` — период опроса/публикации телеметрии (default 50)
- `global_enable: bool` — (optional) стартовое значение enable

### Events Emitted
- `ServiceStatusEvent(service="mayak_spindle", status=.)`
- `MayakSpindleTelemetryEvent(service="mayak_spindle", spindle="sp1|sp2", ...)`
- `MayakSpindleCommandEvent` (emit-only, debug/UI)
- `LogEvent` (k=v)

### Logging (required codes, k=v)
- `SERVICE_START service=mayak_spindle`
- `SERVICE_RUNNING service=mayak_spindle period_ms=<int>`
- `SERVICE_STOP service=mayak_spindle`
- `SERVICE_STOPPED service=mayak_spindle`
- `SERVICE_ERROR service=mayak_spindle error=<str>`

### Lifecycle & Semantics
- start(): fail-fast по некорректной конфигурации (ERROR без worker)
- stop(): завершает worker thread без утечек
- Сервис не знает о роли job/daemon (роль решает Orchestrator v4)
