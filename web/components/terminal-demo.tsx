/**
 * The centrepiece, drawn rather than photographed.
 *
 * The slot used to hold `public/images/dashboard-preview.png`, which is the
 * template's stock screenshot of VS Code — a different program, wearing a
 * mint-green glow that fought every other pixel on a deliberately monochrome
 * page. A picture of somebody else's editor is worse than no picture.
 *
 * Everything below is Pyrrhon's own vocabulary, taken from the TUI rather
 * than invented: the six rail glyphs and their roles come from
 * pyrrhon/tui/messages.py, and the three rail hues are the epistemic ladder
 * out of pyrrhon/tui/theme.py — green verified, amber hedged, and the accent
 * for a turn that was yours. Note what the answer does not contain: no
 * `path:line` in the prose. A verified reference leaves the sentence and
 * arrives as its own citation row, which is the delivery contract the
 * product actually implements.
 */

// The one place on this site with hue in it, and only because these five
// values *are* the product. Sourced from pyrrhon/tui/theme.py.
const RAIL = {
  voice: "#4097de",
  evidence: "#7ec699",
  hedge: "#d9a441",
  muted: "#71717a",
}

type Line = {
  glyph: string
  rail: keyof typeof RAIL
  children: React.ReactNode
  /** Machinery: dimmer body text, for tool and status rows. */
  dim?: boolean
}

const lines: Line[] = [
  {
    glyph: "▌",
    rail: "voice",
    children: <span className="text-foreground">where does the grounding gate actually reject a citation?</span>,
  },
  { glyph: "┊", rail: "muted", dim: true, children: "symbol_context  GroundingGate  ·  4 sites" },
  { glyph: "┊", rail: "muted", dim: true, children: "read_file  core/grounding/gate.py  ·  lines 96-140" },
  { glyph: "┊", rail: "muted", dim: true, children: "read_file  core/grounding/evidence.py  ·  lines 12-58" },
  {
    glyph: "│",
    rail: "evidence",
    children: (
      <>
        In the gate&apos;s check, each claimed reference is sorted against the turn&apos;s evidence ledger. A location
        a tool actually displayed becomes a citation. A path that exists but was never opened is downgraded to the
        bare path with a hedge. Anything it cannot place at all is stripped before you hear it.
      </>
    ),
  },
  { glyph: "📍", rail: "evidence", children: "pyrrhon/core/grounding/gate.py:118" },
  { glyph: "📍", rail: "evidence", children: "pyrrhon/core/grounding/evidence.py:41" },
  {
    glyph: "⚠",
    rail: "hedge",
    children: "One claim about the strict provenance mode was hedged: that file was not opened this turn.",
  },
]

export function TerminalDemo() {
  return (
    <div className="w-full overflow-hidden rounded-2xl border border-border bg-[#0b0b0d] shadow-2xl">
      {/* Window chrome */}
      <div className="flex items-center gap-2 border-b border-border bg-foreground/[0.03] px-4 py-3">
        <span className="flex gap-1.5" aria-hidden>
          <span className="h-2.5 w-2.5 rounded-full bg-foreground/20" />
          <span className="h-2.5 w-2.5 rounded-full bg-foreground/20" />
          <span className="h-2.5 w-2.5 rounded-full bg-foreground/20" />
        </span>
        <span className="ml-2 font-mono text-[11.5px] text-muted-foreground">pyrrhon — ~/src/pyrrhon</span>
      </div>

      <div className="flex flex-col gap-2.5 px-4 py-6 font-mono text-[11.5px] leading-relaxed sm:px-6 sm:py-8 sm:text-[13px]">
        {lines.map((line, i) => (
          <div key={i} className="flex gap-3">
            <span aria-hidden className="w-4 shrink-0 select-none text-center" style={{ color: RAIL[line.rail] }}>
              {line.glyph}
            </span>
            <span className={`min-w-0 ${line.dim ? "text-muted-foreground" : "text-foreground/85"}`}>
              {line.children}
            </span>
          </div>
        ))}

        <div className="flex gap-3 pt-1">
          <span aria-hidden className="w-4 shrink-0 select-none text-center" style={{ color: RAIL.voice }}>
            ▌
          </span>
          <span className="text-muted-foreground">
            and who calls it?
            {/* The caret is a static block. An animated one is a distraction
                next to a page that is already moving on scroll. */}
            <span aria-hidden className="ml-0.5 inline-block h-[1.05em] w-[0.5em] translate-y-[0.15em] bg-foreground/70" />
          </span>
        </div>
      </div>

      {/* Status strip. Same fields the real one carries: repo, model, context
          fill, voice state. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border bg-foreground/[0.03] px-4 py-2.5 font-mono text-[11px] text-muted-foreground sm:px-6">
        <span>pyrrhon</span>
        <span aria-hidden>·</span>
        <span>groq/llama-3.3-70b</span>
        <span aria-hidden>·</span>
        <span>ctx 18%</span>
        <span aria-hidden>·</span>
        <span style={{ color: RAIL.voice }}>🎙 listening</span>
        <span className="ml-auto hidden sm:inline">^p commands</span>
      </div>
    </div>
  )
}
