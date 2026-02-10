#!/usr/bin/env python3
"""
RTSP H.264 video server for Raspberry Pi 5 using Picamera2 + GStreamer.

Two RTSP mounts:
  rtsp://<RPi_IP>:8554/visible   — обычная Pi-камера
  rtsp://<RPi_IP>:8554/thermal   — Infiray P2 Pro (USB /dev/videoX)
"""

import time
import os
from threading import Lock
import subprocess

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import Gst, GstRtspServer, GLib

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import Output

Gst.init(None)

# ========= CONFIG =========
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
TARGET_FPS = 15
BITRATE = 2_000_000  # 2 Mbps

HOST = "0.0.0.0"
PORT = 8554
THERMAL_PROBE_INTERVAL_SEC = 5
# ==========================


def detect_thermal_device_once() -> str | None:
    """
    Неблокирующий поиск инфракрасной камеры по выводу `v4l2-ctl --list-devices`.

    Ищем строку, содержащую "USB Camera".
    Первая /dev/video* строка сразу после неё — и есть нужное устройство.

    Возвращает:
      - путь вида "/dev/videoX" если найдено
      - None если не найдено или произошла ошибка
    """
    try:
        out = subprocess.check_output(
            ["v4l2-ctl", "--list-devices"],
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception as e:
        print(f"[THERMAL][WARN] v4l2-ctl --list-devices failed: {e}")
        return None

    lines = out.splitlines()
    for i, line in enumerate(lines):
        if "USB Camera" in line:
            for j in range(i + 1, len(lines)):
                l2 = lines[j].strip()
                if l2.startswith("/dev/video"):
                    if os.path.exists(l2):
                        print(f"[THERMAL][INFO] Detected thermal camera at {l2}")
                        return l2
                    return None
            return None

    return None



class AppSrcOutput(Output):
    def __init__(self):
        super().__init__()
        self.appsrcs = set()
        self.lock = Lock()

    def add_appsrc(self, appsrc):
        with self.lock:
            self.appsrcs.add(appsrc)

    def remove_appsrc(self, appsrc):
        with self.lock:
            self.appsrcs.discard(appsrc)

    def outputframe(self, frame, keyframe=True, timestamp=None, packet=None, audio=None):
        with self.lock:
            if not self.appsrcs:
                return

            data = frame
            if hasattr(frame, "to_bytes"):
                data = frame.to_bytes()
            elif not isinstance(frame, (bytes, bytearray, memoryview)):
                try:
                    data = bytes(frame)
                except Exception:
                    return

            buf = Gst.Buffer.new_allocate(None, len(data), None)
            buf.fill(0, data)

            if packet is not None and packet.pts is not None:
                pts = int(packet.pts)
                dts = int(packet.dts) if packet.dts is not None else pts
            else:
                pts = int(time.monotonic() * 1e9)
                dts = pts

            buf.pts = pts
            buf.dts = dts
            buf.duration = int(1e9 / TARGET_FPS)

            for i, src in enumerate(list(self.appsrcs)):
                b = buf if i == 0 else buf.copy()
                src.emit("push-buffer", b)


class CameraFactory(GstRtspServer.RTSPMediaFactory):
    """Фабрика для потока с Pi-камеры (Picamera2 → H.264 → RTSP)."""
    def __init__(self, appsrc_output: AppSrcOutput):
        super().__init__()
        self.appsrc_output = appsrc_output
        self.set_shared(True)

    def do_create_element(self, url):
        pipeline_desc = (
            "appsrc name=source is-live=true block=true format=GST_FORMAT_TIME "
            "caps=video/x-h264,stream-format=byte-stream,alignment=au,framerate={}/1 "
            "! h264parse config-interval=1 "
            "! rtph264pay name=pay0 pt=96"
        ).format(TARGET_FPS)

        pipeline = Gst.parse_launch(pipeline_desc)
        appsrc = pipeline.get_child_by_name("source")

        appsrc.set_property("do-timestamp", True)
        self.appsrc_output.add_appsrc(appsrc)

        def _on_state_changed(bus, msg):
            if msg.type == Gst.MessageType.STATE_CHANGED:
                old, new, _ = msg.parse_state_changed()
                if new == Gst.State.NULL:
                    self.appsrc_output.remove_appsrc(appsrc)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", _on_state_changed)

        return pipeline


class ThermalFactory(GstRtspServer.RTSPMediaFactory):
    """
    Фабрика для термального потока (UVC /dev/videoX).

    Требования:
    - /thermal не должен блокировать RTSP обработку, даже если камера отсутствует
    - если камера появится/пропадёт — устройство должно переобнаруживаться без перезапуска
    """

    def __init__(self):
        super().__init__()
        self.set_shared(True)

        self.device: str | None = None
        self._probe_source_id: int | None = None

        self._start_background_probe()

    def _start_background_probe(self):
        if self._probe_source_id is not None:
            return

        # Первичная быстрая проверка сразу (без sleep)
        self._probe_once()

        # Дальше — периодически (не блокирует main loop)
        self._probe_source_id = GLib.timeout_add_seconds(
            THERMAL_PROBE_INTERVAL_SEC,
            self._probe_tick,
        )

    def _probe_tick(self):
        self._probe_once()
        return True  # продолжать таймер

    def _probe_once(self):
        dev = detect_thermal_device_once()

        if dev and os.path.exists(dev):
            if dev != self.device:
                self.device = dev
                print(f"[THERMAL][INFO] Thermal device updated: {self.device}")
        else:
            if self.device is not None:
                print("[THERMAL][WARN] Thermal device disappeared; switching to placeholder")
            self.device = None

    def _build_placeholder_pipeline(self):
        print("[THERMAL][WARN] Thermal camera not found; serving black placeholder stream")
        bitrate_kbps = BITRATE // 1000
        placeholder_desc = (
            "videotestsrc is-live=true pattern=black ! "
            "video/x-raw,framerate=15/1,width=640,height=480 ! "
            "videoconvert ! "
            f"x264enc bitrate={bitrate_kbps} speed-preset=ultrafast tune=zerolatency key-int-max=25 ! "
            "rtph264pay name=pay0 pt=96"
        )
        pipeline = Gst.parse_launch(placeholder_desc)
        bus = pipeline.get_bus()
        bus.add_signal_watch()

        def on_message(bus, msg):
            if msg.type == Gst.MessageType.ERROR:
                err, dbg = msg.parse_error()
                print("[THERMAL][PLACEHOLDER][ERROR]", err, dbg)
            elif msg.type == Gst.MessageType.WARNING:
                warn, dbg = msg.parse_warning()
                print("[THERMAL][PLACEHOLDER][WARN]", warn, dbg)

        bus.connect("message", on_message)
        return pipeline

    def do_create_element(self, url):
        if not self.device:
            return self._build_placeholder_pipeline()

        bitrate_kbps = BITRATE // 1000
        print(f"[THERMAL] create_element url={url}, device={self.device}")

        # Камера даёт 256x384, две картинки вертикально.
        # Обрезаем нижние 192 пикселя — остаётся верхняя.
        # Затем красим в "heat" палитру и кодируем в H.264.
        pipeline_desc = (
            f"v4l2src device={self.device} ! "
            "videoconvert ! "
            "videocrop bottom=192 ! "
            "videoconvert ! "
            "coloreffects preset=heat ! "
            "videoconvert ! "
            f"x264enc bitrate={bitrate_kbps} "
            "speed-preset=ultrafast tune=zerolatency key-int-max=25 ! "
            "rtph264pay name=pay0 pt=96"
        )

        pipeline = Gst.parse_launch(pipeline_desc)

        bus = pipeline.get_bus()
        bus.add_signal_watch()

        def on_message(bus, msg):
            if msg.type == Gst.MessageType.ERROR:
                err, dbg = msg.parse_error()
                print("[THERMAL][ERROR]", err, dbg)
            elif msg.type == Gst.MessageType.WARNING:
                warn, dbg = msg.parse_warning()
                print("[THERMAL][WARN]", warn, dbg)

        bus.connect("message", on_message)

        return pipeline


def main():
    try:
        # --- Pi-камера (видимый поток) ---
        picam2 = Picamera2()
        cam_config = picam2.create_video_configuration(
            main={"size": (FRAME_WIDTH, FRAME_HEIGHT), "format": "YUV420"},
            controls={"FrameRate": TARGET_FPS},
            buffer_count=6,
        )
        picam2.configure(cam_config)

        encoder = H264Encoder(bitrate=BITRATE, repeat=True)
        appsrc_output = AppSrcOutput()

        # --- RTSP сервер ---
        server = GstRtspServer.RTSPServer()
        server.set_address(HOST)
        server.set_service(str(PORT))

        mounts = server.get_mount_points()
        mounts.add_factory("/visible", CameraFactory(appsrc_output))
        mounts.add_factory("/thermal", ThermalFactory())

        attach_id = server.attach(None)
        print(f"[INFO] attach_id={attach_id}")
        if attach_id == 0:
            raise RuntimeError("RTSP server attach failed (port busy?)")

        print(f"[INFO] RTSP server started on {HOST}:{PORT}")
        print(f"[INFO] Visible : rtsp://<RPi_IP>:{PORT}/visible")
        print(f"[INFO] Thermal : rtsp://<RPi_IP>:{PORT}/thermal")

        picam2.start()
        picam2.start_recording(encoder, appsrc_output)

        GLib.MainLoop().run()

    except Exception as e:
        print("[FATAL]", repr(e))
        raise


if __name__ == "__main__":
    main()
