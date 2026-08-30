"""The schema the model is shown must match the signature that runs.

A tool carries two descriptions of its own arity: the JSON schema the model
reads, and the `run()` signature Python enforces. They are written by hand in
two places, so they drift — and the drift is silent until a live turn spends a
round on `ERROR: bad arguments for repo_map`. The model cannot see a signature,
so a schema that promises a parameter `run()` will not take, or omits one it
demands, is a trap the model walks into and cannot diagnose.

One parametrised test over the whole belt replaces a review checklist item on
every tool ever added. The signature is the truth and the schema is the copy:
where they disagree, the schema is what gets fixed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pyrrhon.bootstrap import build_agent
from pyrrhon.core.tools.base import run_parameters
from tests.helpers import FakeLLM


def _build_belt() -> dict:
    # Built at import so the belt can parametrise by tool name, which is what
    # makes a failure say WHICH tool disagreed without reading a traceback.
    # home=tmp isolates from the developer's real ~/.pyrrhon/plugins, exactly
    # as tests/test_safety.py does — a global plugin's tool is not our belt.
    tmp = Path(tempfile.mkdtemp(prefix="pyrrhon-belt-"))
    agent = build_agent(tmp, llm=FakeLLM([]), deep_llm=FakeLLM([]), home=tmp)
    return agent.tools


BELT = _build_belt()
EVERY_TOOL = pytest.mark.parametrize("tool", list(BELT.values()), ids=list(BELT))


@EVERY_TOOL
def test_schema_promises_no_parameter_run_will_not_take(tool):
    accepted = run_parameters(tool)
    promised = set(tool.parameters.get("properties", {}))
    assert promised <= set(accepted), (
        f"{tool.name}'s schema offers {sorted(promised - set(accepted))}, "
        f"which run() does not take"
    )


@EVERY_TOOL
def test_every_required_key_is_a_parameter_without_a_default(tool):
    accepted = run_parameters(tool)
    for key in tool.parameters.get("required", []):
        assert key in accepted, f"{tool.name} requires '{key}', which run() does not take"
        assert accepted[key], (
            f"{tool.name} requires '{key}', but run() gives it a default — "
            f"the schema is stricter than the code"
        )


@EVERY_TOOL
def test_every_mandatory_parameter_is_declared_required(tool):
    """A parameter with no default is one the model MUST send.

    Leaving it out of `required` invites a call that raises TypeError, which
    costs a round to discover and tells the model nothing it can act on.
    """
    required = set(tool.parameters.get("required", []))
    mandatory = {name for name, needed in run_parameters(tool).items() if needed}
    assert mandatory <= required, (
        f"{tool.name} must be called with {sorted(mandatory - required)}, "
        f"but its schema does not say so"
    )


@EVERY_TOOL
def test_every_schema_forbids_arguments_it_did_not_declare(tool):
    """Closed schemas, centrally.

    A tool cannot forget this: `Tool.schema` sets it. The check runs against
    `schema()` rather than `parameters` because the former is what is sent.
    """
    assert tool.schema()["function"]["parameters"].get("additionalProperties") is False


def test_repo_map_declares_no_arguments_and_forbids_extras():
    """The pair is what makes the tool's arity legible.

    Empty `properties` alone reads as "nothing worth describing", not "takes
    nothing" — and a live session read it the first way, calling repo_map with
    an argument and spending a round on the rejection.
    """
    schema = BELT["repo_map"].schema()["function"]["parameters"]
    assert schema.get("properties") == {}
    assert schema.get("additionalProperties") is False
    assert not run_parameters(BELT["repo_map"])
