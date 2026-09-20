#!/usr/bin/env python3
"""Read BACnet properties for configured object instances."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
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

# Binary Output has COMMAND_FAILURE reporting (feedback-value vs present-value) and no alarm-value
BINARY_OUTPUT_ALARM = [
    "time-delay",
    "notification-class",
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
    "bo": ["present-value", "status-flags", "event-state", "out-of-service", "polarity", "priority-array", "relinquish-default", "current-command-priority", *BINARY_OUTPUT_ALARM],
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
    "trend_log": ["enable", "stop-when-full", "buffer-size", "record-count", "total-record-count", "logging-type", "status-flags", "reliability"],
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


async def send_object_marker(
    app: Application,
    target: str,
    obj_id: str,
    obj_name: str,
    local_instance: int,
    index: int = 0,
    total: int = 0,
) -> None:
    """Send an UnconfirmedTextMessageRequest as an object delimiter marker for Wireshark."""
    try:
        from bacpypes3.apdu import UnconfirmedTextMessageRequest
        from bacpypes3.basetypes import UnconfirmedTextMessageRequestMessagePriority
        from bacpypes3.pdu import Address
        from bacpypes3.primitivedata import CharacterString, ObjectIdentifier

        msg_str = f"=== [{index}/{total}] Object: {obj_id} ({obj_name}) ===" if total else f"=== Object: {obj_id} ({obj_name}) ==="
        src_dev = getattr(app.device_object, "objectIdentifier", None) or ObjectIdentifier(("device", int(local_instance)))

        req = UnconfirmedTextMessageRequest(
            textMessageSourceDevice=src_dev,
            messagePriority=UnconfirmedTextMessageRequestMessagePriority.normal,
            message=CharacterString(msg_str),
        )
        req.pduDestination = Address(target)
        app.request(req)
        await asyncio.sleep(0.02)
    except Exception:
        pass


async def run_read_tests(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Configuration file not found: {config_path}", file=sys.stderr)
        return 1

    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}

    for key in ("device", "test_pc", "objects"):
        if key not in config:
            raise ValueError(f"Missing required key in config: {key}")

    pc = config["test_pc"]
    local_address = str(pc["address"])
    udp_port = int(pc.get("udp_port", 47808))
    if udp_port != 47808 and ":" not in local_address:
        local_address = f"{local_address}:{udp_port}"
    timeout = float(pc.get("timeout_seconds", 5))
    target = str(config["device"]["address"])

    # Collect objects
    all_objects = config.get("objects", [])
    objects = all_objects

    if args.object_id:
        objects = [o for o in objects if o["object_id"] == args.object_id]
        if not objects:
            print(f"Error: Specified object-id '{args.object_id}' not found in configuration.", file=sys.stderr)
            return 1

    if args.first_per_type:
        seen = set()
        filtered = []
        for o in objects:
            if o["profile"] not in seen:
                seen.add(o["profile"])
                filtered.append(o)
        objects = filtered

    print(f"[*] Target Device: {target}")
    print(f"[*] Local PC: {local_address} (instance: {pc['device_instance']})")
    print(f"[*] Objects to test: {len(objects)}")

    app_args = SimpleArgumentParser().parse_args([
        "--address", local_address,
        "--instance", str(pc["device_instance"]),
        "--name", "BACnet Property Read Test PC",
    ])
    app = Application.from_args(app_args)

    results: list[dict[str, Any]] = []

    try:
        for obj_idx, obj in enumerate(objects, 1):
            profile = obj["profile"]
            if profile not in REQUIRED:
                print(f"[WARNING] Unknown profile: '{profile}' for {obj['object_id']}, skipping.", file=sys.stderr)
                continue

            obj_id = obj["object_id"]
            obj_name = obj.get("name", obj_id)

            # Send UnconfirmedTextMessage marker packet for Wireshark analysis
            await send_object_marker(
                app=app,
                target=target,
                obj_id=obj_id,
                obj_name=obj_name,
                local_instance=int(pc.get("device_instance", 900001)),
                index=obj_idx,
                total=len(objects),
            )

            props_for_obj = [*COMMON, *REQUIRED[profile]]
            if args.property_name:
                props_for_obj = [p for p in props_for_obj if p == args.property_name]

            for prop in props_for_obj:
                # Exclude properties per requirement
                if profile == "bo" and prop == "alarm-value":
                    continue
                if profile in ("trend_log", "tl") and prop == "log-buffer":
                    continue

                label = f"{obj_id} / {prop}"

                entry: dict[str, Any] = {
                    "object_name": obj_name,
                    "object_id": obj_id,
                    "profile": profile,
                    "property": prop,
                }

                try:
                    raw_val = await asyncio.wait_for(app.read_property(target, obj_id, prop), timeout=timeout)
                    if isinstance(raw_val, ErrorRejectAbortNack):
                        raise RuntimeError(str(raw_val))
                    val_json = to_json(raw_val)
                    entry.update(status="passed", actual=val_json)

                    # Real-time console output
                    val_str = json.dumps(val_json, ensure_ascii=False) if isinstance(val_json, (dict, list)) else str(val_json)
                    if len(val_str) > 70:
                        print(f"[PASSED]  {label}")
                        print(f"  value: {val_str}")
                    else:
                        print(f"[PASSED]  {label} = {val_str}")

                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException as error:
                    err_str = str(error)
                    entry.update(status="failed", error=f"{type(error).__name__}: {err_str}")
                    print(f"[FAILED]    {label} -> {type(error).__name__}: {err_str}")

                results.append(entry)

    except KeyboardInterrupt:
        print("\n[!] Read test interrupted by user (Ctrl+C). Saving partial results...", file=sys.stderr)
    finally:
        app.close()

        # Always save JSON and HTML reports even if interrupted
        passed_count = sum(r.get("status") == "passed" for r in results)
        failed_count = sum(r.get("status") == "failed" for r in results)

        report_data = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "target_address": target,
            "total": len(results),
            "passed": passed_count,
            "failed": failed_count,
            "results": results,
        }

        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")

        html_path = Path(args.html) if args.html else report_path.with_suffix(".html")
        try:
            from html_reporter import generate_html_report
            generate_html_report(report_data, html_path, report_type="read")
        except Exception as html_err:
            print(f"Warning: Failed to generate HTML report: {html_err}", file=sys.stderr)

        print("\n=================================================================")
        print(f"Read Test Finished. Results:")
        print(f"  - Total Tested: {len(results)}")
        print(f"  - Passed: {passed_count}")
        print(f"  - Failed: {failed_count}")
        print(f"Report JSON saved to: {report_path.resolve()}")
        print(f"Report HTML saved to: {html_path.resolve()}")
        print("=================================================================\n")

    return 0 if failed_count == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", "-c",
        default="config/property-read.yaml",
        help="Path to YAML configuration (default: config/property-read.yaml)",
    )
    parser.add_argument(
        "--report", "-r",
        default="reports/property-read.json",
        help="Path to output JSON report (default: reports/property-read.json)",
    )
    parser.add_argument(
        "--html",
        default=None,
        help="Path to output HTML report (default: same name as --report with .html)",
    )
    parser.add_argument(
        "--first-per-type",
        action="store_true",
        help="Test only 1 object per profile type for quick sampling",
    )
    parser.add_argument(
        "--object-id", "-o",
        default=None,
        help="Test a single object ID only (e.g. analog-value,1)",
    )
    parser.add_argument(
        "--property", "-p",
        dest="property_name",
        default=None,
        help="Test a single property only (e.g. present-value)",
    )
    args = parser.parse_args()
    return asyncio.run(run_read_tests(args))


if __name__ == "__main__":
    raise SystemExit(main())
