"""Every callback we hand to HA's time trackers must run ON the event loop.

HA decides by looking at the function: a @callback runs in the loop, a plain
function or lambda is pushed to the executor (a worker thread). Our periodic
refreshers used lambdas, so async_write_ha_state() ran in a thread and HA
raised "calls async_write_ha_state from a thread" every minute.
"""
import asyncio
import inspect
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HassJob, HassJobType

from custom_components.nutriplan_bridge import binary_sensor, sensor


def _capture_tracker(module, entity):
    """Run async_added_to_hass and return the function given to async_track_time_interval."""
    entity.hass = MagicMock()
    entity.async_on_remove = MagicMock()
    seen = {}

    def fake_track(hass, action, interval):
        seen["action"], seen["interval"] = action, interval
        return lambda: None

    async def run():
        with patch.object(module, "async_track_time_interval", fake_track), patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass", new=_noop
        ):
            await entity.async_added_to_hass()

    asyncio.run(run())
    return seen


async def _noop(self):
    return None


@pytest.mark.parametrize(
    "module,factory,minutes",
    [
        (sensor, lambda c, e: sensor.DietoProNextMealSensor(c, e), 1),
        (binary_sensor, lambda c, e: binary_sensor.AppointmentSoonBinary(c, e), 5),
    ],
)
def test_periodic_refresh_runs_on_the_event_loop(coord, entry, module, factory, minutes):
    entity = factory(coord, entry)
    seen = _capture_tracker(module, entity)
    assert seen["interval"] == timedelta(minutes=minutes)
    assert HassJob(seen["action"]).job_type is HassJobType.Callback  # not Executor


def test_no_time_tracker_gets_a_bare_lambda():
    import re
    from pathlib import Path

    pkg = Path(__file__).resolve().parent.parent / "custom_components" / "nutriplan_bridge"
    for path in pkg.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"async_track_\w+\([^)]*lambda", text), f"lambda passed to a HA tracker in {path.name}"
