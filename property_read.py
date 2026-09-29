#!/usr/bin/env python3
"""Read BACnet properties for configured object instances."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import time
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
    # Custom / Proprietary Object Profiles
    "tot": ["present-value"],
    "egc": ["present-value"],
    "cgc": ["present-value"],
    "fbd": ["present-value"],
}

# Default Custom / Proprietary Object Types
CUSTOM_OBJECT_TYPES: dict[int, str] = {
    223: "tot",
    226: "egc",
    227: "cgc",
    246: "fbd",
}

CUSTOM_NAME_TO_TYPE: dict[str, int] = {
    "tot": 223,
    "egc": 226,
    "cgc": 227,
    "fbd": 246,
}

CUSTOM_OBJECT_PV_TYPES: dict[str, str] = {
    "tot": "real",
    "egc": "any",
    "cgc": "boolean",
    "fbd": "boolean",
}


try:
    from bacpypes3.primitivedata import Atomic, TagClass
    _DynamicBase = Atomic
except Exception:
    _DynamicBase = object
    TagClass = None


def resolve_bacnet_type(type_spec: Any) -> Any:
    """Resolve a BACnet property datatype for custom objects."""
    if isinstance(type_spec, type):
        return type_spec
    if not isinstance(type_spec, str):
        return DynamicValue
    s = type_spec.strip().lower().replace("_", "-")
    try:
        from bacpypes3.primitivedata import (
            Real,
            Boolean,
            Integer,
            Unsigned,
            CharacterString,
            OctetString,
            BitString,
            Date,
            Time,
            ObjectIdentifier,
            ObjectType,
        )
        from bacpypes3.constructeddata import AnyAtomic, Any

        type_map = {
            "real": Real,
            "float": Real,
            "double": Real,
            "boolean": Boolean,
            "bool": Boolean,
            "any": AnyAtomic,
            "anyatomic": AnyAtomic,
            "any-atomic": AnyAtomic,
            "integer": Integer,
            "int": Integer,
            "unsigned": Unsigned,
            "uint": Unsigned,
            "character-string": CharacterString,
            "string": CharacterString,
            "str": CharacterString,
            "octet-string": OctetString,
            "bit-string": BitString,
            "date": Date,
            "time": Time,
            "object-identifier": ObjectIdentifier,
            "object-type": ObjectType,
        }
        if s in type_map:
            return type_map[s]
    except Exception:
        pass
    return DynamicValue


class DynamicValue(_DynamicBase):
    """Dynamic decoder for proprietary / custom object properties.

    Safely decodes application or context tags without consuming
    an outer enclosing context tag (such as ClosingTag 3 in ReadPropertyACK).
    """

    @classmethod
    def decode(cls, tag_list: Any) -> Any:
        if not tag_list:
            return None

        # Check if first tag is already a closing tag (end of sequence)
        tag = tag_list.peek() if hasattr(tag_list, "peek") else None
        if tag is not None and getattr(tag, "tag_class", None) == getattr(TagClass, "closing", 5):
            return None

        # If it's a single application tag, consume only that tag and return
        if tag is not None and getattr(tag, "tag_class", None) == getattr(TagClass, "application", 0):
            tag = tag_list.pop()
            if hasattr(tag, "app_to_object"):
                return tag.app_to_object()
            if hasattr(tag, "get_value"):
                return tag.get_value()
            return tag

        # Decode tags until next tag is a closing tag or tag_list is empty
        results = []
        while tag_list:
            next_tag = tag_list.peek() if hasattr(tag_list, "peek") else None
            if next_tag is not None and getattr(next_tag, "tag_class", None) == getattr(TagClass, "closing", 5):
                break
            t = tag_list.pop()
            if getattr(t, "tag_class", None) == 0 and hasattr(t, "app_to_object"):
                results.append(t.app_to_object())
            elif hasattr(t, "get_value"):
                results.append(t.get_value())
            else:
                results.append(t)
        return results if len(results) != 1 else (results[0] if results else None)


class BaseCustomObjectClass:
    """Base BACpypes3 object class stub for vendor proprietary objects."""
    _object_type_name: str = ""
    _pv_type: Any = None

    @classmethod
    def get_property_type(cls, prop: Any) -> Any:
        prop_str = str(prop).lower().replace("_", "-")
        norm = prop_str.replace("-", "")

        # Standard object properties
        if norm in ("objectidentifier", "75"):
            try:
                from bacpypes3.primitivedata import ObjectIdentifier
                return ObjectIdentifier
            except Exception:
                pass
        if norm in ("objectname", "77", "description", "28"):
            try:
                from bacpypes3.primitivedata import CharacterString
                return CharacterString
            except Exception:
                pass
        if norm in ("objecttype", "79"):
            try:
                from bacpypes3.primitivedata import ObjectType
                return ObjectType
            except Exception:
                pass
        if norm in ("propertylist", "371"):
            try:
                from bacpypes3.constructeddata import ArrayOf
                from bacpypes3.basetypes import PropertyIdentifier
                return ArrayOf(PropertyIdentifier)
            except Exception:
                pass

        if prop == 85 or prop == "85" or norm in ("presentvalue", "85"):
            if cls._pv_type is not None:
                return cls._pv_type

        # For any other property, use safe dynamic decoder
        return DynamicValue


# Backward compatibility alias
CustomObjectClass = BaseCustomObjectClass

CUSTOM_OBJECT_CLASSES: dict[int, type] = {}


def make_custom_object_class(obj_type_name: str, pv_type: Any = None) -> type:
    clean_name = str(obj_type_name).strip().lower()

    class _CustomObj(BaseCustomObjectClass):
        _object_type_name = clean_name
        _pv_type = pv_type

    _CustomObj.__name__ = f"CustomObject_{clean_name.replace('-', '_')}"
    _CustomObj.__qualname__ = _CustomObj.__name__
    return _CustomObj


def register_custom_object_types(
    types: dict[int, str] | None = None,
    pv_types: dict[str, Any] | None = None,
) -> None:
    """Register proprietary object types into BACpypes3 ObjectType and VendorInfo."""
    t_map = types or CUSTOM_OBJECT_TYPES
    pv_map = pv_types or CUSTOM_OBJECT_PV_TYPES
    try:
        from bacpypes3.primitivedata import ObjectType
        for code, name in t_map.items():
            clean_name = str(name).strip()
            # Register attribute on ObjectType class
            setattr(ObjectType, clean_name, code)
            setattr(ObjectType, clean_name.replace("-", "_"), code)
            # Register in BACpypes3 internal enum maps
            if hasattr(ObjectType, "_enum_map"):
                ObjectType._enum_map[clean_name] = code
                ObjectType._enum_map[clean_name.lower()] = code
                ObjectType._enum_map[clean_name.upper()] = code
                ObjectType._enum_map[clean_name.replace("-", "_")] = code
                ObjectType._enum_map[clean_name.replace("_", "-")] = code
            if hasattr(ObjectType, "_attr_map"):
                ObjectType._attr_map[code] = clean_name
            if hasattr(ObjectType, "_asn1_map"):
                ObjectType._asn1_map[code] = clean_name
            # Update lookup dictionary
            CUSTOM_NAME_TO_TYPE[clean_name.lower()] = code
            CUSTOM_NAME_TO_TYPE[clean_name.replace("-", "_").lower()] = code
            CUSTOM_NAME_TO_TYPE[clean_name.replace("_", "-").lower()] = code
    except Exception:
        pass

    try:
        from bacpypes3.vendor import VendorInfo, get_vendor_info, _vendor_info

        for code, name in t_map.items():
            int_code = int(code)
            clean_name = str(name).strip().lower()
            pv_spec = pv_map.get(clean_name)
            pv_type = resolve_bacnet_type(pv_spec) if pv_spec else DynamicValue
            cls = make_custom_object_class(clean_name, pv_type)
            CUSTOM_OBJECT_CLASSES[int_code] = cls

        # 1. Register in ASHRAE default vendor info (vendor 0)
        ashrae_info = get_vendor_info(0)
        for code, cls in CUSTOM_OBJECT_CLASSES.items():
            ashrae_info.register_object_class(code, cls)

        # 2. Register in all existing vendor infos in _vendor_info
        for v_info in _vendor_info.values():
            for code, cls in CUSTOM_OBJECT_CLASSES.items():
                v_info.register_object_class(code, cls)

        # 3. Hook VendorInfo.get_object_class as universal fallback
        if not getattr(VendorInfo, "_custom_hooked", False):
            _orig_get_object_class = VendorInfo.get_object_class

            def custom_get_object_class(self, object_type: Any) -> Any:
                res = _orig_get_object_class(self, object_type)
                if res is not None:
                    return res
                try:
                    int_code = int(object_type)
                    if int_code in CUSTOM_OBJECT_CLASSES:
                        return CUSTOM_OBJECT_CLASSES[int_code]
                except (ValueError, TypeError):
                    pass
                return None

            VendorInfo.get_object_class = custom_get_object_class
            VendorInfo._custom_hooked = True
    except Exception:
        pass


def resolve_object_id(obj_id: str | tuple | Any) -> Any:
    """Resolve an object identifier to a BACpypes3 ObjectIdentifier instance.

    Handles custom/proprietary object types (e.g. 'tot,1', 'egc,2') as well
    as standard object types ('analog-value,1') and numeric tuples ((223, 1)).
    """
    try:
        from bacpypes3.primitivedata import ObjectIdentifier

        if isinstance(obj_id, ObjectIdentifier):
            return obj_id

        if isinstance(obj_id, tuple):
            return ObjectIdentifier(obj_id)

        if isinstance(obj_id, str):
            sep = "," if "," in obj_id else (":" if ":" in obj_id else None)
            if sep:
                t_str, inst_str = obj_id.split(sep, 1)
                t_clean = t_str.strip().lower()
                inst = int(inst_str.strip())
                if t_clean in CUSTOM_NAME_TO_TYPE:
                    return ObjectIdentifier((CUSTOM_NAME_TO_TYPE[t_clean], inst))
                if t_clean.isdigit():
                    return ObjectIdentifier((int(t_clean), inst))
            return ObjectIdentifier(obj_id)
    except Exception:
        pass
    return obj_id


def to_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, ErrorRejectAbortNack):
        return str(value)
    if hasattr(value, "get_value"):
        try:
            return to_json(value.get_value())
        except Exception:
            pass
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


def is_port_available(ip: str, port: int) -> bool:
    """Check if a UDP port is available for binding on local IP."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind((ip, port))
            return True
    except OSError as e:
        if getattr(e, "errno", None) in (99, 10049) or "10049" in str(e):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s2:
                    s2.bind(("", port))
                    return True
            except OSError:
                return False
        return False


