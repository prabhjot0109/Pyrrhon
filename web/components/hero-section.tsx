import Link from "next/link"
import { ArrowRight } from "lucide-react"

import { Button } from "@/components/ui/button"
import { CommandBlock } from "@/components/command-block"
import { INSTALL_COMMAND, REPO_URL } from "@/lib/site"

/**
 * Headline, one sentence, and the command.
 *
 * The template's 400-line inline SVG (dot grid, mint gradient wash, the filled
 * rectangle behind the copy) is gone: the dithered canvas is the background
 * now, and layering a second one on top of it just muddied both.
 *
 * The install command is here as well as in <InstallSection />, deliberately.
 * Somebody who already knows what this is should not have to scroll past four
 * sections to find the one line they came for; somebody who does not gets the
 * full sequence further down. Both render `INSTALL_COMMAND` from lib/site, so
 * there is no second string to keep in step.
 *
 * The <Header /> used to be mounted here. It is fixed to the viewport in
 * app/page.tsx now, so it survives past the first screen.
 */
export function HeroSection() {
  return (
    <section className="relative mx-auto flex w-full flex-col items-center px-5 pb-6 pt-0 text-center">
      <div className="relative z-10 mt-32 md:mt-44 lg:mt-52">
        <p className="font-mono text-eyebrow uppercase text-muted-foreground">
          Free and open source
        </p>

        <h1 className="font-display text-display-xl mt-6 text-balance text-foreground">
          Talk to your codebase
        </h1>

        <p className="text-body-lg mx-auto mt-6 max-w-[620px] text-pretty text-muted-foreground">
          A voice-first engineering agent for your terminal. Every claim it makes about your code cites a real{" "}
          <span className="whitespace-nowrap">
            <span className="inline-block rounded-md border border-border bg-foreground/[0.08] px-1.5 py-0.5 align-baseline font-mono text-[0.85em] font-medium text-foreground">file:line</span>,
          </span>{" "}
          or it says it doesn&apos;t know.
        </p>

        <div className="mx-auto mt-10 flex w-full max-w-[440px] flex-col items-stretch gap-3">
          <CommandBlock command={INSTALL_COMMAND} size="hero" className="text-left" />
          <div className="flex flex-col gap-3 sm:flex-row sm:justify-center">
            <Link href="#install-section">
              <Button className="w-full rounded-full bg-secondary px-6 font-medium text-secondary-foreground shadow-lg ring-1 ring-foreground/10 hover:bg-secondary/90 sm:w-auto">
                Get started
                <ArrowRight className="ml-1.5 h-4 w-4" />
              </Button>
            </Link>
            <Link href={REPO_URL} target="_blank" rel="noopener noreferrer">
              <Button
                variant="ghost"
                className="w-full rounded-full border border-border px-6 font-medium text-foreground hover:bg-foreground/[0.06] sm:w-auto"
              >
                View source
              </Button>
            </Link>
          </div>
        </div>
      </div>
    </section>
  )
}
