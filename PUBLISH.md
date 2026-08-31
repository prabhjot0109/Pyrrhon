# Publishing Pyrrhon to PyPI

How to get `pip install pyrrhon` working, and what to check before you do.

Everything below was run against this repo on 2026-09-01 with uv 0.12.7. Where
a number appears (artifact sizes, the installed footprint, the exact error a
wrong Python produces) it is measured, not estimated.

**Read this once before your first release.** A version number cannot be
re-used on PyPI. If a release ships something it should not have, the only
remedy is to yank it and burn a version, and whatever leaked stays in every
mirror that already fetched it.

---

## The one-paragraph version

```bash
uv run pytest && uv run ruff check . && uv run mypy pyrrhon/core
rm -rf dist && uv build
uv publish --index testpypi          # dry run against TestPyPI first
uv publish                            # the real thing
```

The rest of this file is why each of those steps is there and what to look at
in between.

---

## 0. One-time setup

### Claim the name

`pyrrhon` was unclaimed on PyPI as of 2026-09-01 (`GET /pypi/pyrrhon/json`
returns 404). Names are first-come, so the first successful upload claims it.
If someone has taken it by the time you read this, change `name` in
`pyproject.toml` and change it in the README's install line too.

### Create the accounts

You need two, and they are separate registrations with separate passwords:

- <https://pypi.org/account/register/> — the real index.
- <https://test.pypi.org/account/register/> — the rehearsal index.

Enable 2FA on both. PyPI requires it for new projects.

### Pick an authentication method

**Trusted publishing (recommended).** GitHub Actions gets a short-lived token
per run and you never store a secret anywhere. Set it up at
<https://pypi.org/manage/account/publishing/> with:

| Field | Value |
|---|---|
| PyPI project name | `pyrrhon` |
| Owner | `prabhjot0109` |
| Repository name | `Pyrrhon` |
| Workflow name | `release.yml` |
| Environment name | `pypi` (optional but worth setting) |

There is a worked workflow in section 6.

**API token (fine for a first manual release).** Generate one at
<https://pypi.org/manage/account/token/>, scoped to the whole account for the
first upload — a project-scoped token cannot exist before the project does.
Regenerate it as project-scoped once `pyrrhon` exists.

```bash
export UV_PUBLISH_TOKEN='pypi-AgEIcHl...'
```

Never put the token in a file that git tracks. `uv publish` reads
`UV_PUBLISH_TOKEN` from the environment.

For the TestPyPI rehearsal, add this to `pyproject.toml` so
`--index testpypi` resolves:

```toml
[[tool.uv.index]]
name = "testpypi"
url = "https://test.pypi.org/simple/"
publish-url = "https://test.pypi.org/legacy/"
explicit = true
```

---

## 1. Pre-flight

All three gate CI and all three must be clean. A release is the one moment
where "it is probably fine" costs a version number.

```bash
uv run pytest                 # 1119 passed, 1 skipped, 22 deselected
uv run ruff check .
uv run mypy pyrrhon/core
```

Then the layering greps from `CLAUDE.md`, which must both print nothing:

```bash
grep -rn "^from pyrrhon\.\(tui\|voice\|repl\|commands\|cli\)" pyrrhon/core/ pyrrhon/config/
grep -rn "^\s*\(from\|import\) pipecat" pyrrhon/core/ pyrrhon/config/
```

Set the version in **`pyrrhon/__init__.py`**, which is the single source.
`pyproject.toml` declares `dynamic = ["version"]` and hatchling reads it from
there, so the number on PyPI and the number the splash prints cannot disagree.
Do not add a `version =` line back to `pyproject.toml`; the pair used to exist
and nothing imported the pyproject one at runtime, so a release could have
shipped one number and printed another.

`0.1.0` is a first release and the classifiers say `Development Status :: 3 -
Alpha`, which is honest: the harness is code-complete and its runtime
verification is not done. See `CLAUDE.md`. If you would rather not publish an
unproven agent under a plain version, use `0.1.0rc1`, which PyPI marks as a
pre-release and `pip install pyrrhon` will not select by default.

---

## 2. Build

```bash
rm -rf dist && uv build
```

Two artifacts land in `dist/`. Measured on 2026-09-01:

