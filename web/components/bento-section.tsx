import type React from "react"

import GroundedCitations from "./bento/ai-code-reviews"
import BargeIn from "./bento/real-time-previews"
import CodebaseMap from "./bento/language-coverage"
import ProviderPicker from "./bento/provider-picker"
import DeepSubagents from "./bento/parallel-agents"
import SpecOutput from "./bento/spec-output"

interface BentoCardProps {
  title: string
  description: string
  Component: React.ComponentType
}

/**
 * The card ground used to be a literal `rgba(231, 236, 235, 0.08)` with a
 * `border-white/20` on top, which is the one thing globals.css says not to do:
 * both were brighter than every other surface on the page and neither moved
 * when the palette did. They resolve through the tokens now.
 */
const BentoCard = ({ title, description, Component }: BentoCardProps) => (
  <div className="group relative flex flex-col items-start justify-start overflow-hidden rounded-2xl border border-border bg-foreground/[0.03] transition-colors duration-300 hover:border-foreground/20">
    <div className="pointer-events-none absolute inset-0 rounded-2xl bg-gradient-to-br from-foreground/[0.04] to-transparent" />

    <div className="relative z-10 flex flex-col items-start justify-start gap-2 self-stretch p-6">
      <h3 className="font-display text-display-xs self-stretch text-balance text-foreground">{title}</h3>
      <p className="text-body-sm self-stretch text-pretty text-muted-foreground">{description}</p>
    </div>
    <div className="relative z-10 -mt-0.5 h-72 self-stretch">
      <Component />
    </div>
  </div>
)

export function BentoSection() {
  const cards: BentoCardProps[] = [
    {
      title: "Every claim cites a real file:line.",
      description:
        "The grounding gate checks each citation against what the tools actually opened. Anything it can't verify gets stripped, not softened.",
      Component: GroundedCitations,
    },
    {
      title: "Talk over it, mid-sentence.",
      description: "Barge-in cuts the audio the moment you start speaking. No waiting for it to finish a paragraph.",
      Component: BargeIn,
    },
    {
      title: "Understand a codebase you didn't write.",
      description:
        "Ask how a feature works or where to add something. The terminal shows the code while you talk about it.",
      Component: CodebaseMap,
    },
    {
      title: "It escalates instead of guessing.",
      description:
        "think_deeper spawns a bounded, read-only subagent for the hard questions, and narrates what it's doing while it works.",
      Component: DeepSubagents,
    },
    {
      title: "Design a system, then get the spec.",
      description:
        "It interrogates your choices the way a senior architect would, then writes PRD.md, HLD.md, LLD.md and the rest once the reasoning holds up.",
      Component: SpecOutput,
    },
    {
      title: "Any provider. Or none at all.",
      description:
        "Groq out of the box, plus OpenAI, Cerebras, Gemini and more. Point it at Ollama and the model never leaves your machine.",
      Component: ProviderPicker,
    },
  ]

  return (
    <section className="section-rule flex w-full flex-col items-center justify-center overflow-visible bg-transparent px-5 py-24 md:py-32">
      {/*
        No decorative glow: the dither is the hero's, and everything down here
        sits on the flat --background on purpose.

        One vertical rhythm for every section: py-24 md:py-32 on the section
        and nothing else below it. This one used to stack three paddings — the
        section, the inner wrapper and the heading block — for up to 232px of
        dead space above the eyebrow.
      */}
      <div className="relative flex w-full flex-col items-start justify-start gap-6">
        <div className="z-10 flex flex-col items-center justify-center gap-4 self-stretch pb-10 md:pb-14">
          <p className="font-mono text-eyebrow uppercase text-muted-foreground">How it works</p>
          <h2 className="font-display text-display-lg w-full max-w-[760px] text-balance text-center text-foreground">
            It can point at everything it says
          </h2>
          <p className="text-body-lg w-full max-w-[620px] text-pretty text-center text-muted-foreground">
            A confident hallucination spoken out loud is the worst thing a voice agent can do. Pyrrhon is built so that
            it can&apos;t.
          </p>
        </div>
        <div className="self-stretch grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 z-10">
          {cards.map((card) => (
            <BentoCard
              key={card.title}
              title={card.title}
              description={card.description}
              Component={card.Component}
            />
          ))}
        </div>
      </div>
    </section>
  )
}
