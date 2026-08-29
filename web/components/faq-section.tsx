"use client"

import { useId, useState } from "react"
import { ChevronDown } from "lucide-react"

const faqData = [
  {
    question: "Does it write code for me?",
    answer:
      "No, and that's deliberate. Pyrrhon has no file-editing tool on its belt — the only things it can write are spec documents and its own memory notes. It reads, explains and interrogates. Your editor and your existing coding agent keep doing what they already do well.",
  },
  {
    question: "What actually stops it from making things up?",
    answer:
      "A grounding gate that runs on every answer. As the agent works, it records which line ranges each tool result genuinely displayed. Before an answer reaches you, each claimed citation is sorted three ways: a location it actually opened becomes a real citation, a path that exists but was never read is downgraded to a hedge, and anything unverified is stripped out entirely. A file existing is not treated as evidence that anyone looked at it.",
  },
  {
    question: "Do I have to use voice?",
    answer:
      "No. There are three channels and voice is opt-in. `pyrrhon` gives you the Textual TUI, `--text` gives you a plain REPL, and `--voice` adds the audio pipeline on top. The terminal is always there — voice drives, the screen shows the code being discussed.",
  },
  {
    question: "Does my code get uploaded anywhere?",
    answer:
      "That depends entirely on the provider you pick. By default it calls Groq, so the snippets it reads are sent to that API like any other LLM tool. Point it at Ollama or LM Studio instead and the model runs on your machine, so nothing leaves it. There is no Pyrrhon server in the middle either way — it talks to your provider directly with your key.",
  },
  {
    question: "What languages does it understand?",
    answer:
      "Symbol-level indexing works for Python, TypeScript, TSX, JavaScript and Go — that's where it can answer 'where is this defined, who calls it, what does it import' precisely. Text search, git history and file reading work on any repo regardless of language.",
  },
  {
    question: "Is it safe to point at a repo I don't trust?",
    answer:
      "Yes, because a cloned repo is treated as untrusted input rather than configuration. Anything a repo supplies that could run a program, redirect where your keys are sent, or write into the system prompt is refused until you grant it explicitly, and the grant is bound to that exact content. You get one consent prompt at startup listing what the repo asked for.",
  },
  {
    question: "What does it cost?",
    answer:
      "Pyrrhon itself is free and open source. You bring your own provider key — a Groq key covers the free tier for the text and TUI channels, and voice additionally uses OpenAI for speech. Run it against Ollama and it costs nothing at all.",
  },
]

/**
 * The answers are authored with markdown-style backticks around flags and
 * command names. They used to be dropped into a div as-is, so readers saw the
 * literal backtick characters; this renders the odd-indexed segments as code.
 */
function withCode(text: string) {
  return text.split("`").map((part, i) =>
    i % 2 === 1 ? (
      <code
        key={i}
        className="rounded border border-border bg-foreground/[0.08] px-1.5 py-0.5 font-mono text-[0.88em] text-foreground"
      >
        {part}
      </code>
    ) : (
      part
    ),
  )
}

const FAQItem = ({
  question,
  answer,
  isOpen,
  onToggle,
}: {
  question: string
  answer: string
  isOpen: boolean
  onToggle: () => void
}) => {
  const panelId = useId()
  return (
    <div className="w-full overflow-hidden rounded-xl border border-border bg-foreground/[0.03] transition-colors duration-300 hover:border-foreground/20">
      {/*
        A real <button>, not a div with onClick. The template's version was
        unreachable by keyboard and announced nothing about its state; this one
        is tab-focusable, toggles on Enter and Space for free, and tells a
        screen reader whether the answer is open.
      */}
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        aria-controls={panelId}
        className="flex w-full items-center justify-between gap-5 px-5 py-[18px] pr-4 text-left"
      >
        <span className="font-display text-display-xs flex-1 break-words text-foreground">{question}</span>
        <ChevronDown
          aria-hidden
          className={`h-5 w-5 shrink-0 text-muted-foreground transition-transform duration-300 ease-out ${
            isOpen ? "rotate-180" : "rotate-0"
          }`}
        />
      </button>
      <div
        id={panelId}
        // `inert` rather than `hidden`. `hidden` is display:none, which kills
        // the collapse animation outright; inert keeps the transition and
        // still takes the closed answer out of the accessibility tree, so a
        // screen reader does not read all seven answers straight through.
        inert={!isOpen}
        className={`grid transition-[grid-template-rows] duration-300 ease-out ${
          isOpen ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
        }`}
      >
        <div className="overflow-hidden">
          <p className="text-body-sm px-5 pb-[18px] pt-1 break-words text-pretty text-foreground/75">
            {withCode(answer)}
          </p>
        </div>
      </div>
    </div>
  )
}

export function FAQSection() {
  const [openItems, setOpenItems] = useState<Set<number>>(new Set())
  const toggleItem = (index: number) => {
    setOpenItems((prev) => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }
  return (
    <section
      id="faq-section"
      className="section-rule relative flex w-full scroll-mt-24 flex-col items-center px-5 pb-20 pt-24 md:pb-32 md:pt-32"
    >
      <div className="relative z-10 flex flex-col items-center justify-center gap-4 self-stretch pb-10">
        <p className="font-mono text-eyebrow uppercase text-muted-foreground">Questions</p>
        <h2 className="font-display text-display-lg w-full max-w-[760px] text-balance text-center text-foreground">
          The things people ask first
        </h2>
        <p className="text-body-lg w-full max-w-[560px] text-pretty text-center text-muted-foreground">
          What Pyrrhon does, what it deliberately doesn&apos;t, and where your code goes.
        </p>
      </div>
      <div className="relative z-10 flex w-full max-w-[640px] flex-col items-start justify-start gap-3">
        {faqData.map((faq, index) => (
          <FAQItem key={faq.question} {...faq} isOpen={openItems.has(index)} onToggle={() => toggleItem(index)} />
        ))}
      </div>
    </section>
  )
}
