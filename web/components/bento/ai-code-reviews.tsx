import type React from "react"

// Shown in both the blurred backdrop panel and the foreground panel, so it
// lives here rather than being duplicated inline twice. Paraphrases the real
// three-way sort in pyrrhon/core/grounding/gate.py, which is what the card
// above it claims — the template's generic toast-styling snippet said nothing
// about citations.
const SNIPPET = [
  "def check(self, text, evidence):",
  "    for claim in self._claims(text):",
  "",
  "        if evidence.observed(claim.path, claim.line):",
  "            keep(claim)        # a tool actually showed this",
  "",
  "        elif claim.path.exists():",
  "            hedge(claim)       # real file, never opened",
  "",
  "        else:",
  "            strip(claim)       # unverified, say nothing",
  "",
  "    return text",
]

const AiCodeReviews: React.FC = () => {
  const themeVars = {
    "--ai-primary-color": "hsl(var(--primary))",
    "--ai-background-color": "hsl(var(--background))",
    "--ai-text-color": "hsl(var(--foreground))",
    "--ai-text-dark": "hsl(var(--primary-foreground))",
    "--ai-border-color": "hsl(var(--border))",
    "--ai-border-main": "hsl(var(--foreground) / 0.1)",
    "--ai-highlight-primary": "hsl(var(--primary) / 0.12)",
    "--ai-highlight-header": "hsl(var(--accent) / 0.2)",
  }

  return (
    <div
      style={
        {
          width: "100%",
          height: "100%",
          position: "relative",
          background: "transparent",
          ...themeVars,
        } as React.CSSProperties
      }
      role="img"
      aria-label="AI Code Reviews interface showing code suggestions with apply buttons"
    >
      {/* Background Message Box (Blurred) */}
      <div
        style={{
          position: "absolute",
          top: "30px",
          left: "50%",
          transform: "translateX(-50%) scale(0.9)",
          width: "340px",
          height: "205.949px",
          background: "linear-gradient(180deg, var(--ai-background-color) 0%, transparent 100%)",
          opacity: 0.6,
          borderRadius: "8.826px",
          border: "0.791px solid var(--ai-border-color)",
          overflow: "hidden",
          backdropFilter: "blur(16px)",
        }}
      >
        <div
          className="border rounded-lg bg-card"
          style={{
            padding: "7.355px 8.826px",
            height: "100%",
            boxSizing: "border-box",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              fontFamily: "var(--font-geist-mono), 'Geist Mono', 'SF Mono', Monaco, monospace",
              fontSize: "9.562px",
              lineHeight: "14.711px",
              letterSpacing: "-0.2942px",
              color: "hsl(var(--muted-foreground))",
              width: "100%",
              maxWidth: "320px",
              margin: 0,
            }}
          >
            {SNIPPET.map((line, i) => (
              <p key={i} style={{ margin: 0, whiteSpace: "pre-wrap", fontWeight: 400 }}>
                {line || " "}
              </p>
            ))}
          </div>
        </div>
      </div>

      {/* Foreground Message Box (Main) */}
      <div
        style={{
          position: "absolute",
          top: "51.336px",
          left: "50%",
          transform: "translateX(-50%)",
          width: "340px",
          height: "221.395px",
          background: "var(--ai-background-color)",
          backdropFilter: "blur(16px)",
          borderRadius: "9.488px",
          border: "1px solid var(--ai-border-main)",
          overflow: "hidden",
        }}
      >
        <div
          className="bg-card border border-border"
          style={{
            padding: "9.488px",
            height: "100%",
            boxSizing: "border-box",
            position: "relative",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              width: "100%",
              top: "47.67px",
              height: "33.118px",
              background: "hsl(var(--foreground) / 0.08)",
              zIndex: 1,
            }}
          />
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              width: "100%",
              top: "80.791px",
              height: "45.465px",
              background: "var(--ai-highlight-primary)",
              zIndex: 1,
            }}
          />
          <div
            style={{
              fontFamily: "var(--font-geist-mono), 'Geist Mono', 'SF Mono', Monaco, monospace",
              fontSize: "10.279px",
              lineHeight: "15.814px",
              letterSpacing: "-0.3163px",
              color: "var(--ai-text-color)",
              width: "100%",
              maxWidth: "320px",
              position: "relative",
              zIndex: 2,
              margin: 0,
            }}
          >
            {SNIPPET.map((line, i) => (
              <p key={i} style={{ margin: 0, whiteSpace: "pre-wrap", fontWeight: 400 }}>
                {line || " "}
              </p>
            ))}
          </div>
          {/*
            The template floated an "Apply changes ⌘Y" pill over this snippet.
            Removed rather than relabelled. It obscured the code it was meant to
            illustrate, and it advertised the one thing Pyrrhon deliberately
            cannot do: there is no file-editing tool on the belt.
          */}
        </div>
      </div>
    </div>
  )
}

export default AiCodeReviews
