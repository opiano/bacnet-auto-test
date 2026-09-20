"""Validated configuration loading for the BACnet regression tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class NetworkConfig:
    local_address: str
    local_device_instance: int
    udp_port: int
    timeout_seconds: float


@dataclass(frozen=True)
class ControllerConfig:
    address: str
    device_instance: int
    expected: dict[str, Any]


@dataclass(frozen=True)
class RequiredObject:
    name: str
    object_identifier: str
    property_identifier: str
    expected: Any = None
    has_expected: bool = False


@dataclass(frozen=True)
class SafeWriteConfig:
    enabled: bool
    object_identifier: str | None
    property_identifier: str = "present-value"
    value: Any = None
    priority: int = 8
    expected_readback: Any = None
    relinquish_after_test: bool = False


@dataclass(frozen=True)
class TestConfig:
    network: NetworkConfig
    controller: ControllerConfig
    required_objects: list[RequiredObject]
    safe_write: SafeWriteConfig


def _required(mapping: dict[str, Any], key: str) -> Any:
    if key not in mapping:
        raise ValueError(f"Missing required configuration key: {key}")
    return mapping[key]


def load_config(path: str | Path) -> TestConfig:
    """Load test settings and reject unsafe/incomplete values early."""
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream) or {}

    network_raw = _required(raw, "network")
    controller_raw = _required(raw, "controller")
    network = NetworkConfig(
        local_address=str(_required(network_raw, "local_address")),
        local_device_instance=int(_required(network_raw, "local_device_instance")),
        udp_port=int(network_raw.get("udp_port", 47808)),
        timeout_seconds=float(network_raw.get("timeout_seconds", 5)),
    )
    controller = ControllerConfig(
        address=str(_required(controller_raw, "address")),
        device_instance=int(_required(controller_raw, "device_instance")),
        expected=dict(controller_raw.get("expected", {})),
    )
    if network.local_device_instance == controller.device_instance:
        raise ValueError("Test PC and controller Device Instance values must differ")
    if not 0 < network.timeout_seconds <= 60:
        raise ValueError("network.timeout_seconds must be greater than 0 and no more than 60")

    required_objects = []
    for item in raw.get("required_objects", []):
        required_objects.append(
            RequiredObject(
                name=str(_required(item, "name")),
                object_identifier=str(_required(item, "object_identifier")),
                property_identifier=str(_required(item, "property_identifier")),
                expected=item.get("expected"),
                has_expected="expected" in item,
            )
        )

    write_raw = raw.get("safe_write", {})
    safe_write = SafeWriteConfig(
        enabled=bool(write_raw.get("enabled", False)),
        object_identifier=write_raw.get("object_identifier"),
        property_identifier=str(write_raw.get("property_identifier", "present-value")),
        value=write_raw.get("value"),
        priority=int(write_raw.get("priority", 8)),
        expected_readback=write_raw.get("expected_readback"),
        relinquish_after_test=bool(write_raw.get("relinquish_after_test", False)),
    )
    if safe_write.enabled and not safe_write.object_identifier:
        raise ValueError("safe_write.object_identifier is required when writes are enabled")
    if not 1 <= safe_write.priority <= 16:
        raise ValueError("safe_write.priority must be between 1 and 16")

    return TestConfig(network, controller, required_objects, safe_write)

