#!/usr/bin/env python3
"""Run configured BACnet/IP ReadProperty and WriteProperty/readback checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from bacpypes3.app import Application
from bacpypes3.argparse import SimpleArgumentParser
from bacpypes3.constructeddata import AnyAtomic


def unwrap(value: Any) -> Any:
    return value.get_value() if isinstance(value, AnyAtomic) else value


def json_value(value: Any) -> Any:
    """Convert BACpypes atomic and constructed values to JSON-safe data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "get_value"):
        return json_value(value.get_value())
    if hasattr(value, "dict_contents"):
        return json_value(value.dict_contents())
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return str(value)


def values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return math.isclose(float(actual), float(expected), rel_tol=1e-6, abs_tol=1e-6)
        except (TypeError, ValueError):
            return False
    return str(actual) == str(expected)


def load_settings(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        settings = yaml.safe_load(stream) or {}
    for key in ("device", "test_pc", "reads", "writes"):
        if key not in settings:
            raise ValueError(f"Missing required key: {key}")
    if not settings["device"].get("address"):
        raise ValueError("device.address is required")
    if not settings["test_pc"].get("address"):
        raise ValueError("test_pc.address is required")
    if not settings["test_pc"].get("device_instance"):
        raise ValueError("test_pc.device_instance is required")
    return settings


async def read_property(app: Application, address: str, object_id: str, prop: str, timeout: float) -> Any:
    result = await asyncio.wait_for(app.read_property(address, object_id, prop), timeout=timeout)
    return unwrap(result)


async def write_property(app: Application, address: str, item: dict[str, Any], timeout: float) -> None:
    priority = int(item.get("priority", 8))
    if not 1 <= priority <= 16:
        raise ValueError(f"{item.get('name', item['object_id'])}: priority must be 1..16")
    await asyncio.wait_for(
        app.write_property(address, item["object_id"], item["property"], str(item["value"]), None, priority),
        timeout=timeout,
    )


async def run(settings: dict[str, Any]) -> dict[str, Any]:
    pc = settings["test_pc"]
    local_address = str(pc["address"])
    if int(pc.get("udp_port", 47808)) != 47808:
        local_address = f"{local_address}:{int(pc['udp_port'])}"
    timeout = float(pc.get("timeout_seconds", 5))
    target = str(settings["device"]["address"])
    bacnet_args = SimpleArgumentParser().parse_args([
        "--address", local_address,
        "--instance", str(pc["device_instance"]),
        "--name", "BACnet Simple Test PC",
    ])
    app = Application.from_args(bacnet_args)
    results: list[dict[str, Any]] = []
    try:
        for item in settings["reads"]:
            entry = {"kind": "read", "name": item.get("name", item["object_id"]), **item}
            try:
                entry["actual"] = json_value(await read_property(app, target, item["object_id"], item["property"], timeout))
                entry["status"] = "passed"
            except Exception as error:
                entry.update(status="failed", error=f"{type(error).__name__}: {error}")
            results.append(entry)
        for item in settings["writes"]:
            entry = {"kind": "write_readback", "name": item.get("name", item["object_id"]), **item}
            try:
                await write_property(app, target, item, timeout)
                actual = await read_property(app, target, item["object_id"], item["property"], timeout)
                expected = item.get("expected", item["value"])
                entry.update(actual=json_value(actual), expected=json_value(expected))
                entry["status"] = "passed" if values_equal(actual, expected) else "failed"
                if entry["status"] == "failed":
                    entry["error"] = "Readback value does not match expected value"
            except Exception as error:
                entry.update(status="failed", error=f"{type(error).__name__}: {error}")
            results.append(entry)
    finally:
        app.close()
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(), "target_address": target,
        "total": len(results), "passed": sum(x["status"] == "passed" for x in results),
        "failed": sum(x["status"] == "failed" for x in results), "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/simple-bacnet-test.yaml")
    parser.add_argument("--report", default="reports/bacnet-simple-result.json")
    args = parser.parse_args()
    report_path = Path(args.report)
    try:
        result = asyncio.run(run(load_settings(Path(args.config))))
    except Exception as error:
        result = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "total": 0, "passed": 0, "failed": 1,
                  "results": [{"status": "failed", "error": f"{type(error).__name__}: {error}"}]}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in result["results"]:
        object_id = item.get("object_id")
        property_id = item.get("property")
        target = f" ({object_id} / {property_id})" if object_id and property_id else ""
        print(f"[{item['status'].upper()}] {item.get('name', 'setup')}{target}")
        if "actual" in item:
            print(f"  value: {item['actual']}")
        if "error" in item:
            print(f"  error: {item['error']}")
    print(f"Report: {report_path} ({result['passed']} passed, {result['failed']} failed)")
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
