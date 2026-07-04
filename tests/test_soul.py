from pathlib import Path

from pyrrhon.core.agent.soul import build_system_prompt, load_soul


def test_no_soul_files_yields_empty(tmp_path: Path):
    assert load_soul(repo_root=tmp_path, home=tmp_path / "nohome") == ""


def test_repo_soul_is_loaded_after_global(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".pyrrhon").mkdir(parents=True)
    (home / ".pyrrhon" / "soul.md").write_text("I am global.", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".pyrrhon").mkdir(parents=True)
    (repo / ".pyrrhon" / "soul.md").write_text("I am repo-level.", encoding="utf-8")
    (repo / ".pyrrhon" / "skill.md").write_text("Custom skill notes.", encoding="utf-8")

    soul = load_soul(repo_root=repo, home=home)
    assert soul.index("I am global.") < soul.index("I am repo-level.")
    assert "Custom skill notes." in soul
    assert "## From soul.md" in soul


def test_build_system_prompt_mentions_repo_and_soul(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".pyrrhon").mkdir(parents=True)
    (repo / ".pyrrhon" / "soul.md").write_text("Prefers first principles.", encoding="utf-8")
    prompt = build_system_prompt(repo_root=repo, home=tmp_path / "nohome")
    assert str(repo) in prompt
    assert "Prefers first principles." in prompt
    assert "path:line" in prompt  # citation rule present
