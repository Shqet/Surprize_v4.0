from __future__ import annotations
"""
Эмулятор «Маяка», совместимый с клиентом, который работает с UDP
«D-пакетами» формата <IIi10sH> (24 байта).

- Рассылает значения D-ячейек (скорости, статусы, моменты, угол и т.д.)
- Принимает команды управления (ControlWord, TargetSpeed, Global_Enable)

Обновления (DSP402-ближе к реальности):
- StatusWord: bits 0/1/2 = ready_to_switch_on / switched_on / operation_enabled, bit3 = fault
- Fault reset: controlword bit7 (0x80)
- Вращение (ActualSpeed) только при operation_enabled
- ModeDisplay (6061) добавлен как IN-константа (3 = velocity) для каждого шпинделя
"""

import argparse
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Dict


# =====================
# Карта D-ячей (можно править номера)
# =====================
D_MAP = {
    # Spindle 1 (стабилизирующий)
    "SP1_ControlWord": "D1000",
    "SP1_TargetSpeed": "D1001",
    "SP1_StatusWord":  "D1002",
    "SP1_ActualSpeed": "D1003",

    # NEW: read-only mode display (6061), фиксированный velocity=3
    "SP1_ModeDisplay": "D1004",

    # Spindle 2 (вращающий)
    "SP2_ControlWord": "D1010",
    "SP2_TargetSpeed": "D1011",
    "SP2_StatusWord":  "D1012",
    "SP2_ActualSpeed": "D1013",

    # NEW: read-only mode display (6061), фиксированный velocity=3
    "SP2_ModeDisplay": "D1014",

    # NEW: текущие моменты шпинделей
    "SP1_ActualTorque": "D1020",   # момент стабилизирующего
    "SP2_ActualTorque": "D1021",   # момент вращающего

    # NEW: угол стабилизирующего шпинделя
    "SP1_Angle":        "D1022",   # 0–359 градусов

    # Флаги «шпиндель подключен»
    "SP1_Connected":  "D1050",   # IN
    "SP2_Connected":  "D1051",   # IN

    # Service
    "Global_Enable":   "D1090",  # OUT
    "Sim_Time":        "D1091",  # IN (мс от старта эмулятора)
    "Error_Code":      "D1092",  # IN (глобальная ошибка эмулятора, если понадобится)
}


# Направление обмена (OUT — пишет клиент, IN — читает клиент)
D_DIR = {
    # Spindle 1
    D_MAP["SP1_ControlWord"]: "OUT",
    D_MAP["SP1_TargetSpeed"]: "OUT",
    D_MAP["SP1_StatusWord"]:  "IN",
    D_MAP["SP1_ActualSpeed"]: "IN",
    D_MAP["SP1_ModeDisplay"]: "IN",

    # Spindle 2
    D_MAP["SP2_ControlWord"]: "OUT",
    D_MAP["SP2_TargetSpeed"]: "OUT",
    D_MAP["SP2_StatusWord"]:  "IN",
    D_MAP["SP2_ActualSpeed"]: "IN",
    D_MAP["SP2_ModeDisplay"]: "IN",

    # NEW — моменты шпинделей и угол: только IN (только эмулятор пишет)
    D_MAP["SP1_ActualTorque"]: "IN",
    D_MAP["SP2_ActualTorque"]: "IN",
    D_MAP["SP1_Angle"]:        "IN",

    # Флаги «подключен»
    D_MAP["SP1_Connected"]:   "IN",
    D_MAP["SP2_Connected"]:   "IN",

    # Service
    D_MAP["Global_Enable"]:   "OUT",
    D_MAP["Sim_Time"]:        "IN",
    D_MAP["Error_Code"]:      "IN",
}


