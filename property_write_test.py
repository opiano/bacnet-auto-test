#!/usr/bin/env python3
"""Execute WriteProperty and readback verification tests on BACnet objects."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from bacpypes3.apdu import ErrorRejectAbortNack
from bacpypes3.app import Application
from bacpypes3.argparse import SimpleArgumentParser

# Properties to test for writeability across all objects
COMMON_WRITE_PROPERTIES = ["object-name", "description"]

# Profile-specific properties to test for writing
TARGET_PROPERTIES_BY_PROFILE: dict[str, list[str]] = {
    "ai": [
        "high-limit", "low-limit", "deadband", "time-delay",
        "cov-increment", "units", "out-of-service", "present-value",
    ],
    "ao": [
        "high-limit", "low-limit", "deadband", "time-delay",
        "cov-increment", "units", "out-of-service", "present-value",
    ],
    "av": [
        "high-limit", "low-limit", "deadband", "time-delay",
        "cov-increment", "units", "out-of-service", "present-value",
    ],
    "iv": [
        "high-limit", "low-limit", "deadband", "time-delay",
        "cov-increment", "units", "out-of-service", "present-value",
    ],
    "piv": [
        "high-limit", "low-limit", "deadband", "time-delay",
        "cov-increment", "units", "out-of-service", "present-value",
    ],
    "lav": [
        "high-limit", "low-limit", "deadband", "time-delay",
        "cov-increment", "units", "out-of-service", "present-value",
    ],
    "bi": [
        "active-text", "inactive-text", "time-delay", "polarity",
        "out-of-service", "present-value",
    ],
    "bo": [
        "active-text", "inactive-text", "time-delay", "polarity",
        "out-of-service", "present-value",
    ],
    "bv": [
        "active-text", "inactive-text", "time-delay",
        "out-of-service", "present-value",
    ],
    "msv": [
        "state-text", "time-delay", "present-value",
    ],
    "msi": [
        "state-text", "time-delay", "present-value",
    ],
    "mso": [
        "state-text", "time-delay", "present-value",
    ],
    "device": [],
    "file": [],
    "network_port": [],
    "notification_class": [],
    "calendar": [],
    "schedule": [],
    "trend_log": [],
}


def to_json(value: Any) -> Any:
    """Convert BACpypes objects to JSON-serializable types."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, ErrorRejectAbortNack):
        return str(value)
    if hasattr(value, "get_value"):
        return to_json(value.get_value())
    if hasattr(value, "dict_contents"):
        return to_json(value.dict_contents())
    if isinstance(value, dict):
        return {str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json(x) for x in value]
    return str(value)


def compute_test_value(prop: str, orig_val: Any, profile: str) -> Any:
    """Determine a safe, modified test value to write for testing."""
    # 1. Text / String properties
    if prop in ("object-name", "description", "active-text", "inactive-text"):
        s = str(orig_val) if orig_val is not None else ""
        if s.endswith("_T"):
            return s[:-2]
        if len(s) > 20:
            return s[:18] + "_T"
        return f"{s}_T" if s else "Test_Val"

    # 2. Boolean property
    if prop == "out-of-service":
        if isinstance(orig_val, bool):
            return not orig_val
        return False if str(orig_val).lower() in ("true", "1") else True

    # 3. Polarity (bi, bo only)
    if prop == "polarity":
        s = str(orig_val).lower()
        return "reverse" if "normal" in s or s == "0" else "normal"

    # 4. Binary Present-Value (bi, bo, bv)
    if profile in ("bi", "bo", "bv") and prop == "present-value":
        s = str(orig_val).lower()
        return "inactive" if "active" in s or s in ("1", "true") else "active"

    # 5. Analog / Real / Float values (Limits, deadband, cov, analog PV)
    if prop in ("high-limit", "low-limit", "deadband", "cov-increment") or (
        profile in ("ai", "ao", "av", "iv", "piv", "lav") and prop == "present-value"
    ):
        try:
            val_num = float(orig_val)
            if prop == "deadband":
                return round(val_num + 0.5, 2)
            if prop == "cov-increment":
                return round(val_num + 0.1, 2)
            return round(val_num + 1.0, 2)
        except (TypeError, ValueError):
            return 25.0

    # 6. Unsigned integer properties (time-delay)
    if prop == "time-delay":
        try:
            val_int = int(orig_val)
            return val_int + 5 if val_int < 60 else val_int - 5
        except (TypeError, ValueError):
            return 10

    # 7. Multi-state Present-Value
    if profile in ("msv", "msi", "mso") and prop == "present-value":
        try:
            val_int = int(orig_val)
            return 2 if val_int == 1 else 1
        except (TypeError, ValueError):
            return 1

    # 8. State-Text (Array of CharacterString)
    if prop == "state-text":
        if isinstance(orig_val, (list, tuple)) and orig_val:
            return [
                str(item)[:-2] if str(item).endswith("_T") else f"{item}_T"
                for item in orig_val
            ]
        return ["State_1_T", "State_2_T"]

    # 9. Units
    if prop == "units":
        s = str(orig_val).lower()
        return "degreesFahrenheit" if "celsius" in s or s == "62" else "degreesCelsius"

    return f"{orig_val}_t"


