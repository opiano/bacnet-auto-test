#!/usr/bin/env python3
"""Discover BACnet objects from a target device and generate config/property-read.yaml."""

from __future__ import annotations

import argparse
import asyncio
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from bacpypes3.apdu import ErrorRejectAbortNack
from bacpypes3.app import Application
from bacpypes3.argparse import SimpleArgumentParser
from bacpypes3.pdu import Address

# BACnet standard object types mapped by integer code
TYPE_INT_TO_NAME: dict[int, str] = {
    0: "analog-input",
    1: "analog-output",
    2: "analog-value",
    3: "binary-input",
    4: "binary-output",
    5: "binary-value",
    6: "calendar",
    7: "command",
    8: "device",
    9: "event-enrollment",
    10: "file",
    11: "group",
    12: "loop",
    13: "multi-state-input",
    14: "multi-state-output",
    15: "notification-class",
    16: "program",
    17: "schedule",
    18: "averaging",
    19: "multi-state-value",
    20: "trend-log",
    21: "life-safety-point",
    22: "life-safety-zone",
    23: "accumulator",
    24: "pulse-converter",
    25: "event-log",
    26: "global-group",
    27: "trend-log-multiple",
    28: "load-control",
    29: "structured-view",
    30: "access-door",
    31: "timer",
    32: "access-credential",
    33: "access-point",
    34: "access-rights",
    35: "access-user",
    36: "access-zone",
    37: "credential-data-input",
    38: "network-security",
    39: "bitstring-value",
    40: "characterstring-value",
    41: "date-pattern-value",
    42: "date-value",
    43: "datetime-pattern-value",
    44: "datetime-value",
    45: "integer-value",
    46: "large-analog-value",
    47: "octetstring-value",
    48: "positive-integer-value",
    49: "time-pattern-value",
    50: "time-value",
    51: "notification-forwarder",
    52: "alert-enrollment",
    53: "channel",
    54: "lighting-output",
    55: "binary-lighting-output",
    56: "network-port",
    57: "elevator-group",
    58: "escalator",
    59: "lift",
    60: "staging",
    61: "audit-log",
    62: "audit-reporter",
    # Custom / Proprietary object types
    223: "tot",
    226: "egc",
    227: "cgc",
    246: "fbd",
}

# Supported profiles in property_read.py
PROFILE_MAP: dict[str, str] = {
    "analog-input": "ai",
    "analog-output": "ao",
    "analog-value": "av",
    "binary-input": "bi",
    "binary-output": "bo",
    "binary-value": "bv",
    "multi-state-input": "msi",
    "multi-state-output": "mso",
    "multi-state-value": "msv",
    "integer-value": "iv",
    "positive-integer-value": "piv",
    "characterstring-value": "csv",
    "large-analog-value": "lav",
    "network-port": "network_port",
    "file": "file",
    "device": "device",
    "notification-class": "notification_class",
    "calendar": "calendar",
    "schedule": "schedule",
    "trend-log": "trend_log",
    # Custom / Proprietary object profiles
    "tot": "tot",
    "egc": "egc",
    "cgc": "cgc",
    "fbd": "fbd",
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
            setattr(ObjectType, clean_name, code)
            setattr(ObjectType, clean_name.replace("-", "_"), code)
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

        ashrae_info = get_vendor_info(0)
        for code, cls in CUSTOM_OBJECT_CLASSES.items():
            ashrae_info.register_object_class(code, cls)

        for v_info in _vendor_info.values():
            for code, cls in CUSTOM_OBJECT_CLASSES.items():
                v_info.register_object_class(code, cls)

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
    """Resolve an object identifier to a BACpypes3 ObjectIdentifier instance."""
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


def camel_to_kebab(name: str) -> str:
    """Convert camelCase string to kebab-case."""
    if "-" in name:
        return name.lower()
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", name)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1-\2", s)
    return s.lower()


