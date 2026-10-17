"""End-to-end harness: a REAL Home Assistant process with this integration installed,
talking to a fake DietoPro (fake_dietopro.py). Run with:  pytest tests/e2e -o addopts=""
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import pytest
import requests

from .fake_dietopro import RunningFake

REPO = Path(__file__).resolve().parents[2]
HA_PORT = int(os.environ.get("E2E_HA_PORT", "18123"))
FAKE_PORT = int(os.environ.get("E2E_FAKE_PORT", "18999"))
BASE = f"http://127.0.0.1:{HA_PORT}"
CLIENT_ID = f"{BASE}/"
USER, PASSWORD = "e2e", "e2e-password-123"
STARTUP_TIMEOUT = int(os.environ.get("E2E_STARTUP_TIMEOUT", "600"))

CONFIGURATION = f"""
homeassistant:
  name: e2e
  latitude: 40.4
  longitude: -3.7
  elevation: 600
  unit_system: metric
  time_zone: Europe/Madrid
  country: ES
  language: es
http:
  server_port: {HA_PORT}
frontend:
config:
logger:
  default: warning
  logs:
    custom_components.nutriplan_bridge: debug
"""


class HA:
    def __init__(self, config_dir: Path, proc: subprocess.Popen, log: Path) -> None:
        self.config_dir, self.proc, self.log = config_dir, proc, log
        self.token = ""
        self.refresh_token = ""

    # ---- REST
    def api(self, method: str, path: str, **kw):
        headers = {"Authorization": f"Bearer {self.token}"}
        return requests.request(method, f"{BASE}{path}", headers=headers, timeout=60, **kw)

    def state(self, entity_id: str) -> dict:
        r = self.api("GET", f"/api/states/{entity_id}")
        assert r.status_code == 200, f"{entity_id}: {r.status_code} {r.text[:200]}"
        return r.json()

    def states(self) -> list[dict]:
        return self.api("GET", "/api/states").json()

    def find(self, domain: str, suffix: str) -> str:
        """entity_id of the integration's entity whose id ends with `suffix` (as the card does)."""
        for s in self.states():
            eid = s["entity_id"]
            if eid.startswith(f"{domain}.nutriplan_bridge") and eid.endswith(suffix):
                return eid
        raise AssertionError(f"no {domain}.nutriplan_bridge*{suffix} among: " + ", ".join(sorted(s["entity_id"] for s in self.states() if "nutriplan" in s["entity_id"])))

    def service(self, name: str, data: dict | None = None, response: bool = False, domain: str = "nutriplan_bridge"):
        r = self.api("POST", f"/api/services/{domain}/{name}" + ("?return_response" if response else ""), json=data or {})
        return r

    # ---- websocket (one request per connection: simple and enough for tests)
    def ws(self, payload: dict) -> dict:
        async def go() -> dict:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(f"{BASE}/api/websocket") as ws:
                    await ws.receive_json()
                    await ws.send_json({"type": "auth", "access_token": self.token})
                    assert (await ws.receive_json())["type"] == "auth_ok"
                    await ws.send_json({"id": 1, **payload})
                    return await ws.receive_json()

        return asyncio.run(go())

    def log_text(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace") if self.log.exists() else ""

    def wait_for(self, predicate, timeout: float = 60, interval: float = 0.5, what: str = "condition"):
        end = time.time() + timeout
        last = None
        while time.time() < end:
            try:
                last = predicate()
                if last:
                    return last
            except AssertionError:
                pass
            time.sleep(interval)
        raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture(scope="session")
def fake() -> RunningFake:
    server = RunningFake(FAKE_PORT).start()
    yield server
    server.stop()


def _login(ha: HA) -> None:
    flow = requests.post(
        f"{BASE}/auth/login_flow",
        json={"client_id": CLIENT_ID, "handler": ["homeassistant", None], "redirect_uri": CLIENT_ID},
        timeout=30,
    ).json()
    step = requests.post(
        f"{BASE}/auth/login_flow/{flow['flow_id']}",
        json={"client_id": CLIENT_ID, "username": USER, "password": PASSWORD},
        timeout=30,
    ).json()
    assert step.get("type") == "create_entry", step
    tokens = requests.post(
        f"{BASE}/auth/token",
        data={"grant_type": "authorization_code", "code": step["result"], "client_id": CLIENT_ID},
        timeout=30,
    ).json()
    ha.token, ha.refresh_token = tokens["access_token"], tokens["refresh_token"]


@pytest.fixture(scope="session")
def ha(tmp_path_factory, fake) -> HA:
    config_dir = tmp_path_factory.mktemp("ha_config")
    (config_dir / "configuration.yaml").write_text(CONFIGURATION, encoding="utf-8")
    shutil.copytree(REPO / "custom_components" / "nutriplan_bridge", config_dir / "custom_components" / "nutriplan_bridge",
                    ignore=shutil.ignore_patterns("__pycache__"))
    # Onboarding already done, so the frontend goes straight to the dashboard.
    storage = config_dir / ".storage"
    storage.mkdir()
    (storage / "onboarding").write_text(
        json.dumps({"version": 4, "minor_version": 1, "key": "onboarding",
                    "data": {"done": ["user", "core_config", "integration", "analytics"]}}),
        encoding="utf-8",
    )
    env = {**os.environ, "NUTRIPLAN_BRIDGE_BASE_URL": fake.url, "PYTHONUNBUFFERED": "1"}
    # The admin user (HA's own helper script).
    subprocess.run([sys.executable, "-m", "homeassistant", "--script", "auth", "-c", str(config_dir), "add", USER, PASSWORD],
                   check=True, env=env, capture_output=True, timeout=300)
    log = config_dir / "ha_stdout.log"
    handle = open(log, "wb")
    proc = subprocess.Popen([sys.executable, "-m", "homeassistant", "-c", str(config_dir)], env=env, stdout=handle, stderr=subprocess.STDOUT)
    instance = HA(config_dir, proc, log)
    try:
        deadline = time.time() + STARTUP_TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("Home Assistant exited early:\n" + instance.log_text()[-3000:])
            try:
                if requests.get(f"{BASE}/auth/providers", timeout=2).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(2)
        else:
            raise RuntimeError("Home Assistant did not start in time:\n" + instance.log_text()[-3000:])
        _login(instance)
        yield instance
    finally:
        proc.terminate()
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            proc.kill()
        handle.close()
        artifacts = os.environ.get("E2E_ARTIFACTS")
        if artifacts:
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            shutil.copy(log, Path(artifacts) / "ha_stdout.log")
            ha_log = config_dir / "home-assistant.log"
            if ha_log.exists():
                shutil.copy(ha_log, Path(artifacts) / "home-assistant.log")


@pytest.fixture(scope="session")
def entry(ha, fake) -> str:
    """Add the integration through its real config flow (the same one a user goes through)."""
    started = ha.api("POST", "/api/config/config_entries/flow", json={"handler": "nutriplan_bridge", "show_advanced_options": False}).json()
    assert started["type"] == "form" and started["step_id"] == "user", started
    done = ha.api("POST", f"/api/config/config_entries/flow/{started['flow_id']}", json={"email": "t@example.com", "password": fake.state.valid_password}).json()
    assert done["type"] == "create_entry", done
    entry_id = done["result"]["entry_id"]
    ha.wait_for(lambda: ha.find("sensor", "_plan_actual"), timeout=120, what="entities to be created")
    return entry_id
