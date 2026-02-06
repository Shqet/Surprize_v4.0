from __future__ import annotations

from typing import Any, Dict

from app.core.event_bus import EventBus
from app.core.events import ServiceStatusEvent
from app.core.logging_setup import emit_log
from app.services.base import ServiceStatus


class ServiceManager:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._services: Dict[str, Any] = {}

    # needed by Orchestrator v1/v2
    def get_services(self) -> Dict[str, Any]:
        return self._services

    def register(self, service: Any) -> None:
        self._services[service.name] = service
        emit_log(self._bus, "INFO", "services", "SERVICE_REGISTER", f"service={service.name}")
        # initial status event (contract)
        self._bus.publish(ServiceStatusEvent(service_name=service.name, status=ServiceStatus.IDLE.value))

    def start_all(self, profile_config: dict) -> None:
        """
        Accepts either:
          - full profile dict: {"default": {...}}
          - already selected section: {"services": {...}, "orchestrator": {...}}
        """
        default_section = profile_config.get("default") if isinstance(profile_config, dict) else None
        if isinstance(default_section, dict):
            cfg = default_section
        elif isinstance(profile_config, dict):
            cfg = profile_config
        else:
            cfg = {}

        services_cfg = cfg.get("services", {}) if isinstance(cfg, dict) else {}
        if not isinstance(services_cfg, dict):
            services_cfg = {}

        # deterministic order
        for name in sorted(self._services.keys()):
            svc = self._services[name]
            emit_log(self._bus, "INFO", "services", "SERVICE_START", f"service={name}")

            section = services_cfg.get(name, {})
            if section is None or not isinstance(section, dict):
                section = {}

            try:
                # primary: pass section as positional (most of your services)
                svc.start(section)
            except TypeError:
                # fallback: kw-only start(profile_section=...)
                svc.start(profile_section=section)
            except Exception as ex:
                emit_log(self._bus, "ERROR", "services", "SERVICE_ERROR", f"service={name} err={type(ex).__name__}")

    def stop_all(self) -> None:
        for name in sorted(self._services.keys()):
            svc = self._services[name]
            emit_log(self._bus, "INFO", "services", "SERVICE_STOP", f"service={name}")
            try:
                svc.stop()
            except Exception as ex:
                emit_log(self._bus, "ERROR", "services", "SERVICE_ERROR", f"service={name} err={type(ex).__name__}")
