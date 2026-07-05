from pyrrhon.core.agent.design_prompts import DESIGN_PROMPT
from pyrrhon.core.agent.prompts import SYSTEM_PROMPT
from pyrrhon.core.tools.spec_writer import SPEC_FILENAMES


def test_design_prompt_encodes_the_skeptic_policy():
    lower = DESIGN_PROMPT.lower()
    assert "never agree" in lower
    assert "weakest assumption" in lower
    assert "one question" in lower


def test_design_prompt_carries_the_mongo_postgres_exemplar_and_artifacts():
    assert "MongoDB" in DESIGN_PROMPT
    assert "Postgres" in DESIGN_PROMPT
    assert "relational" in DESIGN_PROMPT
    for name in SPEC_FILENAMES:
        assert name in DESIGN_PROMPT, f"{name} missing from DESIGN_PROMPT"


def test_design_prompt_demands_reasoning_before_and_inside_specs():
    lower = DESIGN_PROMPT.lower()
    for choice in ("data model", "interfaces", "failure modes", "scale"):
        assert choice in lower, f"key choice '{choice}' missing"
    assert "write_spec" in DESIGN_PROMPT
    assert "reasoning" in lower


def test_understand_prompt_forbids_spec_writing():
    assert "write_spec" in SYSTEM_PROMPT
    assert "/mode design" in SYSTEM_PROMPT
