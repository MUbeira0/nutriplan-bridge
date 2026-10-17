"""Found by the end-to-end run against a real Home Assistant."""
import inspect
import re
from pathlib import Path

import custom_components.nutriplan_bridge as integration
from custom_components.nutriplan_bridge import button


def test_buttons_stay_pressable_while_dietopro_is_failing(coord, entry):
    coord.last_update_success = False
    assert button.RefreshButton(coord, entry).available is True
    assert button.MarkChatReadButton(coord, entry).available is True


def test_manifest_is_not_read_inside_the_event_loop():
    source = inspect.getsource(integration.async_setup)
    assert "async_add_executor_job(_integration_version)" in source
    assert not re.search(r"(?<!_job\()_integration_version\(\)", source.replace("async_add_executor_job(_integration_version)", ""))
