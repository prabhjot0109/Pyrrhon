/**
 * Single source of truth for the handful of strings that appear in more than
 * one place on the site. Keeps the repo URL from drifting across the header,
 * hero, CTA and footer.
 */

export const REPO_URL = "https://github.com/prabhjot0109/Pyrrhon"

export const SITE = {
  name: "Pyrrhon",
  tagline: "A voice-first engineering agent for your terminal.",
  repo: REPO_URL,
  /** Files worth linking straight into, rather than duplicating on the site. */
  links: {
    readme: `${REPO_URL}#readme`,
    vision: `${REPO_URL}/blob/main/VISION.md`,
    issues: `${REPO_URL}/issues`,
    license: `${REPO_URL}/blob/main/LICENSE`,
  },
} as const