def values_match(actual: Any, expected: Any) -> bool:
    """Compare actual readback value with written test value."""
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return False

    # Handle boolean comparisons (handles 1/0, True/False, "true"/"false")
    if isinstance(expected, bool) or isinstance(actual, bool):
        b_act = bool(int(actual)) if isinstance(actual, (int, float)) and actual in (0, 1) else (
            actual if isinstance(actual, bool) else str(actual).lower() in ("true", "1")
        )
        b_exp = bool(int(expected)) if isinstance(expected, (int, float)) and expected in (0, 1) else (
            expected if isinstance(expected, bool) else str(expected).lower() in ("true", "1")
        )
        return b_act == b_exp

    # Handle numeric comparisons
    if (isinstance(expected, (int, float)) and not isinstance(expected, bool)) or (
        isinstance(actual, (int, float)) and not isinstance(actual, bool)
    ):
        try:
            return math.isclose(float(actual), float(expected), rel_tol=1e-3, abs_tol=1e-3)
        except (TypeError, ValueError):
            pass

    # Handle binary string states
    s_act = str(actual).lower().strip()
    s_exp = str(expected).lower().strip()
    if s_act in ("active", "1", "true") and s_exp in ("active", "1", "true"):
        return True
    if s_act in ("inactive", "0", "false") and s_exp in ("inactive", "0", "false"):
        return True

    # Handle enum / strings with hyphens vs camelCase (e.g. degrees-fahrenheit vs degreesFahrenheit)
    s_act_clean = s_act.replace("-", "").replace("_", "")
    s_exp_clean = s_exp.replace("-", "").replace("_", "")
    if s_act_clean == s_exp_clean:
        return True

    # Handle lists (e.g. state-text)
    if isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
        if len(expected) != len(actual):
            return False
        return all(values_match(a, e) for a, e in zip(actual, expected))

    return s_act == s_exp


