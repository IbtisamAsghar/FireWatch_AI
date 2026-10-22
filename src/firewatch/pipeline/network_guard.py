"""One-way network isolation for dedicated acceptance child processes only."""

import socket
import sys
import urllib.request
from typing import Any

import httpx
from sqlalchemy.engine import make_url


def install_database_only_network_guard(database_url: str) -> list[str]:
    """Irreversible in this interpreter. Call only inside a disposable acceptance process.

    Python DNS/socket connections are restricted to the configured DB endpoint;
    provider HTTP transports are denied even when a provider would resolve locally.
    Native libpq remains available for PostgreSQL. This is process-level acceptance
    isolation, not a host firewall or a general native-code sandbox.
    """
    url = make_url(database_url)
    host, port = url.host or "", url.port or 5432
    allowed = {host}
    for entry in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        allowed.add(str(entry[4][0]))
    attempts: list[str] = []

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event == "socket.getaddrinfo":
            address, service = str(args[0]), args[1]
        elif event in {"socket.connect", "socket.connect_ex"}:
            endpoint = args[1]
            if not isinstance(endpoint, tuple) or len(endpoint) < 2:
                attempts.append("socket-blocked")
                raise PermissionError("Acceptance allows database connections only")
            address, service = str(endpoint[0]), endpoint[1]
        else:
            return
        if address not in allowed or str(service) != str(port):
            attempts.append("network-blocked")
            raise PermissionError("Acceptance blocks outbound network except PostgreSQL")

    def deny_http(*args: Any, **kwargs: Any) -> Any:
        attempts.append("http-blocked")
        raise PermissionError("Provider HTTP disabled during acceptance")

    async def deny_async_http(*args: Any, **kwargs: Any) -> Any:
        return deny_http(*args, **kwargs)

    sys.addaudithook(audit)
    client_type: Any = httpx.Client
    client_type.send = deny_http
    async_client_type: Any = httpx.AsyncClient
    async_client_type.send = deny_async_http
    urllib.request.urlopen = deny_http
    return attempts