# Список всех публикуемых имён D (порядок рассылки)
D_NAMES = [
    # SP1
    D_MAP["SP1_ControlWord"], D_MAP["SP1_TargetSpeed"],
    D_MAP["SP1_StatusWord"],  D_MAP["SP1_ActualSpeed"],
    D_MAP["SP1_ModeDisplay"],

    # SP2
    D_MAP["SP2_ControlWord"], D_MAP["SP2_TargetSpeed"],
    D_MAP["SP2_StatusWord"],  D_MAP["SP2_ActualSpeed"],
    D_MAP["SP2_ModeDisplay"],

    # NEW — моменты и угол
    D_MAP["SP1_ActualTorque"],
    D_MAP["SP2_ActualTorque"],
    D_MAP["SP1_Angle"],

    # Connected flags
    D_MAP["SP1_Connected"],   D_MAP["SP2_Connected"],

    # Service
    D_MAP["Global_Enable"],   D_MAP["Sim_Time"], D_MAP["Error_Code"],
]


# Фиксированные index для имитируемых ячеек
INDEX_MAP: Dict[str, int] = {name: 20000 + i * 4 for i, name in enumerate(D_NAMES)}

# Начальные значения
INITIAL_D = {name: 0 for name in D_NAMES}
INITIAL_D[D_MAP["SP1_Connected"]] = 1
INITIAL_D[D_MAP["SP2_Connected"]] = 1
INITIAL_D[D_MAP["SP1_Angle"]] = 0  # стартовый угол

# фиксированный режим: velocity = 3
INITIAL_D[D_MAP["SP1_ModeDisplay"]] = 3
INITIAL_D[D_MAP["SP2_ModeDisplay"]] = 3


# =====================
# Вспомогательная логика
# =====================

def crc16_ones_complement_22b(first_22: bytes) -> int:
    """
    Простейший «IP-style» one's complement CRC по 22 байтам.
    """
    if len(first_22) != 22:
        raise ValueError("CRC computed over 22 bytes")
    total = 0
    for i in range(0, 22, 2):
        word = first_22[i] | (first_22[i + 1] << 8)
        total += word
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def pack_d_packet(ms: int, index: int, value: int, name: str) -> bytes:
    """
    Упаковка D-пакета <IIi10sH>:
      ms    – machine_size / идентификатор машины
      index – индекс D-ячейки (напр. 20000 + i*4)
      value – int32 значение
      name  – ASCII-имя (до 10 байт)
      crc   – 16-битный crc по первым 22 байтам
    """
    name_b = name.encode("ascii", errors="ignore")[:10].ljust(10, b"\x00")
    header = struct.pack("<IIi10s", ms, index, int(value), name_b)
    crc = crc16_ones_complement_22b(header)
    return header + struct.pack("<H", crc)


@dataclass
class SpindleModel:
    ctrl: str
    tgt: str
    stat: str
    act: str
    mode_disp: str

    ramp_rpm_per_s: float = 6000.0
    tick_s: float = 0.02

    # внутреннее состояние DSP402:
    # 0 = not ready
    # 1 = ready to switch on
    # 2 = switched on
    # 3 = operation enabled
    state: int = 0
    fault: bool = False

    # типовые controlword команды
    CW_SHUTDOWN: int = 0x0006
    CW_SWITCH_ON: int = 0x0007
    CW_ENABLE_OP: int = 0x000F
    CW_FAULT_RESET: int = 0x0080

    MODE_VELOCITY: int = 3  # константа (как в твоём стенде)

    def step(self, d: Dict[str, int], global_enable: bool):
        cw = int(d[self.ctrl])
        tgt = float(d[self.tgt])
        act = float(d[self.act])

        # поддерживаем mode display как константу
        d[self.mode_disp] = self.MODE_VELOCITY

        # если глобально выключено — привод "не готов"
        if not global_enable:
            self.state = 0
            # мягкий выход в 0 скорости
            tgt = 0.0
        else:
            # fault reset (бит 7)
            if (cw & self.CW_FAULT_RESET) != 0:
                self.fault = False
                # после reset обычно возвращаемся к ready
                self.state = 1

            # если fault активен — держим fault состояние
            if self.fault:
                self.state = 0  # для простоты: fault не даёт быть ready/switched/enabled
            else:
                # простая обработка state machine по cw
                if cw == self.CW_SHUTDOWN:
                    self.state = 1
                elif cw == self.CW_SWITCH_ON and self.state >= 1:
                    self.state = 2
                elif cw == self.CW_ENABLE_OP and self.state >= 2:
                    self.state = 3
                elif cw == 0:
                    self.state = 0

        # вращаемся только если operation enabled
        op_enabled = (self.state == 3) and (not self.fault) and global_enable
        desired = tgt if op_enabled else 0.0

        # ограничение на изменение скорости (ramp)
        max_delta = self.ramp_rpm_per_s * self.tick_s
        delta = max(-max_delta, min(max_delta, desired - act))
        act += delta
        d[self.act] = int(round(act))

        # statusword: биты 0/1/2 по состояниям, бит 3 fault
        sw = 0
        if global_enable and not self.fault:
            if self.state >= 1:
                sw |= 0x0001  # ready to switch on
            if self.state >= 2:
                sw |= 0x0002  # switched on
            if self.state >= 3:
                sw |= 0x0004  # operation enabled
        if self.fault:
            sw |= 0x0008  # fault
        d[self.stat] = int(sw)


