#!/usr/bin/env python3
"""Read BACnet properties for configured object instances."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from bacpypes3.apdu import ErrorRejectAbortNack
from bacpypes3.app import Application
from bacpypes3.argparse import SimpleArgumentParser

# Properties required for every standard Object, followed by the Object-specific
# required set defined by the BACpypes3 standard object classes.
COMMON = ["object-identifier", "object-name", "object-type", "property-list", "description"]

# Alarm / Intrinsic Reporting related properties
ANALOG_ALARM = [
    "time-delay",
    "notification-class",
    "high-limit",
    "low-limit",
    "deadband",
    "limit-enable",
    "event-enable",
    "acked-transitions",
    "notify-type",
    "event-time-stamps",
]

BINARY_ALARM = [
    "time-delay",
    "notification-class",
    "alarm-value",
    "event-enable",
    "acked-transitions",
    "notify-type",
    "event-time-stamps",
]

MULTISTATE_ALARM = [
    "time-delay",
    "notification-class",
    "alarm-values",
    "fault-values",
    "event-enable",
    "acked-transitions",
    "notify-type",
    "event-time-stamps",
]

REQUIRED: dict[str, list[str]] = {
    "ai": ["present-value", "status-flags", "event-state", "out-of-service", "units", *ANALOG_ALARM],
    "ao": ["present-value", "status-flags", "event-state", "out-of-service", "units", "priority-array", "relinquish-default", "current-command-priority", *ANALOG_ALARM],
    "av": ["present-value", "status-flags", "event-state", "out-of-service", "units", *ANALOG_ALARM],
    "bi": ["present-value", "status-flags", "event-state", "out-of-service", "polarity", *BINARY_ALARM],
    "bo": ["present-value", "status-flags", "event-state", "out-of-service", "polarity", "priority-array", "relinquish-default", "current-command-priority", *BINARY_ALARM],
    "bv": ["present-value", "status-flags", "event-state", "out-of-service", *BINARY_ALARM],
    "msi": ["present-value", "status-flags", "event-state", "out-of-service", "number-of-states", *MULTISTATE_ALARM],
    "mso": ["present-value", "status-flags", "event-state", "out-of-service", "number-of-states", "priority-array", "relinquish-default", "current-command-priority", *MULTISTATE_ALARM],
    "msv": ["present-value", "status-flags", "event-state", "out-of-service", "number-of-states", *MULTISTATE_ALARM],
    "iv": ["present-value", "status-flags", "event-state", "out-of-service", "units", *ANALOG_ALARM],
    "piv": ["present-value", "status-flags", "event-state", "out-of-service", "units", *ANALOG_ALARM],
    "csv": ["present-value", "status-flags", "event-state", "out-of-service"],
    "lav": ["present-value", "status-flags", "event-state", "out-of-service", "units", *ANALOG_ALARM],
    "network_port": ["status-flags", "reliability", "out-of-service", "network-type", "protocol-level", "network-number"],
    "file": ["file-type", "file-size", "modification-date", "archive", "read-only", "file-access-method"],
    "device": ["system-status", "vendor-name", "vendor-identifier", "model-name", "firmware-revision", "application-software-version", "protocol-version", "protocol-revision", "protocol-services-supported", "protocol-object-types-supported", "object-list", "max-apdu-length-accepted", "segmentation-supported", "apdu-timeout", "number-of-apdu-retries", "device-address-binding", "database-revision"],
    "notification_class": ["notification-class", "priority", "ack-required", "recipient-list"],
    "calendar": ["present-value", "date-list"],
    "schedule": ["present-value", "effective-period", "schedule-default", "list-of-object-property-references", "priority-for-writing", "status-flags", "reliability", "out-of-service"],
    "trend_log": ["enable", "stop-when-full", "buffer-size", "log-buffer", "record-count", "total-record-count", "logging-type", "status-flags", "reliability"],
}


def to_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, ErrorRejectAbortNack):
        return str(value)
    if hasattr(value, "get_value"):
        return to_json(value.get_value())
    if hasattr(value, "dict_contents"):
        return to_json(value.dict_contents())
    if isinstance(value, dict):
        return {str(key): to_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json(item) for item in value]
    return str(value)


async def run(config: dict[str, Any]) -> dict[str, Any]:
    pc = config["test_pc"]
    local_address = str(pc["address"])
    if int(pc.get("udp_port", 47808)) != 47808:
        local_address = f"{local_address}:{int(pc['udp_port'])}"
    timeout = float(pc.get("timeout_seconds", 5))
    target = str(config["device"]["address"])
    args = SimpleArgumentParser().parse_args([
        "--address", local_address, "--instance", str(pc["device_instance"]),
        "--name", "BACnet Property Test PC",
    ])
    app = Application.from_args(args)
    results: list[dict[str, Any]] = []
    objects = config.get("objects") or config.get("mandatory_objects") or []
    try:
        for obj in objects:
            profile = obj["profile"]
            if profile not in REQUIRED:
                raise ValueError(f"Unknown profile: {profile}. Use one of: {', '.join(REQUIRED)}")
            for prop in [*COMMON, *REQUIRED[profile]]:
                entry = {"object_name": obj.get("name", obj["object_id"]), "object_id": obj["object_id"], "profile": profile, "property": prop}
                try:
                    value = await asyncio.wait_for(app.read_property(target, obj["object_id"], prop), timeout=timeout)
                    if isinstance(value, ErrorRejectAbortNack):
                        raise RuntimeError(str(value))
                    entry.update(status="passed", actual=to_json(value))
                except (Exception, ErrorRejectAbortNack) as error:
                    entry.update(status="failed", error=f"{type(error).__name__}: {error}")
                results.append(entry)
    finally:
        app.close()
    return {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "target_address": target, "total": len(results), "passed": sum(row["status"] == "passed" for row in results), "failed": sum(row["status"] == "failed" for row in results), "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/property-read.yaml")
    parser.add_argument("--report", default="reports/property-read.json")
    parser.add_argument("--html", default=None, help="Path to output HTML report (default: same name as --report with .html)")
    args = parser.parse_args()
    try:
        with Path(args.config).open(encoding="utf-8") as stream:
            config = yaml.safe_load(stream) or {}
        for key in ("device", "test_pc"):
            if key not in config:
                raise ValueError(f"Missing required key: {key}")
        if "objects" not in config and "mandatory_objects" not in config:
            raise ValueError("Missing required key in config: objects")
        result = asyncio.run(run(config))
    except (Exception, ErrorRejectAbortNack) as error:
        result = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "total": 0, "passed": 0, "failed": 1, "results": [{"status": "failed", "error": f"{type(error).__name__}: {error}"}]}
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # Generate HTML report
    html_path = Path(args.html) if args.html else report.with_suffix(".html")
    try:
        from html_reporter import generate_html_report
        generate_html_report(result, html_path, report_type="read")
    except Exception as html_err:
        print(f"Warning: Failed to generate HTML report: {html_err}", file=sys.stderr)

    for row in result["results"]:
        label = f"{row.get('object_id', 'setup')} / {row.get('property', '')}".rstrip(" / ")
        if "actual" in row:
            actual = row["actual"]
            val_str = json.dumps(actual, ensure_ascii=False) if isinstance(actual, (dict, list)) else str(actual)
            if len(val_str) > 80:
                print(f"[{row['status'].upper()}] {label}")
                print(f"  value: {val_str}")
            else:
                print(f"[{row['status'].upper()}] {label} = {val_str}")
        else:
            print(f"[{row['status'].upper()}] {label}")
        if "error" in row:
            print(f"  error: {row['error']}")
    print(f"\nReport JSON: {report}")
    print(f"Report HTML: {html_path} ({result['passed']} passed, {result['failed']} failed)\n")
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
