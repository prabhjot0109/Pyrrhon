# web/

The Pyrrhon landing page. Next.js 15 (App Router) + Tailwind, statically
prerendered, deployed on Vercel.

This lives in the Pyrrhon repo on purpose: the install command, the feature
list and the FAQ all restate things that live in `README.md`, `VISION.md` and
the source. Split into its own repo and they drift apart within a month.

## Local

```bash
cd web
npm install
npm run dev        # http://localhost:3000
npm run build      # what Vercel runs — type errors fail it
npm run typecheck  # tsc --noEmit, without the bundle
```

## Deploying

Vercel, with **Root Directory** set to `web`. That is the whole configuration —
with it set, Vercel only ever sees the Node project and never tries to
interpret the Python package at the repo root. No `vercel.json` needed.

1. Import the repo on Vercel.
2. Settings → General → Root Directory → `web`.
3. Push. Framework detection, build command and output directory are automatic.

## The demo slot

`components/demo-player.tsx` is the centrepiece. Voice is the one thing a
README physically cannot show, so a recording of an actual interrupted
conversation is the highest-value asset this page can carry.

Until one exists, it renders a hand-built terminal frame showing a real
exchange. To swap in the recording:

1. Drop the file at `public/demo/pyrrhon.mp4`.
2. Set `NEXT_PUBLIC_DEMO_SRC=/demo/pyrrhon.mp4` in the Vercel project env.

The frame is hand-built rather than a screenshot so it stays sharp at any
width, follows the theme, and keeps the text selectable.

## Rules for this directory

**Nothing on this page may claim something the program does not do.** Pyrrhon's
entire pitch is that it never fabricates a citation; a landing page that
overstates it would undercut the product more than any missing feature.

Concretely, when editing copy:

- No testimonials, customer logos or usage numbers unless they are real and
  attributable. This page shipped from a template that had fabricated quotes
  attributed to named people at Sony, IBM and McDonald's. They were removed.
- No pricing section. It is free and open source.
- Feature claims trace to source. The provider list in
  `components/bento/provider-picker.tsx` mirrors `BUILTIN_PROVIDERS` in
  `pyrrhon/config/settings.py`; the supported languages in the FAQ mirror
  `LANGUAGES` in `pyrrhon/core/tools/languages.py`. If those change, this
  changes.
- No dead links. The footer only lists destinations that resolve.

## Gotchas

**The root `.gitignore` is a Python gitignore.** Its patterns are unanchored,
so a bare `lib/` or `build/` matches at every depth. `lib/`, `build/` and
`dist/` are anchored with a leading slash for exactly this reason — without
that, `web/lib/utils.ts` is silently untracked and the Vercel build fails with
`Can't resolve '@/lib/utils'` while working perfectly on your machine.

**`typescript.ignoreBuildErrors` is off and should stay off.** It was on in the
template and was hiding two real type errors (a framer-motion `onDrag` prop
conflict in `animated-section.tsx`, and a `CSSProperties` custom-property index
in `one-click-integrations-illustration.tsx`). A flag that lets a broken build
ship is not a convenience.

**CI ignores this directory.** `.github/workflows/ci.yml` has
`paths-ignore: ["web/**"]`, so copy changes don't spend a runner on the Python
suite. The tradeoff is that nothing in CI type-checks this directory — run
`npm run build` before pushing, or let the Vercel deploy be the check.
