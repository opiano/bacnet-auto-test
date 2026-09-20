#!/usr/bin/env python3
"""Discover BACnet objects from a target device and generate config/mandatory-property-read.yaml."""

from __future__ import annotations

import argparse
import asyncio
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bacpypes3.apdu import ErrorRejectAbortNack
from bacpypes3.app import Application
from bacpypes3.argparse import SimpleArgumentParser

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
}

# Supported profiles in mandatory_property_read.py
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
    "device": "device",
    "notification-class": "notification_class",
    "calendar": "calendar",
    "schedule": "schedule",
    "trend-log": "trend_log",
}


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
    else:
        type_str = str(raw_type)
        if "." in type_str:
            type_str = type_str.split(".")[-1]
        kebab_type = camel_to_kebab(type_str)

    object_id_str = f"{kebab_type},{instance}"
    return kebab_type, instance, object_id_str


async def discover_device_instance(app: Application, target_address: str, timeout: float = 5.0) -> int:
    """Find target device instance using unicast or broadcast Who-Is."""
    target_ip = target_address.split(":")[0]

    # 1. Try unicast Who-Is directly to target
    print(f"[*] Sending unicast Who-Is to {target_address}...")
    try:
        responses = await asyncio.wait_for(app.who_is(address=target_address), timeout=timeout)
        if responses:
            for resp in responses:
                dev_id = resp.iAmDeviceIdentifier
                inst = dev_id[1] if isinstance(dev_id, (tuple, list)) else getattr(dev_id, "instance", None)
                if inst is not None:
                    print(f"[+] Discovered Device Instance {inst} via unicast Who-Is ({resp.pduSource})")
                    return int(inst)
    except Exception as e:
        print(f"[-] Unicast Who-Is did not respond ({e}). Trying broadcast Who-Is...")

    # 2. Try broadcast Who-Is
    try:
        print("[*] Sending broadcast Who-Is...")
        responses = await asyncio.wait_for(app.who_is(), timeout=timeout)
        if responses:
            for resp in responses:
                src_str = str(resp.pduSource)
                if target_ip in src_str:
                    dev_id = resp.iAmDeviceIdentifier
                    inst = dev_id[1] if isinstance(dev_id, (tuple, list)) else getattr(dev_id, "instance", None)
                    if inst is not None:
                        print(f"[+] Discovered Device Instance {inst} via broadcast Who-Is ({resp.pduSource})")
                        return int(inst)
            if len(responses) == 1:
                resp = responses[0]
                dev_id = resp.iAmDeviceIdentifier
                inst = dev_id[1] if isinstance(dev_id, (tuple, list)) else getattr(dev_id, "instance", None)
                if inst is not None:
                    print(f"[+] Discovered single responding Device Instance {inst} ({resp.pduSource})")
                    return int(inst)
    except Exception as e:
        print(f"[-] Broadcast Who-Is failed: {e}")

    raise RuntimeError(
        f"Could not automatically discover Device Instance for {target_address}.\n"
        f"Please specify it directly with: --device-instance <ID>"
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
        val = await asyncio.wait_for(
            app.read_property(target_address, object_id, "object-name"),
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
    mandatory_objects: list[dict[str, Any]],
) -> None:
    """Generate cleanly formatted YAML file with comments."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# =====================================================================",
        "# BACnet/IP Mandatory Property Read Configuration",
        f"# Auto-generated: {timestamp}",
        f"# Target Controller: {target_address}",
        f"# Total Configured Objects: {len(mandatory_objects)}",
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
        "# List of objects to verify standard mandatory properties against.",
        "mandatory_objects:",
    ]

    for obj in mandatory_objects:
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
        print("Usage: python generate_mandatory_config.py <DEVICE_IP> [options]", file=sys.stderr)
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

    print(f"[*] Target Device: {target_address}")
    print(f"[*] Local Address: {local_address} (port: {args.udp_port}, instance: {args.local_instance})")

    # Start BACnet application
    app_address = f"{local_address}:{args.udp_port}" if args.udp_port != 47808 else local_address
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
            device_instance = await discover_device_instance(app, target_address, timeout=args.timeout)

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
        print(f"[*] Reading names for {len(parsed_objects)} objects...")
        mandatory_objects: list[dict[str, Any]] = []
        for idx, obj in enumerate(parsed_objects, 1):
            name = None
            if not args.skip_names:
                name = await fetch_object_name(app, target_address, obj["object_id"])
            if not name:
                name = f"{obj['kebab_type'].replace('-', '_')}_{obj['instance']}"
            mandatory_objects.append({
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
            local_port=args.udp_port,
            timeout=args.timeout,
            mandatory_objects=mandatory_objects,
        )

        print("\n=================================================================")
        print(f"Config successfully generated: {output_path}")
        print(f"  - Target Address: {target_address}")
        print(f"  - Device Instance: {device_instance}")
        print(f"  - Local Test Address: {local_address}")
        print(f"  - Total Configured Objects: {len(mandatory_objects)}")
        if skipped_types:
            skipped_summary = ", ".join(f"{t}: {c}" for t, c in sorted(skipped_types.items()))
            print(f"  - Skipped Unsupported Types ({skipped_count}): {skipped_summary}")
        print("=================================================================")
        print(f"\nYou can now run the mandatory property test with:")
        print(f"  python mandatory_property_read.py --config {output_path}\n")
        return 0

    finally:
        app.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover BACnet objects from a target device and generate config/mandatory-property-read.yaml"
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Target BACnet controller IP or IP:port (e.g. 192.168.219.227 or 192.168.219.227:47808)",
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
        help="Target device instance number (auto-discovered via Who-Is if omitted)",
    )
    parser.add_argument(
        "--local-address", "-l",
        default=None,
        help="Test PC BACnet/IP with CIDR (e.g. 192.168.219.50/24). Auto-detected if omitted.",
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
        default=47809,
        help="Test PC UDP port (default: 47809)",
    )
    parser.add_argument(
        "--timeout", "-t",
        type=float,
        default=5.0,
        help="BACnet request timeout in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--output", "-o",
        default="config/mandatory-property-read.yaml",
        help="Output YAML file path (default: config/mandatory-property-read.yaml)",
    )
    parser.add_argument(
        "--first-per-type",
        action="store_true",
        help="Include only the first object of each profile type (for quick sampling)",
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
