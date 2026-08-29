import { TerminalDemo } from "@/components/terminal-demo"

/**
 * The centrepiece slot.
 *
 * Voice is the one thing a README physically cannot demonstrate, so a real
 * recording belongs here. Set NEXT_PUBLIC_DEMO_SRC to a video in public/
 * (e.g. "/demo/pyrrhon.mp4") and it renders the player.
 *
 * Until then the fallback is <TerminalDemo />, drawn from the TUI's own row
 * vocabulary, rather than the template's stock screenshot of VS Code. The
 * poster is gone with it: a still of a different editor is not a placeholder,
 * it is a wrong answer.
 */

const DEMO_SRC = process.env.NEXT_PUBLIC_DEMO_SRC

export function DemoPlayer() {
  return (
    <div className="w-[calc(100vw-40px)] max-w-[1000px]">
      <div className="rounded-2xl border border-border bg-foreground/[0.03] p-2 shadow-2xl backdrop-blur-sm">
        {DEMO_SRC ? (
          <video
            src={DEMO_SRC}
            controls
            playsInline
            preload="metadata"
            className="h-auto w-full rounded-xl shadow-lg"
          />
        ) : (
          <TerminalDemo />
        )}
      </div>
    </div>
  )
}