def detect_local_ip(target_ip: str) -> str:
    """Detect local IP address on the interface that routes to target_ip."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target_ip, 47808))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


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


def normalize_object_identifier(item: Any) -> tuple[str, int, str]:
    """
    Parse BACpypes ObjectIdentifier into (kebab_type, instance, object_id_str).
    Example: ('analog-value', 1, 'analog-value,1')
    """
    raw_type = None
    instance = None

    if isinstance(item, (tuple, list)) and len(item) == 2:
        raw_type, instance = item[0], int(item[1])
    elif hasattr(item, "objectType") and (hasattr(item, "instance") or hasattr(item, "instanceNumber")):
        raw_type = item.objectType
        instance = int(getattr(item, "instance", getattr(item, "instanceNumber", 0)))
    else:
        s = str(item).replace(":", ",")
        parts = s.split(",")
        if len(parts) >= 2:
            raw_type = parts[0].strip()
            instance = int(parts[1].strip())
        else:
            raise ValueError(f"Cannot parse object identifier: {item}")

    if isinstance(raw_type, int) and raw_type in TYPE_INT_TO_NAME:
        kebab_type = TYPE_INT_TO_NAME[raw_type]
    elif str(raw_type).isdigit() and int(str(raw_type)) in TYPE_INT_TO_NAME:
        kebab_type = TYPE_INT_TO_NAME[int(str(raw_type))]
    else:
        type_str = str(raw_type)
        if "." in type_str:
            type_str = type_str.split(".")[-1]
        kebab_type = camel_to_kebab(type_str)

    object_id_str = f"{kebab_type},{instance}"
    return kebab_type, instance, object_id_str


async def discover_device_instance(
    app: Application,
    target_address: str,
    local_address: str,
    timeout: float = 3.0,
) -> int:
    """Find target device instance using unicast, subnet broadcast, global broadcast, or candidate probe."""
    target_ip = target_address.split(":")[0]

    # 1. Try unicast Who-Is directly to target address using Address object
    print(f"[*] Sending unicast Who-Is to {target_address}...")
    try:
        dest = Address(target_address)
        responses = await asyncio.wait_for(app.who_is(address=dest), timeout=timeout)
        if responses:
            for resp in responses:
                dev_id = resp.iAmDeviceIdentifier
                inst = dev_id[1] if isinstance(dev_id, (tuple, list)) else getattr(dev_id, "instance", None)
                if inst is not None:
                    print(f"[+] Discovered Device Instance {inst} via unicast Who-Is ({resp.pduSource})")
                    return int(inst)
    except Exception as e:
        print(f"[-] Unicast Who-Is did not respond ({e}).")

    # 2. Try subnet broadcast to port 47808 (ensures controller receives it even if client is on 47809)
    try:
        local_ip = local_address.split("/")[0]
        ip_parts = local_ip.split(".")
        if len(ip_parts) == 4:
            subnet_bcast = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.255:47808"
            print(f"[*] Sending Who-Is to subnet broadcast {subnet_bcast}...")
            dest_bcast = Address(subnet_bcast)
            responses = await asyncio.wait_for(app.who_is(address=dest_bcast), timeout=timeout)
            if responses:
                for resp in responses:
                    src_str = str(resp.pduSource)
                    if target_ip in src_str:
                        dev_id = resp.iAmDeviceIdentifier
                        inst = dev_id[1] if isinstance(dev_id, (tuple, list)) else getattr(dev_id, "instance", None)
                        if inst is not None:
                            print(f"[+] Discovered Device Instance {inst} via subnet broadcast ({resp.pduSource})")
                            return int(inst)
                if len(responses) == 1:
                    resp = responses[0]
                    dev_id = resp.iAmDeviceIdentifier
                    inst = dev_id[1] if isinstance(dev_id, (tuple, list)) else getattr(dev_id, "instance", None)
                    if inst is not None:
                        print(f"[+] Discovered single responding Device Instance {inst} ({resp.pduSource})")
                        return int(inst)
    except Exception as e:
        print(f"[-] Subnet broadcast Who-Is did not respond ({e}).")

    # 3. Try standard local Who-Is broadcast
    try:
        print("[*] Sending standard local broadcast Who-Is...")
        responses = await asyncio.wait_for(app.who_is(), timeout=timeout)
        if responses:
            for resp in responses:
                src_str = str(resp.pduSource)
                if target_ip in src_str:
                    dev_id = resp.iAmDeviceIdentifier
                    inst = dev_id[1] if isinstance(dev_id, (tuple, list)) else getattr(dev_id, "instance", None)
                    if inst is not None:
                        print(f"[+] Discovered Device Instance {inst} via local broadcast ({resp.pduSource})")
                        return int(inst)
            if len(responses) == 1:
                resp = responses[0]
                dev_id = resp.iAmDeviceIdentifier
                inst = dev_id[1] if isinstance(dev_id, (tuple, list)) else getattr(dev_id, "instance", None)
                if inst is not None:
                    print(f"[+] Discovered single responding Device Instance {inst} ({resp.pduSource})")
                    return int(inst)
    except Exception as e:
        print(f"[-] Local broadcast Who-Is did not respond ({e}).")

    # 4. Fallback: Directly probe candidate Device Instances (e.g. last octet of IP or standard IDs)
    candidates: list[int] = []
    try:
        last_octet = int(target_ip.split(".")[-1])
        candidates.append(last_octet)
    except Exception:
        pass
    for cand in [1, 100, 1000, 1234, 900001]:
        if cand not in candidates:
            candidates.append(cand)

    print(f"[*] Who-Is did not respond. Probing probable Device Instances directly: {candidates}...")
    for cand in candidates:
        try:
            val = await asyncio.wait_for(
                app.read_property(target_address, f"device,{cand}", "object-name"),
                timeout=1.5,
            )
            if val is not None and not isinstance(val, ErrorRejectAbortNack):
                print(f"[+] Candidate probe succeeded! Found Device Instance {cand} (name: {val})")
                return cand
        except Exception:
            continue

    raise RuntimeError(
        f"Could not automatically discover Device Instance for {target_address}.\n"
        f"Please specify it directly with: --device-instance <ID> (e.g. -i 130)"
    )


async def read_object_list(app: Application, target_address: str, device_instance: int, timeout: float = 5.0) -> list[Any]:
    """Read object-list property from device, trying full read first, then array indexing."""
    device_obj = f"device,{device_instance}"
    print(f"[*] Reading object-list from {device_obj}...")

    # Method 1: Read the entire array at once
    try:
        raw_list = await asyncio.wait_for(
            app.read_property(target_address, device_obj, "object-list"),
            timeout=timeout,
        )
        if raw_list is not None and not isinstance(raw_list, ErrorRejectAbortNack):
            if hasattr(raw_list, "get_value"):
                raw_list = raw_list.get_value()
            if isinstance(raw_list, (list, tuple)):
                print(f"[+] Successfully read full object-list ({len(raw_list)} objects found)")
                return list(raw_list)
    except Exception as e:
        print(f"[-] Direct object-list read not available ({e}). Trying indexed array read...")

    # Method 2: Read array length (index 0), then each element (index 1..N)
    try:
        count_val = await asyncio.wait_for(
            app.read_property(target_address, device_obj, "object-list", array_index=0),
            timeout=timeout,
        )
        if hasattr(count_val, "get_value"):
            count_val = count_val.get_value()
        count = int(count_val)
        print(f"[+] Device reports {count} objects. Reading objects individually...")

        objects = []
        for idx in range(1, count + 1):
            try:
                item = await asyncio.wait_for(
                    app.read_property(target_address, device_obj, "object-list", array_index=idx),
                    timeout=timeout,
                )
                if hasattr(item, "get_value"):
                    item = item.get_value()
                objects.append(item)
                if idx % 10 == 0 or idx == count:
                    print(f"    Progress: {idx}/{count} objects read...")
            except Exception as item_err:
                print(f"    [!] Failed to read object-list[{idx}]: {item_err}")
        return objects
    except Exception as e:
        raise RuntimeError(f"Failed to read object-list from {device_obj}: {e}") from e


async def fetch_object_name(app: Application, target_address: str, object_id: str, timeout: float = 2.0) -> str | None:
    """Attempt to read object-name for an object."""
    try:
        target_obj_id = resolve_object_id(object_id)
        val = await asyncio.wait_for(
            app.read_property(target_address, target_obj_id, "object-name"),
            timeout=timeout,
        )
        if hasattr(val, "get_value"):
            val = val.get_value()
        return str(val) if val is not None else None
    except Exception:
        return None


def write_yaml_config(
    output_path: Path,
    target_address: str,
    local_address: str,
    local_instance: int,
    local_port: int,
    timeout: float,
    objects: list[dict[str, Any]],
) -> None:
    """Generate cleanly formatted YAML file with comments."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# =====================================================================",
        "# BACnet/IP Property Read Configuration",
        f"# Auto-generated: {timestamp}",
        f"# Target Controller: {target_address}",
        f"# Total Configured Objects: {len(objects)}",
        "# =====================================================================",
        "",
        "device:",
        f'  address: "{target_address}"',
        "",
        "test_pc:",
        f'  address: "{local_address}"',
        f"  device_instance: {local_instance}",
        f"  udp_port: {local_port}",
        f"  timeout_seconds: {int(timeout) if timeout.is_integer() else timeout}",
        "",
        "# List of objects to verify standard properties against.",
        "objects:",
    ]

    for obj in objects:
        clean_name = str(obj["name"]).replace('"', '\\"')
        lines.append(f'  - name: "{clean_name}"')
        lines.append(f'    object_id: "{obj["object_id"]}"')
        lines.append(f'    profile: {obj["profile"]}')
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