@dataclass
class MayakUdpEmulator:
    bind_host: str = "0.0.0.0"     # где слушаем команды от клиента
    bind_port: int = 12346         # ДОЛЖЕН совпадать с cnc_port UdpDClient
    target_host: str = "127.0.0.1" # куда шлём телеметрию (клиент слушает)
    target_port: int = 12345       # ДОЛЖЕН совпадать с listen_port UdpDClient
    machine_size: int = 850592
    tx_interval_s: float = 0.01    # период рассылки D-пакетов

    def __post_init__(self):
        self.d = dict(INITIAL_D)
        self.start = time.perf_counter()
        self._stop = threading.Event()
        self._lock = threading.Lock()

        self.sock_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_rx.bind((self.bind_host, self.bind_port))
        self.sock_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # модели шпинделей
        self.sp1 = SpindleModel(
            ctrl=D_MAP["SP1_ControlWord"],
            tgt=D_MAP["SP1_TargetSpeed"],
            stat=D_MAP["SP1_StatusWord"],
            act=D_MAP["SP1_ActualSpeed"],
            mode_disp=D_MAP["SP1_ModeDisplay"],
        )
        self.sp2 = SpindleModel(
            ctrl=D_MAP["SP2_ControlWord"],
            tgt=D_MAP["SP2_TargetSpeed"],
            stat=D_MAP["SP2_StatusWord"],
            act=D_MAP["SP2_ActualSpeed"],
            mode_disp=D_MAP["SP2_ModeDisplay"],
        )

    # ---- loops ----
    def serve(self):
        print(f"[EMU] RX on {self.bind_host}:{self.bind_port} -> "
              f"TX to {self.target_host}:{self.target_port}")
        t_rx = threading.Thread(target=self._loop_rx, daemon=True)
        t_tx = threading.Thread(target=self._loop_tx, daemon=True)
        t_md = threading.Thread(target=self._loop_model, daemon=True)
        t_rx.start()
        t_tx.start()
        t_md.start()
        try:
            while not self._stop.is_set():
                time.sleep(0.2)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        self._stop.set()
        try:
            self.sock_rx.close()
        except Exception:
            pass

    def _loop_model(self):
        """
        Локальная модель «железа»:
        - обновляет Sim_Time
        - шагает модели шпинделей
        - рассчитывает момент и угол
        """
        step = min(self.sp1.tick_s, self.sp2.tick_s)
        prev_sp1 = 0.0
        prev_sp2 = 0.0

        while not self._stop.is_set():
            time.sleep(step)
            with self._lock:
                # время с запуска эмулятора (мс)
                self.d[D_MAP["Sim_Time"]] = int((time.perf_counter() - self.start) * 1000)

                ge = bool(self.d[D_MAP["Global_Enable"]])

                # шаг моделей шпинделей
                self.sp1.step(self.d, ge)
                self.sp2.step(self.d, ge)

                sp1_rpm = float(self.d[D_MAP["SP1_ActualSpeed"]])
                sp2_rpm = float(self.d[D_MAP["SP2_ActualSpeed"]])

                # torque: пусть зависит от ускорения + чуть от скорости
                sp1_acc = (sp1_rpm - prev_sp1) / step
                sp2_acc = (sp2_rpm - prev_sp2) / step
                prev_sp1, prev_sp2 = sp1_rpm, sp2_rpm

                self.d[D_MAP["SP1_ActualTorque"]] = int(abs(sp1_acc) * 0.001 + abs(sp1_rpm) * 0.01)
                self.d[D_MAP["SP2_ActualTorque"]] = int(abs(sp2_acc) * 0.001 + abs(sp2_rpm) * 0.01)

                # угол стабилизирующего: пусть зависит от скорости (rpm -> deg/s)
                angle = float(self.d.get(D_MAP["SP1_Angle"], 0))
                deg_per_s = (sp1_rpm / 60.0) * 360.0
                angle = (angle + deg_per_s * step) % 360.0
                self.d[D_MAP["SP1_Angle"]] = int(angle)

                # глобальный error_code пока 0 (можно потом использовать)
                self.d[D_MAP["Error_Code"]] = 0

    def _loop_tx(self):
        """
        Периодически шлём все D-ячейки в виде пачки UDP-пакетов.
        """
        while not self._stop.is_set():
            time.sleep(self.tx_interval_s)
            with self._lock:
                ms = self.machine_size
                for name in D_NAMES:
                    idx = INDEX_MAP[name]
                    val = int(self.d.get(name, 0))
                    pkt = pack_d_packet(ms, idx, val, name)
                    try:
                        self.sock_tx.sendto(pkt, (self.target_host, self.target_port))
                    except OSError:
                        # иногда может вылетать при закрытии сокета
                        pass

    def _loop_rx(self):
        """
        Слушаем команды от клиента, который пишет OUT-ячейки.
        """
        while not self._stop.is_set():
            try:
                data, addr = self.sock_rx.recvfrom(1024)
            except OSError:
                break
            if len(data) != 24:
                continue
            try:
                ms, index, value, name_b, crc = struct.unpack("<IIi10sH", data)
            except struct.error:
                continue

            # проверка CRC
            if crc != crc16_ones_complement_22b(data[:22]):
                continue

            name = name_b.rstrip(b"\x00").decode("ascii", errors="ignore")
            # defensive — ищем по имени
            if name not in D_DIR:
                continue
            if D_DIR[name] != "OUT":
                # писать разрешено только в OUT-ячейки
                continue

            with self._lock:
                self.d[name] = int(value)


# =====================
# CLI
# =====================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="UDP-эмулятор Маяка (D-ячейки).")
    p.add_argument(
        "--bind-host",
        default="0.0.0.0",
        help="Адрес для приёма команд (по умолчанию 0.0.0.0)",
    )
    p.add_argument(
        "--bind-port",
        type=int,
        default=12346,  # должен совпадать с cnc_port UdpDClient
        help="Порт для приёма команд (по умолчанию 12346)",
    )
    p.add_argument(
        "--target-host",
        default="127.0.0.1",
        help="Куда слать телеметрию (по умолчанию 127.0.0.1)",
    )
    p.add_argument(
        "--target-port",
        type=int,
        default=12345,  # должен совпадать с listen_port UdpDClient
        help="Порт для телеметрии (по умолчанию 12345)",
    )
    p.add_argument(
        "--tx-interval",
        type=float,
        default=0.01,
        help="Период рассылки D-данных, сек",
    )
    return p.parse_args()


def main():
    args = parse_args()
    emu = MayakUdpEmulator(
        bind_host=args.bind_host,
        bind_port=args.bind_port,
        target_host=args.target_host,
        target_port=args.target_port,
        tx_interval_s=args.tx_interval,
    )
    emu.serve()


if __name__ == "__main__":
    main()