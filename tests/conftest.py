"""CI rejects accidental provider/network access; loopback PostgreSQL remains usable."""

import ipaddress
import os
import socket

import pytest


def loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def offline_network():
    if os.getenv("FIREWATCH_OFFLINE_TESTS") != "1":
        yield
        return
    connect, getaddrinfo = socket.socket.connect, socket.getaddrinfo

    def guarded_connect(self, address):
        if self.family in {socket.AF_INET, socket.AF_INET6} and not loopback(address[0]):
            raise AssertionError("Offline tests attempted external network access")
        return connect(self, address)

    def guarded_lookup(host, *args, **kwargs):
        if not loopback(host):
            raise AssertionError("Offline tests attempted external DNS lookup")
        return getaddrinfo(host, *args, **kwargs)

    # Own the network patch independently. Requesting the shared monkeypatch fixture
    # here would make test-specific DB patches outlive dependent DB fixture cleanup.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket.socket, "connect", guarded_connect)
        patch.setattr(socket, "getaddrinfo", guarded_lookup)
        yield
