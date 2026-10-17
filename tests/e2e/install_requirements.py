"""Install the PyPI requirements of the Home Assistant integrations the e2e run uses.

A pip-installed Home Assistant normally fetches these on demand at startup; in CI that did
not happen (the frontend package was missing and HA fell into recovery mode), so do it up
front, deterministically.   usage: python tests/e2e/install_requirements.py [--dry-run]
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

SEEDS = (
    "frontend", "config", "lovelace", "onboarding", "auth", "http", "websocket_api", "api", "logger", "repairs",
    "calendar", "todo", "button", "binary_sensor", "sensor", "diagnostics", "system_log", "search",
)


def _base_components(core: Path) -> set[str]:
    """Home Assistant imports a set of "base" integrations just to validate any services.yaml
    (service.py::_base_components) - and they need their own packages (hassil, ...)."""
    source = (core / "helpers" / "service.py").read_text(encoding="utf-8")
    match = re.search(r"def _base_components\(.*?from homeassistant\.components import \((.*?)\)", source, re.S)
    return set(re.findall(r"[a-z_0-9]+", match.group(1))) - {"noqa", "PLC0415"} if match else set()


def closure() -> tuple[set[str], set[str]]:
    spec = importlib.util.find_spec("homeassistant")
    core = Path(list(spec.submodule_search_locations)[0])
    components = core / "components"
    seen: set[str] = set()
    requirements: set[str] = set()

    def visit(domain: str) -> None:
        if domain in seen:
            return
        seen.add(domain)
        manifest = components / domain / "manifest.json"
        if not manifest.exists():
            return
        data = json.loads(manifest.read_text(encoding="utf-8"))
        requirements.update(data.get("requirements", []))
        for dependency in data.get("dependencies", []):
            visit(dependency)

    for seed in (*SEEDS, *sorted(_base_components(core))):
        visit(seed)
    return seen, requirements


def main() -> None:
    domains, requirements = closure()
    print(f"{len(domains)} integrations, {len(requirements)} requirements")
    for requirement in sorted(requirements):
        print("  ", requirement)
    if "--dry-run" in sys.argv:
        return
    if requirements:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", *sorted(requirements)], check=True)


if __name__ == "__main__":
    main()
