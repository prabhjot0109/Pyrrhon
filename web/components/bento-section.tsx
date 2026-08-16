import type React from "react"

import GroundedCitations from "./bento/ai-code-reviews"
import BargeIn from "./bento/real-time-previews"
import CodebaseMap from "./bento/one-click-integrations-illustration"
import ProviderPicker from "./bento/provider-picker"
import DeepSubagents from "./bento/parallel-agents"
import SpecOutput from "./bento/spec-output"

interface BentoCardProps {
  title: string
  description: string
  Component: React.ComponentType
}

const BentoCard = ({ title, description, Component }: BentoCardProps) => (
  <div className="overflow-hidden rounded-2xl border border-white/20 flex flex-col justify-start items-start relative">
    {/* Background with blur effect */}
    <div
      className="absolute inset-0 rounded-2xl"
      style={{
        background: "rgba(231, 236, 235, 0.08)",
        backdropFilter: "blur(4px)",
        WebkitBackdropFilter: "blur(4px)",
      }}
    />
    {/* Additional subtle gradient overlay */}
    <div className="absolute inset-0 bg-gradient-to-br from-white/5 to-transparent rounded-2xl" />

    <div className="self-stretch p-6 flex flex-col justify-start items-start gap-2 relative z-10">
      <div className="self-stretch flex flex-col justify-start items-start gap-1.5">
        <p className="self-stretch text-foreground text-lg font-normal leading-7">
          {title} <br />
          <span className="text-muted-foreground">{description}</span>
        </p>
      </div>
    </div>
    <div className="self-stretch h-72 relative -mt-0.5 z-10">
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
    <section className="w-full px-5 flex flex-col justify-center items-center overflow-visible bg-transparent">
      <div className="w-full py-8 md:py-16 relative flex flex-col justify-start items-start gap-6">
        <div className="w-[547px] h-[938px] absolute top-[614px] left-[80px] origin-top-left rotate-[-33.39deg] bg-primary/10 blur-[130px] z-0" />
        <div className="self-stretch py-8 md:py-14 flex flex-col justify-center items-center gap-2 z-10">
          <div className="flex flex-col justify-start items-center gap-4">
            <h2 className="w-full max-w-[655px] text-center text-foreground text-4xl md:text-6xl font-semibold leading-tight md:leading-[66px]">
              It can point at everything it says
            </h2>
            <p className="w-full max-w-[600px] text-center text-muted-foreground text-lg md:text-xl font-medium leading-relaxed">
              A confident hallucination spoken out loud is the worst thing a voice agent can do. Pyrrhon is built so
              that it can&apos;t.
            </p>
          </div>
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
