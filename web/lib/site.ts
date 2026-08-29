/**
 * Single source of truth for the handful of strings that appear in more than
 * one place on the site. Keeps the repo URL and the install command from
 * drifting across the header, hero, install section and footer.
 */

export const REPO_URL = "https://github.com/prabhjot0109/Pyrrhon"

/** The PyPI distribution name. `pyrrhon` on PyPI, `pyrrhon` on your PATH. */
export const PACKAGE = "pyrrhon"

/**
 * The one command in the hero.
 *
 * `uv tool install` rather than `pip install` because Pyrrhon is an
 * application, not a library: it wants its own isolated environment with the
 * `pyrrhon` binary linked onto PATH, which is precisely what a pip install
 * into whatever interpreter happens to be active does not give you. The
 * alternatives below cover people who do not have uv.
 */
export const INSTALL_COMMAND = `uv tool install ${PACKAGE}`

export const INSTALL_ALTERNATIVES = [
  {
    tool: "uvx",
    command: `uvx ${PACKAGE} .`,
    note: "Run it once without installing anything.",
  },
  {
    tool: "pipx",
    command: `pipx install ${PACKAGE}`,
    note: "Same isolation as uv, if that is what you already have.",
  },
  {
    tool: "pip",
    command: `pip install ${PACKAGE}`,
    note: "Into the active environment. Works, but shares its dependencies.",
  },
] as const

export const SITE = {
  name: "Pyrrhon",
  tagline: "A voice-first engineering agent for your terminal.",
  repo: REPO_URL,
  pypi: `https://pypi.org/project/${PACKAGE}/`,
  /** Files worth linking straight into, rather than duplicating on the site. */
  links: {
    readme: `${REPO_URL}#readme`,
    vision: `${REPO_URL}/blob/main/VISION.md`,
    issues: `${REPO_URL}/issues`,
    license: `${REPO_URL}/blob/main/LICENSE`,
  },
} as const