async def test_single_property(
    app: Application,
    target: str,
    obj_id: str,
    obj_name: str,
    profile: str,
    prop: str,
    timeout: float,
    priority: int,
    restore: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """Test write and readback on a single object property."""
    entry: dict[str, Any] = {
        "object_id": obj_id,
        "object_name": obj_name,
        "profile": profile,
        "property": prop,
    }

    # Step 1: Read original value
    try:
        raw_orig = await asyncio.wait_for(app.read_property(target, obj_id, prop), timeout=timeout)
        if isinstance(raw_orig, ErrorRejectAbortNack):
            raise RuntimeError(str(raw_orig))
        orig_val = to_json(raw_orig)
        entry["original_value"] = orig_val
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as read_err:
        err_str = str(read_err)
        if "unknown-property" in err_str.lower():
            entry.update(status="not_supported", message="Property not supported by object")
        else:
            entry.update(status="read_failed", error=f"{type(read_err).__name__}: {err_str}")
        return entry

    # Step 2: Compute test value to write
    test_val = compute_test_value(prop, orig_val, profile)
    entry["test_value"] = to_json(test_val)

    if dry_run:
        entry.update(status="dry_run", message=f"Would write: {test_val}")
        return entry

    # Determine if priority should be applied (AO and BO present-value are commandable)
    is_commandable = profile in ("ao", "bo") and prop == "present-value"
    write_prio = priority if is_commandable else None

    # Step 3: Write test value
    try:
        await asyncio.wait_for(
            app.write_property(target, obj_id, prop, str(test_val), None, write_prio),
            timeout=timeout,
        )
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as write_err:
        err_str = str(write_err)
        if (
            "write-access-denied" in err_str.lower()
            or "read-only" in err_str.lower()
            or "not-for-writing" in err_str.lower()
        ):
            entry.update(status="read_only", message="Write access denied (property is read-only)")
        else:
            entry.update(status="write_failed", error=f"{type(write_err).__name__}: {err_str}")
        return entry

    # Step 4: Readback verification
    try:
        raw_actual = await asyncio.wait_for(app.read_property(target, obj_id, prop), timeout=timeout)
        actual_val = to_json(raw_actual)
        entry["actual_readback"] = actual_val
        matched = values_match(actual_val, test_val)
        entry["status"] = "writable" if matched else "mismatch"
        if not matched:
            entry["error"] = f"Readback mismatch (expected: {test_val}, got: {actual_val})"
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException as readback_err:
        entry.update(status="readback_failed", error=f"{type(readback_err).__name__}: {readback_err}")

    # Step 5: Restore original value
    if restore:
        try:
            if is_commandable:
                # Relinquish priority back to Null
                await asyncio.wait_for(
                    app.write_property(target, obj_id, prop, "Null", None, write_prio),
                    timeout=timeout,
                )
            else:
                restore_val = orig_val if orig_val is not None else ""
                await asyncio.wait_for(
                    app.write_property(target, obj_id, prop, str(restore_val), None, None),
                    timeout=timeout,
                )
            entry["restored"] = True
        except (SystemExit, KeyboardInterrupt):
            raise
        except BaseException as rest_err:
            entry["restored"] = False
            entry["restore_error"] = str(rest_err)

    return entry


async def run_write_tests(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Configuration file not found: {config_path}", file=sys.stderr)
        return 1

    with config_path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}

    for k in ("device", "test_pc", "mandatory_objects"):
        if k not in config:
            raise ValueError(f"Missing key in config: {k}")

    pc = config["test_pc"]
    local_address = str(pc["address"])
    udp_port = int(pc.get("udp_port", 47808))
    if udp_port != 47808 and ":" not in local_address:
        local_address = f"{local_address}:{udp_port}"
    timeout = float(pc.get("timeout_seconds", 5))
    target = str(config["device"]["address"])

    # Collect objects to test
    objects = config["mandatory_objects"]
    if args.object_id:
        objects = [o for o in objects if o["object_id"] == args.object_id]
        if not objects:
            print(f"Error: Specified object-id '{args.object_id}' not found in configuration.")
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
    if args.dry_run:
        print("[*] MODE: DRY RUN (no writes will be transmitted)")

    # Initialize BACnet application
    app_args = SimpleArgumentParser().parse_args([
        "--address", local_address,
        "--instance", str(pc["device_instance"]),
        "--name", "BACnet Write Test Suite",
    ])
    app = Application.from_args(app_args)

    results: list[dict[str, Any]] = []

    try:
        for obj_idx, obj in enumerate(objects, 1):
            profile = obj["profile"]
            obj_id = obj["object_id"]
            obj_name = obj.get("name", obj_id)

            # Build list of properties for this object
            props_to_test = list(COMMON_WRITE_PROPERTIES)
            if profile in TARGET_PROPERTIES_BY_PROFILE:
                for p in TARGET_PROPERTIES_BY_PROFILE[profile]:
                    if p not in props_to_test:
                        props_to_test.append(p)

            # Optional filter by specific property
            if args.property_name:
                props_to_test = [p for p in props_to_test if p == args.property_name]

            if not props_to_test:
                continue

            for prop in props_to_test:
                result = await test_single_property(
                    app=app,
                    target=target,
                    obj_id=obj_id,
                    obj_name=obj_name,
                    profile=profile,
                    prop=prop,
                    timeout=timeout,
                    priority=args.priority,
                    restore=not args.no_restore,
                    dry_run=args.dry_run,
                )
                results.append(result)

                # Format terminal display
                label = f"{obj_id} / {prop}"
                status = result.get("status", "unknown").upper()

                if status == "WRITABLE":
                    orig = result.get("original_value")
                    test_v = result.get("test_value")
                    rest_str = " (restored)" if result.get("restored") else ""
                    print(f"[{status}]  {label} (orig: {orig} -> test: {test_v}{rest_str})")
                elif status == "READ_ONLY":
                    print(f"[{status}] {label} ({result.get('message', 'read-only')})")
                elif status == "NOT_SUPPORTED":
                    # Optionally hide or show compactly
                    print(f"[{status}] {label}")
                elif status == "DRY_RUN":
                    print(f"[{status}]   {label} -> would write: {result.get('test_value')}")
                else:
                    err_msg = result.get("error") or result.get("message") or ""
                    print(f"[{status}]    {label} -> {err_msg}")

                if args.delay > 0:
                    await asyncio.sleep(args.delay)

    finally:
        app.close()

    # Generate summary counts
    writable_count = sum(r.get("status") == "writable" for r in results)
    readonly_count = sum(r.get("status") == "read_only" for r in results)
    notsupp_count = sum(r.get("status") == "not_supported" for r in results)
    failed_count = sum(r.get("status") in ("mismatch", "write_failed", "readback_failed") for r in results)

    report_data = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "target_address": target,
        "total_properties_tested": len(results),
        "writable": writable_count,
        "read_only": readonly_count,
        "not_supported": notsupp_count,
        "failed": failed_count,
        "results": results,
    }

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=================================================================")
    print(f"Write Test Finished. Results:")
    print(f"  - Total Tested: {len(results)}")
    print(f"  - Writable (Verified & Restored): {writable_count}")
    print(f"  - Read-Only: {readonly_count}")
    print(f"  - Not Supported (Property omitted): {notsupp_count}")
    print(f"  - Failed / Mismatch: {failed_count}")
    print(f"Report JSON saved to: {report_path}")
    print("=================================================================\n")

    return 0 if failed_count == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute write and readback tests on configured BACnet objects and properties"
    )
    parser.add_argument(
        "--config", "-c",
        default="config/mandatory-property-read.yaml",
        help="Path to YAML configuration (default: config/mandatory-property-read.yaml)",
    )
    parser.add_argument(
        "--report", "-r",
        default="reports/property-write-result.json",
        help="Path to output JSON report (default: reports/property-write-result.json)",
    )
    parser.add_argument(
        "--first-per-type",
        action="store_true",
        help="Test only 1 object per profile (AI, AO, AV, BI, BO, BV, MSV, etc.) for quick testing",
    )
    parser.add_argument(
        "--object-id", "-o",
        default=None,
        help="Test a single object ID (e.g. analog-value,1)",
    )
    parser.add_argument(
        "--property", "-p",
        dest="property_name",
        default=None,
        help="Test a single property only (e.g. high-limit or description)",
    )
    parser.add_argument(
        "--priority",
        type=int,
        default=8,
        help="BACnet write priority for commandable points (default: 8)",
    )
    parser.add_argument(
        "--no-restore",
        action="store_true",
        help="Do NOT restore original values after testing (default restores original value / relinquishes priority)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.1,
        help="Delay in seconds between write operations (default: 0.1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview test values without sending any write requests",
    )
    args = parser.parse_args()
    return asyncio.run(run_write_tests(args))


if __name__ == "__main__":
    raise SystemExit(main())
