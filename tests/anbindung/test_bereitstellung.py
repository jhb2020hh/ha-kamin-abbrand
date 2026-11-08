"""Die Anmeldung darf keinen festen Hostnamen verwenden."""
import socket

from anbindung import bereitstellung


def test_host_kommt_vom_container(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "a1b2c3d4-kamin-abbrand")
    assert bereitstellung.eigener_host() == "a1b2c3d4-kamin-abbrand"
