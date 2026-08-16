/**
 * The centrepiece slot.
 *
 * Voice is the one thing a README physically cannot demonstrate, so this is
 * where the recording belongs. Set NEXT_PUBLIC_DEMO_SRC to a video in
 * public/ (e.g. "/demo/pyrrhon.mp4") and it renders the player; until then it
 * falls back to a static terminal frame showing a real exchange.
 *
 * The fallback is hand-built rather than a screenshot on purpose: it stays
 * sharp at any width, respects the theme, and is selectable text.
 */

const DEMO_SRC = process.env.NEXT_PUBLIC_DEMO_SRC

type Line = { kind: "you" | "agent" | "tool" | "cite" | "blank"; text?: string }

const transcript: Line[] = [
  { kind: "you", text: "how does the grounding gate decide what to strip?" },
  { kind: "blank" },
  { kind: "tool", text: "grep  \"class GroundingGate\"  ·  1 match" },
  { kind: "tool", text: "read_file  pyrrhon/core/grounding/gate.py:60-140" },
  { kind: "blank" },
  {
    kind: "agent",
    text: "It sorts every claim three ways against the evidence ledger. A location a tool",
  },
  { kind: "agent", text: "actually displayed becomes a citation. A path that exists but was never opened" },
  { kind: "agent", text: "gets downgraded to the bare path with a hedge. Anything else is removed —" },
  { kind: "agent", text: "existing on disk is not treated as evidence that we looked at it." },
  { kind: "blank" },
  { kind: "cite", text: "pyrrhon/core/grounding/gate.py:88" },
  { kind: "cite", text: "pyrrhon/core/grounding/evidence.py:41" },
  { kind: "blank" },
  { kind: "you", text: "wait — so what happens if ⏎" },
]

const styles: Record<Line["kind"], string> = {
  you: "text-foreground",
  agent: "text-foreground/75",
  tool: "text-muted-foreground/60",
  cite: "text-primary",
  blank: "",
}

export function DemoPlayer() {
  return (
    <div className="w-[calc(100vw-32px)] md:w-[1160px]">
      <div className="bg-primary-light/50 rounded-2xl p-2 shadow-2xl">
        {DEMO_SRC ? (
          <video
            src={DEMO_SRC}
            controls
            playsInline
            preload="metadata"
            className="w-full h-full object-cover rounded-xl shadow-lg"
          />
        ) : (
          <div className="w-full rounded-xl shadow-lg overflow-hidden border border-border bg-background/80 backdrop-blur">
            {/* Title bar */}
            <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
              <span className="w-3 h-3 rounded-full bg-foreground/15" />
              <span className="w-3 h-3 rounded-full bg-foreground/15" />
              <span className="w-3 h-3 rounded-full bg-foreground/15" />
              <span className="ml-3 font-mono text-xs text-muted-foreground">pyrrhon --voice .</span>
              <span className="ml-auto flex items-center gap-2 font-mono text-xs text-primary">
                <span className="w-2 h-2 rounded-full bg-primary animate-pulse" />
                listening
              </span>
            </div>

            {/* Transcript */}
            <div className="p-4 md:p-6 font-mono text-[11px] md:text-sm leading-6 overflow-x-auto">
              {transcript.map((line, i) =>
                line.kind === "blank" ? (
                  <div key={i} className="h-3" aria-hidden="true" />
                ) : (
                  <p key={i} className={`whitespace-pre ${styles[line.kind]}`}>
                    {line.kind === "you" && <span className="text-primary select-none">you  </span>}
                    {line.kind === "tool" && <span className="select-none">     </span>}
                    {line.kind === "cite" && <span className="select-none">   ↳ </span>}
                    {line.text}
                  </p>
                ),
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
