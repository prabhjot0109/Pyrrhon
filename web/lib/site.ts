/**
 * Single source of truth for the handful of strings that appear in more than
 * one place on the site. Keeps the repo URL and the install command from
 * drifting across the header, hero, install section and footer.
 */

export const REPO_URL = "https://github.com/prabhjot0109/Pyrrhon"

/** The PyPI distribution name. `pyrrhon` on PyPI, `pyrrhon` on your PATH. */
export const PACKAGE = "pyrrhon"

/**
 * The one command in the hero and install section.
 *
 * `pip install` installs directly into the active Python environment.
 * The alternatives below cover isolated environments and one-off runs.
 */
export const INSTALL_COMMAND = `pip install ${PACKAGE}`

export const INSTALL_ALTERNATIVES = [
  {
    tool: "uv tool",
    command: `uv tool install ${PACKAGE}`,
    note: "Isolated environment with the pyrrhon binary on your PATH (install uv first).",
  },
  {
    tool: "uvx",
    command: `uvx ${PACKAGE}`,
    note: "Run it once without installing anything (install uv first).",
  },
  {
    tool: "pipx",
    command: `pipx install ${PACKAGE}`,
    note: "Isolated environment with pipx, if that is what you already have.",
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
