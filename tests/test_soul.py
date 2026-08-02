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


# -- the character cap (M10 Stage 2.2) --------------------------------------
#
# Soul files are re-sent on every round of every turn. A full memory.md at the
# 200-bullet cap pushed the system prompt past 26 KB (~6.6k tokens), several
# times the cost of the entire tool belt, paid again on each tool round.


def _memory(repo: Path, bullets: int) -> None:
    (repo / ".pyrrhon").mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        f"- [2026-07-01] fact number {i} padded out to a realistic length "
        f"so the file reaches a realistic size"
        for i in range(bullets)
    )
    (repo / ".pyrrhon" / "memory.md").write_text(
        "# Memory\n" + body, encoding="utf-8"
    )


def test_soul_is_capped(tmp_path: Path):
    repo = tmp_path / "repo"
    _memory(repo, 200)
    soul = load_soul(repo_root=repo, home=tmp_path / "nohome", max_chars=2000)
    assert len(soul) < 2500  # cap plus the section header


def test_memory_keeps_its_newest_entries(tmp_path: Path):
    """RememberTool appends, so the tail is the recent end. Trimming the tail
    would discard exactly the facts most likely to still matter."""
    repo = tmp_path / "repo"
    _memory(repo, 200)
    soul = load_soul(repo_root=repo, home=tmp_path / "nohome", max_chars=2000)
    assert "fact number 199 " in soul
    assert "fact number 0 " not in soul


def test_a_short_soul_is_untouched(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".pyrrhon").mkdir(parents=True)
    (repo / ".pyrrhon" / "soul.md").write_text("Short and sweet.", encoding="utf-8")
    soul = load_soul(repo_root=repo, home=tmp_path / "nohome")
    assert soul == "## From soul.md\n\nShort and sweet."


def test_repo_context_wins_the_budget_over_global(tmp_path: Path):
    """Budget is claimed in reverse load order. Allocating forward would let a
    large global file starve the repo's own notes, inverting the documented
    "repo-level context wins" precedence."""
    home = tmp_path / "home"
    (home / ".pyrrhon").mkdir(parents=True)
    (home / ".pyrrhon" / "soul.md").write_text("G" * 5000, encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".pyrrhon").mkdir(parents=True)
    (repo / ".pyrrhon" / "soul.md").write_text("REPO-CRITICAL", encoding="utf-8")

    soul = load_soul(repo_root=repo, home=home, max_chars=1000)
    assert "REPO-CRITICAL" in soul


def test_an_unreadable_soul_file_does_not_break_startup(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".pyrrhon").mkdir(parents=True)
    # A directory named *.md: read_text raises OSError, not FileNotFoundError.
    (repo / ".pyrrhon" / "weird.md").mkdir()
    (repo / ".pyrrhon" / "soul.md").write_text("still here", encoding="utf-8")
    assert "still here" in load_soul(repo_root=repo, home=tmp_path / "nohome")
