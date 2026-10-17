"""Install the PyPI requirements of the Home Assistant integrations the e2e run uses.

A pip-installed Home Assistant normally fetches these on demand at startup; in CI that did
not happen (the frontend package was missing and HA fell into recovery mode), so do it up
front, deterministically.   usage: python tests/e2e/install_requirements.py [--dry-run]
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SEEDS = (
    "frontend", "config", "lovelace", "onboarding", "auth", "http", "websocket_api", "api", "logger", "repairs",
    "calendar", "todo", "button", "binary_sensor", "sensor", "diagnostics", "system_log", "search",
)


def closure() -> tuple[set[str], set[str]]:
    spec = importlib.util.find_spec("homeassistant")
    components = Path(list(spec.submodule_search_locations)[0]) / "components"
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

    for seed in SEEDS:
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
