from __future__ import annotations

import socket
import struct
import time

from app.services.mayak_spindle import MayakUdpTransport, _crc16_ones_complement_22b


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return int(port)


def _pack_d_packet(machine_size: int, index: int, value: int, name: str) -> bytes:
    name_b = name.encode("ascii", errors="ignore")[:10].ljust(10, b"\x00")
    header = struct.pack("<IIi10s", int(machine_size), int(index), int(value), name_b)
    crc = _crc16_ones_complement_22b(header)
    return header + struct.pack("<H", crc)


def test_udp_transport_write_packet_format():
    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.bind(("127.0.0.1", 0))
    recv_sock.settimeout(1.0)
    recv_port = recv_sock.getsockname()[1]

    listen_port = _free_udp_port()
    tr = MayakUdpTransport(
        cnc_host="127.0.0.1",
        cnc_port=int(recv_port),
        listen_host="127.0.0.1",
        listen_port=listen_port,
        machine_size=850592,
    )
    try:
        tr.write_cells({"D1010": 0x000F})
        data, _ = recv_sock.recvfrom(1024)
    finally:
        tr.close()
        recv_sock.close()

    assert len(data) == 24
    machine_size, index, value, name_b, crc = struct.unpack("<IIi10sH", data)
    assert machine_size == 850592
    assert index >= 20000
    assert value == 0x000F
    assert name_b.rstrip(b"\x00") == b"D1010"
    assert crc == _crc16_ones_complement_22b(data[:22])


def test_udp_transport_receives_cells():
    listen_port = _free_udp_port()
    tr = MayakUdpTransport(
        cnc_host="127.0.0.1",
        cnc_port=_free_udp_port(),
        listen_host="127.0.0.1",
        listen_port=listen_port,
    )
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        pkt = _pack_d_packet(850592, 20000, 1234, "D1091")
        tx.sendto(pkt, ("127.0.0.1", listen_port))
        time.sleep(0.05)
        vals = tr.read_cells(["D1091", "D0000"])
    finally:
        tx.close()
        tr.close()

    assert vals["D1091"] == 1234
    assert vals["D0000"] == 0