| Artifact | Size |
|---|---|
| `pyrrhon-0.1.0-py3-none-any.whl` | 248 KB |
| `pyrrhon-0.1.0.tar.gz` | 366 KB |

`uv build` builds the wheel *from* the sdist, so if the sdist is missing a file
the wheel build fails rather than silently producing a good wheel beside a
broken tarball. That is worth knowing because it means checking the sdist is
not optional politeness — it is on the critical path.

### Check what is inside, every time

This is the step with teeth. The build is an **allowlist**
(`[tool.hatch.build.targets.sdist] only-include`), and it is an allowlist
because the denylist that preceded it failed open: three new top-level
directories appeared that it did not name, and a tarball went out carrying 70
files of session memory, 43 of internal review artifacts including three diffs
over 140 KB, 31 planning documents and `CLAUDE.md`. A denylist has to be
updated every time the repo grows. An allowlist has to be updated before
anything new can ship.

```bash
uv run python -c "
import tarfile, zipfile, glob
sd = glob.glob('dist/*.tar.gz')[0]
names = tarfile.open(sd).getnames()
print('sdist top level:', sorted({n.split('/')[1] for n in names if '/' in n}))
print('sdist files:', len(names))
wh = glob.glob('dist/*.whl')[0]
wn = zipfile.ZipFile(wh).namelist()
print('wheel top level:', sorted({n.split('/')[0] for n in wn}))
print('wheel files:', len(wn))
"
```

Expected, and anything else is a stop:

```
sdist top level: ['.gitignore', 'LICENSE', 'PKG-INFO', 'README.md', 'pyproject.toml', 'pyrrhon', 'tests']
sdist files: 214
wheel top level: ['pyrrhon', 'pyrrhon-0.1.0.dist-info']
wheel files: 97
```

If `docs`, `web`, `evals`, `.remember`, `.superpowers`, `.claude`, `.pyrrhon`
or `CLAUDE.md` appears, stop and fix `only-include` before uploading. Note that
`pyrrhon/evals/` **is** meant to be there — it is the package's own eval
module. The top-level `evals/` directory of YAML cases is not.

Do not rely on `.gitignore` to keep anything out. Hatchling honours it, so
`docs/` used to be excluded by accident, and it started shipping the moment
`docs/` was commented out of `.gitignore` for an unrelated reason.

### Check the metadata

```bash
uv run python -c "
import zipfile, glob
z = zipfile.ZipFile(glob.glob('dist/*.whl')[0])
md = [n for n in z.namelist() if n.endswith('METADATA')][0]
print(z.read(md).decode().split('\n\n')[0])
"
```

`Requires-Python: <3.14,>=3.12` must be there. Section 5 explains why the
ceiling exists and when to lift it.

---

## 3. Rehearse on TestPyPI

```bash
uv publish --index testpypi
```

Then install it into a throwaway environment. TestPyPI does not mirror the real
index, so dependencies have to come from PyPI:

```bash
uv venv /tmp/pyrrhon-check --python 3.12
uv pip install --python /tmp/pyrrhon-check \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  pyrrhon
/tmp/pyrrhon-check/bin/pyrrhon --version   # Scripts\pyrrhon.exe on Windows
```

The rehearsal catches the two failures that are invisible until someone
installs from an index: a missing runtime dependency that your dev environment
happened to have, and a console script that does not resolve. Both were checked
here and both are fine.

TestPyPI also burns version numbers. Bump the local version or append a suffix
if you need to rehearse twice.

---

## 4. Publish

```bash
uv publish
```

Then verify from the real index, in a clean environment, on the oldest
supported Python:

```bash
uv venv /tmp/pyrrhon-live --python 3.12
uv pip install --python /tmp/pyrrhon-live pyrrhon
/tmp/pyrrhon-live/bin/pyrrhon --help
```

Then tag and release:

```bash
git tag -a v0.1.0 -m "Pyrrhon 0.1.0"
git push origin v0.1.0
gh release create v0.1.0 --generate-notes
```

`pyproject.toml` points its `Changelog` URL at GitHub releases, so the release
notes are what a PyPI visitor follows.

---

## 5. What a user actually gets, and the two things that will surprise them

Be straight about both in the README. Neither is a bug and both are
surprising enough to read as one.

### It is a 1.2 GB install

