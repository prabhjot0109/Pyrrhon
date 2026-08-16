import { Header } from "./header"

/**
 * Headline only.
 *
 * The template's 400-line inline SVG (dot grid, mint gradient wash, the filled
 * rectangle behind the copy) is gone: the dithered canvas in app/layout.tsx is
 * the background now, and layering a second one on top of it just muddied both.
 *
 * The install command, the repo link and the explanation moved to
 * <InstallSection />. Above the fold there is a sentence and the demo.
 */
export function HeroSection() {
  return (
    <section className="flex flex-col items-center text-center relative mx-auto w-full px-4 pt-0 pb-6 md:px-0">
      <div className="absolute top-0 left-0 right-0 z-20">
        <Header />
      </div>

      <div className="relative z-10 mt-28 md:mt-40 lg:mt-52 px-4">
        <h1 className="text-foreground text-4xl md:text-6xl lg:text-7xl font-semibold leading-tight tracking-tight">
          Talk to your codebase
        </h1>
        <p className="mx-auto mt-6 max-w-[640px] text-muted-foreground text-base md:text-lg leading-relaxed">
          A voice-first engineering agent for your terminal. Every claim it makes about your code cites a real{" "}
          <span className="font-mono text-foreground/80">file:line</span> — or it says it doesn&apos;t know.
        </p>
      </div>
    </section>
  )
}