async def run(args: argparse.Namespace) -> int:
    target_arg = args.target_flag or args.target
    if not target_arg:
        print("Error: Target device IP address is required.", file=sys.stderr)
        print("Usage: python generate_config.py <DEVICE_IP> [options]", file=sys.stderr)
        return 1

    # Normalize target address
    if ":" in target_arg:
        target_ip, port_str = target_arg.split(":", 1)
        target_address = f"{target_ip}:{int(port_str)}"
    else:
        target_ip = target_arg
        target_address = f"{target_ip}:47808"

    # Determine local address
    if args.local_address:
        local_address = args.local_address
        if "/" not in local_address:
            local_address = f"{local_address}/24"
    else:
        detected_ip = detect_local_ip(target_ip)
        local_address = f"{detected_ip}/24"

    local_ip_only = local_address.split("/")[0]

    # Select UDP port: check CLI argument first, then existing YAML file, else default 47808
    configured_port = None
    if args.udp_port is not None:
        configured_port = int(args.udp_port)
    else:
        output_file = Path(args.output)
        if output_file.exists():
            try:
                with output_file.open(encoding="utf-8") as f:
                    existing_data = yaml.safe_load(f) or {}
                    if "test_pc" in existing_data and "udp_port" in existing_data["test_pc"]:
                        configured_port = int(existing_data["test_pc"]["udp_port"])
            except Exception:
                pass
    if configured_port is None:
        configured_port = 47808

    # Check port availability: try configured_port first, fallback to configured_port + 1 if in use
    local_port = get_available_port(local_ip_only, configured_port)

    print(f"[*] Target Device: {target_address}")
    print(f"[*] Local Address: {local_address} (port: {local_port}, instance: {args.local_instance})")

    # Register proprietary custom object types
    register_custom_object_types()
    if getattr(args, "custom_type", None):
        for ct in args.custom_type:
            parts = ct.split(":")
            code = int(parts[0].strip())
            name = parts[1].strip() if len(parts) > 1 else f"custom-{code}"
            prof = parts[2].strip() if len(parts) > 2 else name
            TYPE_INT_TO_NAME[code] = name
            PROFILE_MAP[name] = prof
            register_custom_object_types({code: name})

    # Start BACnet application
    app_address = f"{local_address}:{local_port}" if local_port != 47808 else local_address
    bacnet_args = SimpleArgumentParser().parse_args([
        "--address", app_address,
        "--instance", str(args.local_instance),
        "--name", "BACnet Config Generator",
    ])
    app = Application.from_args(bacnet_args)

    try:
        # Step 1: Discover device instance if not provided
        if args.device_instance is not None:
            device_instance = int(args.device_instance)
            print(f"[*] Using specified Device Instance: {device_instance}")
        else:
            device_instance = await discover_device_instance(
                app, target_address, local_address, timeout=args.timeout
            )

        # Step 2: Read object-list
        raw_objects = await read_object_list(app, target_address, device_instance, timeout=args.timeout)

        # Step 3: Parse and filter supported objects
        parsed_objects: list[dict[str, Any]] = []
        skipped_count = 0
        skipped_types: dict[str, int] = {}

        for raw_item in raw_objects:
            try:
                kebab_type, instance, obj_id = normalize_object_identifier(raw_item)
            except Exception:
                skipped_count += 1
                continue

            if kebab_type in PROFILE_MAP:
                parsed_objects.append({
                    "object_id": obj_id,
                    "kebab_type": kebab_type,
                    "instance": instance,
                    "profile": PROFILE_MAP[kebab_type],
                })
            else:
                skipped_count += 1
                skipped_types[kebab_type] = skipped_types.get(kebab_type, 0) + 1

        # Ensure device object itself is present
        device_obj_id = f"device,{device_instance}"
        if not any(o["object_id"] == device_obj_id for o in parsed_objects):
            parsed_objects.insert(0, {
                "object_id": device_obj_id,
                "kebab_type": "device",
                "instance": device_instance,
                "profile": "device",
            })

        # Apply filtering if --first-per-type requested
        if args.first_per_type:
            seen_profiles = set()
            selected_objects = []
            for obj in parsed_objects:
                if obj["profile"] not in seen_profiles:
                    seen_profiles.add(obj["profile"])
                    selected_objects.append(obj)
            parsed_objects = selected_objects
            print(f"[*] Filtered to first object per profile: {len(parsed_objects)} objects selected")

        # Step 4: Resolve object names
        # Step 4: Resolve object names
        print(f"[*] Reading names for {len(parsed_objects)} objects...")
        objects: list[dict[str, Any]] = []
        for idx, obj in enumerate(parsed_objects, 1):
            name = None
            if not args.skip_names:
                name = await fetch_object_name(app, target_address, obj["object_id"])
            if not name:
                name = f"{obj['kebab_type'].replace('-', '_')}_{obj['instance']}"
            objects.append({
                "name": name,
                "object_id": obj["object_id"],
                "profile": obj["profile"],
            })
            if idx % 10 == 0 or idx == len(parsed_objects):
                print(f"    Progress: {idx}/{len(parsed_objects)} names resolved...")

        # Step 5: Write YAML file
        output_path = Path(args.output)
        write_yaml_config(
            output_path=output_path,
            target_address=target_address,
            local_address=local_address,
            local_instance=args.local_instance,
            local_port=local_port,
            timeout=args.timeout,
            objects=objects,
        )

        print("\n=================================================================")
        print(f"Config successfully generated: {output_path}")
        print(f"  - Target Address: {target_address}")
        print(f"  - Device Instance: {device_instance}")
        print(f"  - Local Test Address: {local_address} (port: {local_port})")
        print(f"  - Total Configured Objects: {len(objects)}")
        if skipped_types:
            skipped_summary = ", ".join(f"{t}: {c}" for t, c in sorted(skipped_types.items()))
            print(f"  - Skipped Unsupported Types ({skipped_count}): {skipped_summary}")
        print("=================================================================")
        print(f"\nYou can now run the property read test with:")
        print(f"  python property_read.py --config {output_path}\n")
        return 0

    finally:
        app.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover BACnet objects from a target device and generate config/property-read.yaml"
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Target BACnet controller IP or IP:port (e.g. 192.168.219.130 or 192.168.219.130:47808)",
    )
    parser.add_argument(
        "--device", "-d",
        dest="target_flag",
        default=None,
        help="Target BACnet controller IP (alternative to positional argument)",
    )
    parser.add_argument(
        "--device-instance", "-i",
        type=int,
        default=None,
        help="Target device instance number (auto-discovered if omitted, or probe fallback)",
    )
    parser.add_argument(
        "--local-address", "-l",
        default=None,
        help="Test PC BACnet/IP with CIDR (e.g. 192.168.219.125/24). Auto-detected if omitted.",
    )
    parser.add_argument(
        "--local-instance",
        type=int,
        default=900001,
        help="Test PC device instance (default: 900001)",
    )
    parser.add_argument(
        "--udp-port", "-p",
        type=int,
        default=None,
        help="Test PC UDP port (default: 47808 if available, else 47809)",
    )
    parser.add_argument(
        "--timeout", "-t",
        type=float,
        default=5.0,
        help="BACnet request timeout in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--output", "-o",
        default="config/property-read.yaml",
        help="Output YAML file path (default: config/property-read.yaml)",
    )
    parser.add_argument(
        "--first-per-type",
        action="store_true",
        help="Include only the first object of each profile type (for quick sampling)",
    )
    parser.add_argument(
        "--custom-type",
        action="append",
        metavar="CODE:NAME[:PROFILE]",
        help="Register custom object type (e.g. --custom-type 250:custom_xyz:custom)",
    )
    parser.add_argument(
        "--skip-names",
        action="store_true",
        help="Skip reading object-name for each object to speed up discovery",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