Measured: a clean `uv venv --python 3.12` plus the wheel comes to **1199 MB**
of site-packages. The wheel itself is 248 KB. The weight is `torch`,
`torchaudio` and the on-device speech engines, which arrive because
`pipecat-ai[local,local-smart-turn,...]` is a single unconditional dependency
line rather than a `voice` extra.

That is a deliberate trade, argued in `pyproject.toml`: a provider the setup
menu offers and one command cannot start is a menu that lies, and
`tests/test_voice_registry.py::test_tier2_every_table_row_ships_installed`
fails if a new row outruns the dependency line. The cost is that a text-only
user pays for the audio stack.

If you later want a slim default, the change is to move the audio extras behind
`[project.optional-dependencies] voice` and make the voice registry's
`availability()` surface an install command for unbundled rows — which it
already knows how to do. That is a real design decision, not a packaging tweak,
because it reopens the menu-that-lies question. Do not make it as part of a
release.

### It needs Python 3.12 or 3.13, not 3.14

`pipecat-ai[local]` pulls `pyaudio`, whose latest release (0.2.14) ships wheels
for cp312 and cp313 and none for cp314. Without a ceiling, pip selected Pyrrhon
on 3.14 and then failed inside a C build of a dependency the user never named,
which reads exactly like a bug in Pyrrhon.

`requires-python = ">=3.12,<3.14"` turns that into:

```
ERROR: Package 'pyrrhon' requires a different Python: 3.14.2 not in '<3.14,>=3.12'
```

A sentence someone can act on. `tests/test_safety.py::test_requires_python_agrees_with_the_classifiers`
holds the ceiling and the classifier list in step, so moving one forces a
decision about the other.

**Lift the cap the release after pyaudio ships a cp314 wheel.** Check with:

```bash
uv run python -c "
import urllib.request, json, re
d = json.load(urllib.request.urlopen('https://pypi.org/pypi/pyaudio/json'))
v = d['info']['version']
tags = sorted({m.group(1) for f in d['releases'][v] if (m := re.search(r'-(cp3\d+)-', f['filename']))})
print(v, tags)
"
```

One caveat worth knowing: `uv pip install` is lenient about `Requires-Python`
for a package you name explicitly, so a uv user on 3.14 will still walk into
the compiler error. Real `pip` and `uv add` both enforce it. Nothing in the
package can change that.

---

## 6. Automating it

Trusted publishing, no stored secret. Save as `.github/workflows/release.yml`
and it fires when you push a `v*` tag.

```yaml
name: release
on:
  push:
    tags: ["v*"]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      # Same reason ci.yml does it: pyaudio ships no manylinux wheel and
      # compiles against portaudio.h, so the runner needs the headers. Without
      # this step `uv sync` fails on Ubuntu and the release never builds.
      - run: sudo apt-get update && sudo apt-get install -y portaudio19-dev
      - run: uv sync --dev
      - run: uv run pytest
      - run: uv run ruff check .
      - run: uv run mypy pyrrhon/core
      - run: uv build
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  publish:
    needs: build
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write        # this is what mints the trusted-publishing token
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - uses: pypa/gh-action-pypi-publish@release/v1
```

The `build` job runs the full gate before anything is uploaded, and the split
means a failing test cannot reach the `publish` job at all. `id-token: write`
is the whole trusted-publishing mechanism; without it the action has no
credential and fails.

The gate here is the same one `.github/workflows/ci.yml` already runs on every
push, deliberately repeated rather than assumed: a tag can be pushed at a
commit CI never saw. `uv sync` on a runner installs the same 1.2 GB, which is
what `enable-cache: true` is for.

---

## 7. If something goes wrong

**"File already exists."** That version was uploaded. Bump and rebuild; PyPI
will not accept a replacement under the same version, deliberately.

**Something private shipped.** Yank the release immediately from the project's
Manage page, fix `only-include`, and release a new version. Yanking hides it
from resolvers but does not delete it, and anything already mirrored stays
mirrored. This is the failure mode section 2's allowlist exists to prevent.

**A user reports an import error that you cannot reproduce.** You are almost
certainly testing in the repo, where the source tree is importable regardless
of what shipped. Reproduce in a clean venv against the built wheel, never
against a checkout.

**The console script is missing after install.** Check `[project.scripts]` in
`pyproject.toml` and that the wheel contains a `.dist-info/entry_points.txt`.
