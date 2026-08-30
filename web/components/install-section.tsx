import Link from "next/link"

import { Button } from "@/components/ui/button"
import { CommandBlock, CopyButton } from "@/components/command-block"
import { INSTALL_ALTERNATIVES, INSTALL_COMMAND, PACKAGE, REPO_URL, SITE } from "@/lib/site"

/**
 * How to get it and how to run it, as a real sequence, so the numbers carry
 * information rather than decoration.
 *
 * The headline command installs the published package. The clone-and-sync
 * route it replaced is still here at the bottom, because it is the right one
 * for contributors and the wrong one for everybody else — asking a visitor to
 * clone a repo to try a CLI is a barrier, not a feature.
 */

/*
 * `copy` is what reaches the clipboard when it differs from what is shown.
 * Step 03 shows two invocations side by side with aligned trailing comments,
 * which is the right thing to *read* and the wrong thing to paste into a
 * shell \u2014 so the button hands over the one line somebody actually wants.
 */
const steps: { label: string; command: string; copy?: string; note: string }[] = [
  {
    label: "Install it",
    command: INSTALL_COMMAND,
    note: "Python 3.12 or newer. The audio stack and every speech provider come with it, so there is no second command and no optional extra to remember.",
  },
  {
    label: "Give it a key",
    command: "export GROQ_API_KEY=...",
    note: "Or run `pyrrhon` with no config and the setup wizard walks you through picking a provider. Point it at Ollama and nothing leaves your machine.",
  },
  {
    label: "Point it at a repo",
    command: `${PACKAGE} .          # terminal UI\n${PACKAGE} --voice .  # and talk to it`,
    copy: `${PACKAGE} .`,
    note: "Then ask it something. \u201cWhere does the retry logic live?\u201d Talk over it any time; barge-in cuts the audio mid-sentence.",
  },
]

export function InstallSection() {
  return (
    <section
      id="install-section"
      className="section-rule flex w-full scroll-mt-24 flex-col items-center px-5 py-24 md:py-32"
    >
      <div className="w-full max-w-[760px]">
        <p className="font-mono text-eyebrow uppercase text-muted-foreground">Install</p>
        <h2 className="font-display text-display-lg mt-5 text-foreground">One command.</h2>
        <p className="text-body-lg mt-6 max-w-[54ch] text-pretty text-muted-foreground">
          It runs on your machine and calls your provider directly. There is no account, no signup and no server in
          between.
        </p>

        <div className="mt-9 max-w-[440px]">
          <CommandBlock command={INSTALL_COMMAND} size="hero" />
        </div>

        <ol className="mt-12 flex flex-col gap-3">
          {steps.map((step, i) => (
            <li key={step.label} className="overflow-hidden rounded-2xl border border-border bg-foreground/[0.03]">
              {/* The copy button lives on the step's title row rather than
                  floating over the <pre>. Absolutely positioning it there
                  would sit it on top of a block that scrolls horizontally on
                  narrow screens, i.e. over the end of the command it copies. */}
              <div className="flex items-center gap-3 py-2.5 pl-5 pr-2.5">
                <span className="select-none font-mono text-xs font-semibold text-muted-foreground">{`0${i + 1}`}</span>
                <span className="font-display text-display-2xs flex-1 text-foreground">{step.label}</span>
                <CopyButton text={step.copy ?? step.command} label={`step ${i + 1}, ${step.label}`} />
              </div>
              <pre className="overflow-x-auto px-5 pb-4 font-mono text-xs font-medium leading-relaxed text-foreground/90 sm:text-[13.5px]">
                {step.command}
              </pre>
              <p className="text-body-xs px-5 pb-4 text-muted-foreground">{step.note}</p>
            </li>
          ))}
        </ol>

        <div className="mt-12 rounded-2xl border border-border bg-foreground/[0.03] p-6 md:p-7">
          <p className="font-display text-display-xs text-foreground">Prefer something else?</p>
          <p className="text-body-sm mt-2 max-w-[58ch] text-pretty text-muted-foreground">
            <code className="font-mono text-[0.92em] text-foreground/90">uv tool install</code> is the recommendation
            because Pyrrhon is an application rather than a library: it wants its own environment with the{" "}
            <code className="font-mono text-[0.92em] text-foreground/90">{PACKAGE}</code> binary on your PATH. Any of
            these work.
          </p>
          <dl className="mt-6 flex flex-col gap-4">
            {INSTALL_ALTERNATIVES.map((alt) => (
              <div key={alt.tool} className="flex flex-col gap-2">
                <CommandBlock command={alt.command} />
                <dd className="text-body-xs pl-1 text-muted-foreground">{alt.note}</dd>
              </div>
            ))}
          </dl>
        </div>

        <div className="mt-6 rounded-2xl border border-border bg-foreground/[0.03] p-6 md:p-7">
          <p className="font-display text-display-xs text-foreground">Working on Pyrrhon itself?</p>
          <p className="text-body-sm mt-2 max-w-[58ch] text-pretty text-muted-foreground">
            Clone it and let uv build the environment from the lockfile. That is the only route that gets you the test
            suite and the evals.
          </p>
          {/* Full width, unlike the others. This command is long enough that a
              440px box cut it off mid-URL, which reads as a broken link even
              though the box scrolls. */}
          <div className="mt-4">
            <CommandBlock command={`git clone ${REPO_URL} && cd Pyrrhon && uv sync`} />
          </div>
        </div>

        <div className="mt-10 flex flex-col gap-4 sm:flex-row sm:items-center">
          <Link href={REPO_URL} target="_blank" rel="noopener noreferrer">
            <Button className="rounded-full bg-secondary px-8 py-3 text-base font-medium text-secondary-foreground shadow-lg ring-1 ring-foreground/10 hover:bg-secondary/90">
              View on GitHub
            </Button>
          </Link>
          <Link
            href={SITE.pypi}
            target="_blank"
            rel="noopener noreferrer"
            className="text-body-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            {PACKAGE} on PyPI &rarr;
          </Link>
        </div>
      </div>
    </section>
  )
}
