from __future__ import annotations

from typing import Any

from app.core.event_bus import EventBus
from app.core.events import ServiceStatusEvent
from app.core.logging_setup import emit_log
from app.services.base import BaseService, ServiceStatus


class ServiceManager:
    """
    v0:
      - register(service)
      - start_all(profile_config)
      - stop_all()
      - keep name->service map
      - services do not call each other directly (manager orchestrates)
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._services: dict[str, BaseService] = {}

    def register(self, service: BaseService) -> None:
        name = service.name
        if not name:
            raise ValueError("service.name must be non-empty")
        if name in self._services:
            raise ValueError(f"service already registered: {name}")

        self._services[name] = service

        emit_log(self._bus, "INFO", "services", "SERVICE_REGISTER", f"service={name}")
        # Initial snapshot
        self._bus.publish(ServiceStatusEvent(service_name=name, status=ServiceStatus.IDLE.value))

    def start_all(self, profile_config: dict[str, Any]) -> None:
        """
        profile_config: dict loaded from YAML, expected structure:
          <profile_name>:
            services:
              <service_name>: { ...service cfg... }
        """
        for name, svc in self._services.items():
            svc_cfg = self._extract_service_cfg(profile_config, name)

            emit_log(self._bus, "INFO", "services", "SERVICE_START", f"service={name}")
            # "Command accepted / starting" — actual RUNNING должен публиковать сам сервис
            self._bus.publish(ServiceStatusEvent(service_name=name, status=ServiceStatus.STARTING.value))

            try:
                svc.start(cfg=svc_cfg)
            except Exception as ex:
                emit_log(self._bus, "ERROR", "services", "SERVICE_ERROR", f"service={name} err={type(ex).__name__}")
                self._bus.publish(ServiceStatusEvent(service_name=name, status=ServiceStatus.ERROR.value))
                raise

    def stop_all(self) -> None:
        # Best-effort stop (non-blocking if services implement async stop)
        for name, svc in reversed(list(self._services.items())):
            emit_log(self._bus, "INFO", "services", "SERVICE_STOP", f"service={name}")
            # Не публикуем STOPPED тут — фактический STOPPED/ERROR публикует сам сервис
            try:
                svc.stop()
            except Exception as ex:
                emit_log(self._bus, "ERROR", "services", "SERVICE_ERROR", f"service={name} err={type(ex).__name__}")
                self._bus.publish(ServiceStatusEvent(service_name=name, status=ServiceStatus.ERROR.value))
                # continue stopping others

    def get_services(self) -> dict[str, BaseService]:
        return dict(self._services)

    def _extract_service_cfg(self, profile_config: dict[str, Any], service_name: str) -> dict[str, Any]:
        if not profile_config:
            return {}
        profile_root = next(iter(profile_config.values()), {})
        services = profile_root.get("services", {}) if isinstance(profile_root, dict) else {}
        cfg = services.get(service_name, {}) if isinstance(services, dict) else {}
        return cfg if isinstance(cfg, dict) else {}
