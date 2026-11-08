"""Bringt die mitgelieferte HA-Integration an ihren Platz und meldet die App an HA.

Kopiert wird nur der eine Ordner custom_components/kamin_abbrand. Einen HA-Neustart
löst die App nicht aus, sie zeigt ihn auf der Seite Service an.
"""
from __future__ import annotations

import asyncio
import filecmp
import logging
import shutil
import socket
from pathlib import Path

import aiohttp

log = logging.getLogger(__name__)

QUELLE = Path(__file__).parent.parent / "integration" / "kamin_abbrand"
HA_CONFIG = Path("/homeassistant")
PORT_INTEGRATION = 8100
DISCOVERY_URL = "http://supervisor/discovery"


def eigener_host() -> str:
    """Der Supervisor setzt den Hostnamen des Containers. Er enthält die ID des Repositorys."""
    return socket.gethostname()


def _gleich(a: Path, b: Path) -> bool:
    vergleich = filecmp.dircmp(a, b, ignore=["__pycache__"])
    if vergleich.left_only or vergleich.right_only or vergleich.diff_files or vergleich.funny_files:
        return False
    return all(_gleich(a / d, b / d) for d in vergleich.common_dirs)


def kopieren(quelle: Path = QUELLE, ha_config: Path = HA_CONFIG) -> dict:
    """Gibt zurück, ob die Integration da ist und ob HA neu starten muss."""
    if not ha_config.is_dir() or not quelle.is_dir():
        return {"installiert": False, "neustart_noetig": False, "text": "Der HA-Ordner ist nicht erreichbar."}
    ziel = ha_config / "custom_components" / "kamin_abbrand"
    if ziel.is_dir() and _gleich(quelle, ziel):
        return {"installiert": True, "neustart_noetig": False, "text": "Die Integration ist aktuell."}
    ziel.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(ziel, ignore_errors=True)
    shutil.copytree(quelle, ziel, ignore=shutil.ignore_patterns("__pycache__"))
    log.info("Integration nach %s kopiert, HA muss einmal neu starten", ziel)
    return {"installiert": True, "neustart_noetig": True,
            "text": "Die Integration wurde kopiert. Sie erscheint nach einem Neustart von Home Assistant."}


async def anmelden(token_integration: str, supervisor_token: str, url: str = DISCOVERY_URL) -> bool:
    """Bietet HA die Integration an. Scheitert das, fügt man sie von Hand hinzu."""
    try:
        async with aiohttp.ClientSession() as sitzung, asyncio.timeout(10):
            async with sitzung.post(
                url,
                json={"service": "kamin_abbrand",
                      "config": {"host": eigener_host(), "port": PORT_INTEGRATION, "token": token_integration}},
                headers={"Authorization": f"Bearer {supervisor_token}"},
            ) as antwort:
                if antwort.status >= 300:
                    log.warning("Supervisor-Discovery abgelehnt: HTTP %s", antwort.status)
                    return False
                return True
    except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as fehler:
        log.warning("Supervisor-Discovery nicht möglich: %r", fehler)
        return False


async def integration_bereitstellen(token_integration: str, supervisor_token: str) -> dict:
    try:
        stand = await asyncio.to_thread(kopieren)
    except OSError as fehler:
        log.error("Integration nicht kopiert: %r", fehler)
        stand = {"installiert": False, "neustart_noetig": False, "text": "Die Integration konnte nicht kopiert werden."}
    stand["discovery"] = await anmelden(token_integration, supervisor_token)
    return stand