def get_available_port(ip: str, preferred_port: int) -> int:
    """Try preferred port first; if unavailable, fallback to preferred_port + 1."""
    if is_port_available(ip, preferred_port):
        return preferred_port

    fallback_port = preferred_port + 1
    if not is_port_available(ip, fallback_port):
        for p in range(preferred_port + 2, preferred_port + 10):
            if is_port_available(ip, p):
                fallback_port = p
                break

    print(
        f"[!] UDP port {preferred_port} is already in use on {ip}. "
        f"Falling back to port {fallback_port}."
    )
    return fallback_port


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
    raw_local_address = str(pc["address"])
    if ":" in raw_local_address:
        base_addr, addr_port_str = raw_local_address.split(":", 1)
        configured_port = int(addr_port_str)
    else:
        base_addr = raw_local_address
        configured_port = int(pc.get("udp_port", 47808))

    local_ip_only = base_addr.split("/")[0]
    selected_port = get_available_port(local_ip_only, configured_port)

    local_address = f"{base_addr}:{selected_port}" if selected_port != 47808 else base_addr
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

    # Register proprietary custom object types into BACpypes3
    cfg_custom_types = config.get("custom_object_types", {})
    if cfg_custom_types:
        for k, v in cfg_custom_types.items():
            try:
                code = int(k)
                CUSTOM_OBJECT_TYPES[code] = str(v)
                CUSTOM_NAME_TO_TYPE[str(v).lower()] = code
            except ValueError:
                code = int(v)
                CUSTOM_OBJECT_TYPES[code] = str(k)
                CUSTOM_NAME_TO_TYPE[str(k).lower()] = code

    cfg_pv_types = config.get("custom_pv_types", {})
    if cfg_pv_types:
        for k, v in cfg_pv_types.items():
            CUSTOM_OBJECT_PV_TYPES[str(k).strip().lower()] = str(v)

    register_custom_object_types()
    for prof_name, prop_list in config.get("custom_profiles", {}).items():
        REQUIRED[prof_name] = prop_list

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
            custom_props = obj.get("properties")
            if profile not in REQUIRED and custom_props is None:
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

            props_for_obj = [*COMMON, *(custom_props if custom_props is not None else REQUIRED.get(profile, []))]
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

                t_start = time.perf_counter()
                try:
                    target_obj_id = resolve_object_id(obj_id)
                    raw_val = await asyncio.wait_for(app.read_property(target, target_obj_id, prop), timeout=timeout)
                    elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
                    if isinstance(raw_val, ErrorRejectAbortNack):
                        raise RuntimeError(str(raw_val))
                    if raw_val in ("-no object class-", "-no property type-"):
                        raise RuntimeError(f"BACpypes decode failed: {raw_val}")
                    val_json = to_json(raw_val)
                    entry.update(status="passed", actual=val_json, elapsed_ms=elapsed_ms)

                    # Real-time console output
                    val_str = json.dumps(val_json, ensure_ascii=False) if isinstance(val_json, (dict, list)) else str(val_json)
                    if len(val_str) > 70:
                        print(f"[PASSED]  {label} ({elapsed_ms:.1f}ms)")
                        print(f"  value: {val_str}")
                    else:
                        print(f"[PASSED]  {label} = {val_str} ({elapsed_ms:.1f}ms)")

                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException as error:
                    elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
                    err_str = str(error)
                    entry.update(status="failed", error=f"{type(error).__name__}: {err_str}", elapsed_ms=elapsed_ms)
                    print(f"[FAILED]    {label} ({elapsed_ms:.1f}ms) -> {type(error).__name__}: {err_str}")

                results.append(entry)

    except KeyboardInterrupt:
        print("\n[!] Read test interrupted by user (Ctrl+C). Saving partial results...", file=sys.stderr)
    finally:
        app.close()

        # Always save JSON and HTML reports even if interrupted
        passed_count = sum(r.get("status") == "passed" for r in results)
        failed_count = sum(r.get("status") == "failed" for r in results)

        valid_times = [r["elapsed_ms"] for r in results if r.get("elapsed_ms") is not None and r.get("status") == "passed"]
        avg_ms = round(sum(valid_times) / len(valid_times), 2) if valid_times else None
        min_ms = round(min(valid_times), 2) if valid_times else None
        max_ms = round(max(valid_times), 2) if valid_times else None

        report_data = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "target_address": target,
            "total": len(results),
            "passed": passed_count,
            "failed": failed_count,
            "summary": {
                "avg_response_time_ms": avg_ms,
                "min_response_time_ms": min_ms,
                "max_response_time_ms": max_ms,
            },
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
        if avg_ms is not None:
            print(f"  - Response Time: avg {avg_ms:.1f}ms (min: {min_ms:.1f}ms, max: {max_ms:.1f}ms)")
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
