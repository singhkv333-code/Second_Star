"""The only way out for the remote browser's Chromium.

Chromium is started with --proxy-server pointing here and with the implicit
loopback bypass removed, so every request it makes — pages, scripts, images,
WebSockets — arrives at this proxy first. The proxy resolves the name ONCE,
refuses it if any answer is not a public address (private ranges, loopback,
link-local and the cloud metadata address 169.254.169.254, carrier NAT,
reserved), and connects to exactly the address it checked, so a name that
resolves differently on a second lookup cannot get inside. Ports 80 and 443
only.

This is the browser's network boundary; the container's own network rules sit
behind it as a second one.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket

log = logging.getLogger("egress")
PORTS = {80, 443}
refused: dict[str, float] = {}   # host -> when; read by the session to explain a failed load


def _ok(a: str) -> bool:
    ip = ipaddress.ip_address(a.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def _resolve(host: str, port: int) -> str | None:
    host = host.strip("[]")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return None
    addrs = [i[4][0] for i in infos]
    if not addrs or not all(_ok(a) for a in addrs):
        return None
    # prefer IPv4: the container may have no IPv6 route
    return next((a for a in addrs if ":" not in a), addrs[0])


async def _pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await r.read(65536)
            if not chunk:
                break
            w.write(chunk)
            await w.drain()
    except (ConnectionError, asyncio.CancelledError, OSError):
        pass
    finally:
        try:
            w.close()
        except Exception:  # noqa: BLE001
            pass


async def _refuse(w: asyncio.StreamWriter, why: str) -> None:
    body = why.encode()
    w.write(b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nConnection: close\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    try:
        await w.drain()
    finally:
        w.close()


async def _handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(cr.readuntil(b"\r\n\r\n"), 30)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
        cw.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, version = lines[0].split(" ", 2)
    except ValueError:
        cw.close()
        return
    if method == "CONNECT":
        host, _, port_s = target.rpartition(":")
        port = int(port_s) if port_s.isdigit() else 0
        rest = b""
    else:
        # plain http: absolute-form request line
        if not target.startswith("http://"):
            return await _refuse(cw, "bad request")
        hostport, _, path = target[7:].partition("/")
        host, _, port_s = hostport.rpartition(":") if ":" in hostport.split("]")[-1] else (hostport, "", "80")
        port = int(port_s) if port_s.isdigit() else 0
        hdrs = [h for h in lines[1:] if h and not h.lower().startswith(("proxy-connection:", "connection:", "keep-alive:"))]
        # one request per connection: the next request may be for another host
        rest = (f"{method} /{path} {version}\r\n" + "\r\n".join(hdrs) + "\r\nConnection: close\r\n\r\n").encode("latin-1")
    if port not in PORTS:
        refused[host.strip("[]")] = asyncio.get_running_loop().time()
        return await _refuse(cw, "port not allowed")
    ip = await _resolve(host, port)
    if not ip:
        log.info("refused %s", host)
        if len(refused) > 1000:
            refused.clear()
        refused[host.strip("[]")] = asyncio.get_running_loop().time()
        return await _refuse(cw, "not a public address")
    try:
        ur, uw = await asyncio.wait_for(asyncio.open_connection(ip, port), 15)
    except (OSError, asyncio.TimeoutError):
        cw.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
        cw.close()
        return
    if method == "CONNECT":
        cw.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await cw.drain()
    else:
        uw.write(rest)
        await uw.drain()
    await asyncio.gather(_pipe(cr, uw), _pipe(ur, cw))


async def serve(port: int = 8899) -> asyncio.AbstractServer:
    return await asyncio.start_server(_handle, "127.0.0.1", port, limit=1 << 16)
