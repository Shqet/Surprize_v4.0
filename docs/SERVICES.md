# SERVICES REGISTRY

Name | Status | Description
-----|--------|------------
ExeRunnerService | planned | Run external exe
Service name: ballistics_model
Implementation: app/services/ballistics_model.py

Purpose

Запуск численной баллистической модели как внешнего вычислителя через subprocess. Модель изолирована в model_ballistics/ и управляется через конфиг vkr_config.json.

Inputs

Из профиля (YAML):

model_root: путь к папке модели (cwd для subprocess), например model_ballistics

python_exe: путь к python интерпретатору (по умолчанию python)

calc_entry: файл расчёта, по умолчанию run_vkr.py

plots_entry: файл визуализации, по умолчанию visualization.py (optional)

out_root: корневая папка для запусков, например outputs/ballistics

timeout_sec: таймаут расчёта (subprocess terminate/kill)

make_plots: bool (если true — после расчёта запускаем plots)

config_json: dict, который сериализуется 1:1 в vkr_config.json

Запуск расчёта: python run_vkr.py --config <run_dir>/vkr_config.json --out <run_dir>

Outputs

Файлы в run_dir:

trajectory.csv

diagnostics.csv

(optional) plots/*.png

Lifecycle

start():

публикует SERVICE_STATUS STARTING

создаёт run_id, run_dir

пишет vkr_config.json из config_json

запускает calc subprocess (stdout/stderr → PROCESS_STDOUT/ERR)

по завершении проверяет наличие csv

(optional) запускает plots subprocess

публикует SERVICE_STATUS STOPPED и лог “готово” с путями

stop():

terminate → wait(timeout) → kill

публикует STOPPED или ERROR (если не удалось корректно остановить)

Logging / Events

Строго по стандартам:

SERVICE_START / SERVICE_STOP / SERVICE_ERROR

PROCESS_START / PROCESS_EXIT / PROCESS_STDOUT / PROCESS_STDERR

SERVICE_STATUS (RUNNING/STOPPED/ERROR)
Результат минимум: LogEvent с code=SERVICE_STATUS и message=run_id=... out_dir=... trajectory=... diagnostics=... plots=....

Edge cases

отсутствует config_json → ERROR (не запускать subprocess)

subprocess exit!=0 → ERROR

нет trajectory.csv/diagnostics.csv после exit=0 → ERROR

stop во время расчёта → корректно завершает процесс и выдаёт STOPPED/ERROR