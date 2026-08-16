import Link from "next/link"
import { Button } from "@/components/ui/button"
import { REPO_URL } from "@/lib/site"

/**
 * How to run it, on its own, after the cards. A real sequence, so the numbers
 * carry information rather than decoration.
 */

const steps = [
  {
    label: "Install",
    command: `git clone ${REPO_URL}\ncd Pyrrhon && uv sync`,
    note: "Python 3.12+ and uv. Add --extra voice for the audio stack.",
  },
  {
    label: "Point it at a repo",
    command: "uv run pyrrhon .          # terminal UI\nuv run pyrrhon --voice .  # and talk to it",
    note: "Set GROQ_API_KEY first, or configure another provider. Voice also uses OPENAI_API_KEY.",
  },
  {
    label: "Ask",
    command: '"Where does the retry logic live?"',
    note: "Talk over it any time — barge-in cuts the audio mid-sentence.",
  },
]

export function InstallSection() {
  return (
    <section
      id="install-section"
      className="w-full scroll-mt-20 px-5 py-24 md:py-32 flex flex-col items-center"
    >
      <div className="w-full max-w-[720px]">
        <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-muted-foreground/55">Install</p>
        <h2 className="mt-5 text-foreground text-4xl md:text-5xl font-semibold leading-[1.1] tracking-tight">
          Three commands.
        </h2>
        <p className="mt-6 max-w-[52ch] text-muted-foreground text-lg leading-relaxed text-pretty">
          It runs on your machine and calls your provider directly. There is no account and no server in between.
        </p>

        <ol className="mt-12 flex flex-col gap-4">
          {steps.map((step, i) => (
            <li key={step.label} className="rounded-xl border border-border bg-foreground/[0.03] overflow-hidden">
              <div className="flex items-baseline gap-3 px-5 pt-4">
                <span className="font-mono text-xs text-muted-foreground/55 select-none">{`0${i + 1}`}</span>
                <span className="text-foreground text-sm font-medium">{step.label}</span>
              </div>
              <pre className="px-5 py-4 font-mono text-xs md:text-[13px] leading-relaxed text-foreground/85 overflow-x-auto">
                {step.command}
              </pre>
              <p className="px-5 pb-4 text-xs text-muted-foreground/70 leading-relaxed">{step.note}</p>
            </li>
          ))}
        </ol>

        <div className="mt-10 flex flex-col sm:flex-row sm:items-center gap-4">
          <Link href={REPO_URL} target="_blank" rel="noopener noreferrer">
            <Button className="bg-secondary text-secondary-foreground hover:bg-secondary/90 px-8 py-3 rounded-full font-medium text-base shadow-lg ring-1 ring-white/10">
              View on GitHub
            </Button>
          </Link>
          <span className="text-sm text-muted-foreground/70">Free and open source.</span>
        </div>
      </div>
    </section>
  )
}
