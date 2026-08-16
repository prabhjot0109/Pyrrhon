"use client"

import type React from "react"
import { useState } from "react"
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

interface FAQItemProps {
  question: string
  answer: string
  isOpen: boolean
  onToggle: () => void
}

const FAQItem = ({ question, answer, isOpen, onToggle }: FAQItemProps) => {
  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault()
    onToggle()
  }
  return (
    <div
      className={`w-full bg-[rgba(231,236,235,0.08)] shadow-[0px_2px_4px_rgba(0,0,0,0.16)] overflow-hidden rounded-[10px] outline outline-1 outline-border outline-offset-[-1px] transition-all duration-500 ease-out cursor-pointer`}
      onClick={handleClick}
    >
      <div className="w-full px-5 py-[18px] pr-4 flex justify-between items-center gap-5 text-left transition-all duration-300 ease-out">
        <div className="flex-1 text-foreground text-base font-medium leading-6 break-words">{question}</div>
        <div className="flex justify-center items-center">
          <ChevronDown
            className={`w-6 h-6 text-muted-foreground-dark transition-all duration-500 ease-out ${isOpen ? "rotate-180 scale-110" : "rotate-0 scale-100"}`}
          />
        </div>
      </div>
      <div
        className={`overflow-hidden transition-all duration-500 ease-out ${isOpen ? "max-h-[500px] opacity-100" : "max-h-0 opacity-0"}`}
        style={{
          transitionProperty: "max-height, opacity, padding",
          transitionTimingFunction: "cubic-bezier(0.4, 0, 0.2, 1)",
        }}
      >
        <div
          className={`px-5 transition-all duration-500 ease-out ${isOpen ? "pb-[18px] pt-2 translate-y-0" : "pb-0 pt-0 -translate-y-2"}`}
        >
          <div className="text-foreground/80 text-sm font-normal leading-6 break-words">{answer}</div>
        </div>
      </div>
    </div>
  )
}

export function FAQSection() {
  const [openItems, setOpenItems] = useState<Set<number>>(new Set())
  const toggleItem = (index: number) => {
    const newOpenItems = new Set(openItems)
    if (newOpenItems.has(index)) {
      newOpenItems.delete(index)
    } else {
      newOpenItems.add(index)
    }
    setOpenItems(newOpenItems)
  }
  return (
    <section
      id="faq-section"
      className="w-full pt-[66px] pb-20 md:pb-40 px-5 relative flex flex-col justify-center items-center"
    >
      <div className="w-[300px] h-[500px] absolute top-[150px] left-1/2 -translate-x-1/2 origin-top-left rotate-[-33.39deg] bg-primary/10 blur-[100px] z-0" />
      <div className="self-stretch pt-8 pb-8 md:pt-14 md:pb-14 flex flex-col justify-center items-center gap-2 relative z-10">
        <div className="flex flex-col justify-start items-center gap-4">
          <h2 className="w-full max-w-[435px] text-center text-foreground text-4xl font-semibold leading-10 break-words">
            Frequently Asked Questions
          </h2>
          <p className="self-stretch text-center text-muted-foreground text-sm font-medium leading-[18.20px] break-words">
            What Pyrrhon does, what it deliberately doesn&apos;t, and where your code goes
          </p>
        </div>
      </div>
      <div className="w-full max-w-[600px] pt-0.5 pb-10 flex flex-col justify-start items-start gap-4 relative z-10">
        {faqData.map((faq, index) => (
          <FAQItem key={index} {...faq} isOpen={openItems.has(index)} onToggle={() => toggleItem(index)} />
        ))}
      </div>
    </section>
  )
}
