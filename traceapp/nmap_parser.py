from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


@dataclass
class NmapService:
    port: int
    protocol: str
    name: str
    product: str
    notes: str


@dataclass
class NmapHost:
    address: str
    hostname: str
    os_guess: str
    services: list[NmapService] = field(default_factory=list)


def parse_nmap(path: Path | str) -> list[NmapHost]:
    """Parse nmap XML or grepable output. Auto-detects format."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("<?xml") or "<nmaprun" in stripped[:512]:
        return _parse_xml(text)
    return _parse_grepable(text)


def _parse_xml(text: str) -> list[NmapHost]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid nmap XML: {exc}") from exc

    hosts: list[NmapHost] = []
    for host_el in root.findall("host"):
        status = host_el.find("status")
        if status is not None and status.get("state") != "up":
            continue

        addr = ""
        for addr_el in host_el.findall("address"):
            if addr_el.get("addrtype") in ("ipv4", "ipv6"):
                addr = addr_el.get("addr", "")
                break
        if not addr:
            continue

        hostname = ""
        hostnames_el = host_el.find("hostnames")
        if hostnames_el is not None:
            hn = hostnames_el.find("hostname")
            if hn is not None:
                hostname = hn.get("name", "")

        os_guess = ""
        os_el = host_el.find("os")
        if os_el is not None:
            matches = os_el.findall("osmatch")
            if matches:
                best = max(matches, key=lambda m: int(m.get("accuracy", "0")))
                os_guess = best.get("name", "")

        services: list[NmapService] = []
        ports_el = host_el.find("ports")
        if ports_el is not None:
            for port_el in ports_el.findall("port"):
                state_el = port_el.find("state")
                if state_el is None or state_el.get("state") != "open":
                    continue
                port_num = int(port_el.get("portid", 0))
                proto = port_el.get("protocol", "tcp")
                name = product = version = extra = ""
                svc_el = port_el.find("service")
                if svc_el is not None:
                    name = svc_el.get("name", "")
                    product = svc_el.get("product", "")
                    version = svc_el.get("version", "")
                    extra = svc_el.get("extrainfo", "")
                product_full = " ".join(filter(None, [product, version])).strip()
                notes = " ".join(filter(None, [version, extra])).strip()
                services.append(NmapService(
                    port=port_num, protocol=proto,
                    name=name, product=product_full, notes=notes,
                ))

        hosts.append(NmapHost(address=addr, hostname=hostname, os_guess=os_guess, services=services))
    return hosts


def _parse_grepable(text: str) -> list[NmapHost]:
    # Format: Host: 10.0.0.1 (hostname)  Ports: 22/open/tcp//ssh//OpenSSH 7.9/
    hosts: list[NmapHost] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith("Host:"):
            continue
        m = re.match(r"Host:\s+(\S+)\s+\(([^)]*)\)", line)
        if not m:
            continue
        addr, hostname = m.group(1), m.group(2)
        services: list[NmapService] = []
        ports_m = re.search(r"Ports:\s+(.+?)(?:\t|Ignored|$)", line)
        if ports_m:
            for entry in ports_m.group(1).split(","):
                parts = [p.strip() for p in entry.strip().split("/")]
                if len(parts) < 3:
                    continue
                try:
                    port_num = int(parts[0])
                except ValueError:
                    continue
                state = parts[1] if len(parts) > 1 else ""
                proto = parts[2] if len(parts) > 2 else "tcp"
                if state != "open":
                    continue
                name = parts[4] if len(parts) > 4 else ""
                product = parts[6] if len(parts) > 6 else ""
                services.append(NmapService(
                    port=port_num, protocol=proto,
                    name=name, product=product, notes="",
                ))
        hosts.append(NmapHost(address=addr, hostname=hostname, os_guess="", services=services))
    return hosts
