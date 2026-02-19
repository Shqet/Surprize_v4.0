GPS SDR Simulation Service
Назначение

gps_sdr_sim — сервис генерации и воспроизведения GPS-сигнала на основе:

Локальной траектории (CSV)

Конвертации в NMEA (10 Гц)

Генерации IQ через gps-sdr-sim.exe

Воспроизведения IQ через PlutoPlayer.exe

Сервис реализует единый pipeline:

trajectory.csv
   ↓
NMEA (10Hz + static prefix)
   ↓
gps-sdr-sim → gpssim_iq.bin
   ↓
PlutoPlayer → transmission

Структура проекта
app/
  services/
    gps_sdr_sim/
      engine.py
      formats.py
      process.py
      service.py

bin/
  gps_sdr_sim/gps-sdr-sim.exe
  pluto/PlutoPlayer.exe

outputs/
  gps_sdr_sim/<run_id>/
    input/
    sim/
    pluto/
    logs/
    meta/

Входные данные
1️⃣ Траектория (CSV)

Ожидаемые колонки:

Колонка	Описание
t	время (сек)
X	East (м)
Y	North (м)
Z	Up (м)

Координаты интерпретируются как ENU относительно заданной стартовой точки.

2️⃣ Параметры
Параметр	Описание
origin_lat	стартовая широта
origin_lon	стартовая долгота
origin_h	стартовая высота
static_sec	длительность статики (сек)
bit_depth	8 или 16
nav	файл эфемерид (RINEX nav)
tx_atten_db	TX attenuation
rf_bw_mhz	RF bandwidth
Логика работы
Step 1 — Генерация NMEA

Частота фиксирована: 10 Гц

Добавляется статический префикс:

static_lines = static_sec * 10


В run.json:

"static_sec": 200,
"static_lines": 2000,
"nmea_lines": 20142,
"duration_sec": 2014.2

Step 2 — Генерация IQ

Команда:

gps-sdr-sim.exe
  -e <nav>
  -g <nmea>
  -b <8|16>
  -o gpssim_iq.bin


Особенности:

-d не передаётся (длительность берётся из NMEA)

nav копируется в sim/

запуск выполняется с cwd=sim_dir

используются относительные пути (Windows-safe)

Step 3 — PlutoPlayer

Команда:

PlutoPlayer.exe
  -t gpssim_iq.bin
  -a <tx_atten_db>
  -b <rf_bw_mhz>


Режимы:

--hold-sec N → автоматическая остановка через N секунд

без флага → ожидание завершения процесса

Ctrl+C → корректное завершение

CLI-инструменты
1️⃣ Только подготовка NMEA
python -m tools.test_gps_prepare \
  --input app\trajectory.csv \
  --origin-lat 55 \
  --origin-lon 37 \
  --origin-h 0 \
  --static-sec 200

2️⃣ Генерация IQ
python -m tools.test_gps_sim \
  --input app\trajectory.csv \
  --origin-lat 55 \
  --origin-lon 37 \
  --origin-h 0 \
  --static-sec 200 \
  --gps-sdr-sim-exe bin\gps_sdr_sim\gps-sdr-sim.exe \
  --nav data\ephemerides\brdc0430.25n \
  --bit-depth 16

3️⃣ Запуск PlutoPlayer
python -m tools.test_pluto_player \
  --run-id gps_20260219_093650_257ef8d7 \
  --tx-atten-db -20.0 \
  --rf-bw-mhz 3.0 \
  --hold-sec 20

4️⃣ Полный pipeline одной командой
python -m tools.test_full_pipeline \
  --input app\trajectory.csv \
  --origin-lat 55 \
  --origin-lon 37 \
  --origin-h 0 \
  --static-sec 200 \
  --nav data\ephemerides\brdc0430.25n \
  --bit-depth 16 \
  --hold-sec 20

run_dir структура
outputs/gps_sdr_sim/<run_id>/
  input/
    nmea_strings.txt
  sim/
    gpssim_iq.bin
    gps_sdr_sim.cmdline.txt
  pluto/
    plutoplayer.cmdline.txt
  logs/
    stdout_*.log
    stderr_*.log
  meta/
    run.json


Каждый запуск полностью воспроизводим.

Поведение при ошибках
Ошибка	Причина
ephemeris file not found	неправильный nav или cwd
Invalid duration	передан -d (не используется теперь)
rc!=0	смотреть stderr лог
Инженерные принципы

Один run_id = один полностью изолированный запуск

Все артефакты локализованы

Используются относительные пути для Windows-совместимости

Длительность берётся из NMEA

Статика рассчитывается строго как static_sec * 10

Готовность к интеграции в сервис

CLI-скрипты являются прямым прототипом:

engine.py   → подготовка данных
process.py  → запуск subprocess
service.py  → orchestration


Pipeline уже полностью проверен.