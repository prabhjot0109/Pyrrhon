/**
 * The explanation, between the demo and the feature cards.
 *
 * Deliberately short. A reader who just watched the video needs four facts —
 * what it is, the two things it does, why it can be trusted — and the cards
 * below expand on all of them. Installation is its own section further down;
 * nobody installs anything before they know what it is.
 */

const acts = [
  {
    name: "Understand",
    lead: "A codebase you didn't write.",
    body: "Where a feature lives, who calls a function, why it changed. It reads and traces symbols across Python, TypeScript, JavaScript and Go.",
  },
  {
    name: "Design",
    lead: "A system you're about to build.",
    body: "It interrogates your decisions the way a senior architect would, then writes the spec — PRD.md, HLD.md and the rest.",
  },
]

const facts = ["Never edits your files", "Your provider, your key", "Runs fully local on Ollama"]

export function AboutSection() {
  return (
    <section id="about-section" className="section-rule flex w-full scroll-mt-24 justify-center px-5 py-24 md:py-32">
      <div className="w-full max-w-[880px]">
        <p className="font-mono text-eyebrow uppercase text-muted-foreground">What it is</p>

        <h2 className="font-display text-display-lg mt-5 max-w-[22ch] text-balance text-foreground">
          A terminal agent. A reviewer. One you talk to.
        </h2>

        <p className="text-body-lg mt-6 max-w-[58ch] text-pretty text-muted-foreground">
          Point it at any model — Groq, OpenAI, Gemini, DeepSeek, Cerebras, OpenRouter, or Ollama running on your own
          machine — and it reads the repo you give it, thinks the problem through, and answers out loud while the
          screen shows the code it&apos;s citing. It is how you get inside an open-source project nobody wrote a
          document for.
        </p>

        <div className="mt-14 grid gap-px overflow-hidden rounded-2xl border border-border bg-border sm:grid-cols-2">
          {acts.map((act) => (
            <div key={act.name} className="bg-background p-7 md:p-8">
              <p className="font-mono text-eyebrow uppercase text-foreground/70">{act.name}</p>
              <p className="font-display text-display-sm mt-4 text-balance text-foreground">{act.lead}</p>
              <p className="text-body-sm mt-3 text-pretty text-muted-foreground">{act.body}</p>
            </div>
          ))}
        </div>

        <div className="mt-10 rounded-2xl border border-border bg-foreground/[0.03] p-7 md:p-8">
          <p className="font-display text-display-md text-balance text-foreground">
            Every claim cites a real{" "}
            {/* Chip and comma share a nowrap box. As separate inline nodes the
                line broke between them and the sentence began with a comma. */}
            <span className="whitespace-nowrap">
              <span className="inline-block rounded-md border border-border bg-foreground/[0.08] px-1.5 py-0.5 align-baseline font-mono text-[0.8em] font-medium text-foreground">file:line</span>,
            </span>{" "}
            or it says it doesn&apos;t know.
          </p>
          <p className="text-body-sm mt-4 max-w-[62ch] text-pretty text-muted-foreground">
            A confident hallucination spoken aloud is the worst thing a voice agent can do, so anything it hasn&apos;t
            actually opened is stripped before you hear it.
          </p>
        </div>

        <ul className="mt-8 flex flex-wrap gap-x-8 gap-y-3">
          {facts.map((fact) => (
            <li key={fact} className="flex items-center gap-2.5 text-sm font-medium text-muted-foreground">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-foreground/40" />
              {fact}
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
