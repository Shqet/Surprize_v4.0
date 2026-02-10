# RTSP Video Server (Visible + Thermal)

RTSP-сервер на Python (GStreamer + GstRtspServer) для Raspberry Pi 5,  
предназначенный для трансляции:

- **Visible camera** (Picamera2 → H.264 → RTSP)
- **Thermal camera** (USB V4L2 → H.264 → RTSP)

Сервер ориентирован на:
- низкую задержку;
- поддержку нескольких клиентов;
- стабильную работу без блокировок;
- корректный lifecycle RTSP media.

---

## 1. Архитектура

### 1.1 RTSP mounts

| Mount       | Назначение           | Источник      |
|------------|-----------------------|---------------|
| `/visible` | Обычная камера        | Picamera2    |
| `/thermal` | Тепловизор (USB V4L2) | `/dev/videoX`|

RTSP сервер слушает порт **8554**:

```text
rtsp://<IP>:8554/visible
rtsp://<IP>:8554/thermal
```

---

## 2. Ключевые архитектурные решения

### 2.1 Shared Media

Оба `RTSPMediaFactory` используют:

```python
self.set_shared(True)
```

Это означает:
- один pipeline на mount;
- fan-out на нескольких клиентов;
- экономия CPU и encoder ресурсов.

---

### 2.2 Visible pipeline (Picamera2 → appsrc)

Visible камера:
- управляется Picamera2;
- кадры подаются в GStreamer через `appsrc`;
- `appsrc` жёстко привязан к lifecycle RTSP media.

Важно:
- UI / клиенты не влияют на работу камеры напрямую;
- `appsrc` создаётся и уничтожается строго при подключении/отключении клиента.

---

### 2.3 Lifecycle appsrc (Шаг 4)

`appsrc`:
- добавляется в `self.appsrcs` в `do_configure(media)`;
- удаляется при событии:

```python
media.connect("unprepared", ...)
```

Это гарантирует:
- отсутствие утечек `appsrc`;
- корректную работу при десятках reconnect;
- отсутствие «висячих» источников при shared media.

Bus watcher используется **только как fallback**, не как основной механизм.

---

### 2.4 Thermal camera detection (non-blocking)

Поиск тепловизора:
- выполняется неблокирующе;
- через `v4l2-ctl --list-devices`;
- без `sleep()` и без ожиданий в RTSP threads.

Если камера отсутствует:
- `/thermal` быстро отказывает;
- RTSP сервер и `/visible` продолжают работать штатно.

---

## 3. Конфигурация (Шаг 6)

Настройки **разделены для каждого mount**.

### 3.1 Visible

```python
VISIBLE_FPS
VISIBLE_BITRATE
```

Используются только для:
- Picamera2;
- H.264 encoder visible pipeline.

---

### 3.2 Thermal

```python
THERMAL_FPS
THERMAL_BITRATE
```

Используются только для:
- `v4l2src`;
- thermal H.264 encoder.

Изменения thermal **не влияют** на visible и наоборот.

---

## 4. Логирование

При старте сервера выводится:
- параметры visible:
  - FPS
  - bitrate
- параметры thermal:
  - FPS
  - bitrate
- сообщения обнаружения/пропадания thermal устройства;
- события attach/detach `appsrc`.

Логи намеренно короткие, без dump’ов pipeline строк.

---

## 5. Проверка работы

### 5.1 Запуск сервера

```bash
python3 video_rtsp_server.py
```

### 5.2 Проверка visible

```bash
vlc rtsp://<IP>:8554/visible
```

### 5.3 Проверка thermal

```bash
vlc rtsp://<IP>:8554/thermal
```

---

## 6. Ограничения и осознанные упрощения

- Thermal pipeline **не использует input-selector**  
  (переключение без reconnect сознательно отложено)
- Caps thermal камеры **не фиксируются жёстко**  
  (из-за различий V4L2 устройств)
- RTSP клиент (VLC) **должен переподключаться**, если thermal камера появилась позже

Эти решения приняты ради:
- стабильности;
- предсказуемости;
- отсутствия скрытых deadlock’ов.

---

## 7. Возможные будущие улучшения

- input-selector для thermal (auto-switch без reconnect);
- авто-детект thermal caps через `v4l2-ctl --list-formats-ext`;
- systemd service + healthcheck;
- RTSP auth;
- Prometheus metrics.

---

## 8. Статус

Текущая версия:
- стабильна;
- воспроизводима;
- предназначена для эксплуатации и дальнейшего развития.

Шаги **1–4 и 6** считаются архитектурно завершёнными.
