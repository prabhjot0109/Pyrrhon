import type React from "react"

interface SpecOutputProps {
  /** Width of component – number (px) or any CSS size value */
  width?: number | string
  /** Height of component – number (px) or any CSS size value */
  height?: number | string
  /** Extra Tailwind / CSS classes for root element */
  className?: string
}

const SpecOutput: React.FC<SpecOutputProps> = ({ width = "100%", height = "100%", className = "" }) => {
  /* ------------------------------------------------------------
   * Theme-based design tokens using global CSS variables
   * ---------------------------------------------------------- */
  const themeVars = {
    "--spec-primary-color": "hsl(var(--primary))",
    "--spec-background-color": "hsl(var(--background))",
    "--spec-text-color": "hsl(var(--foreground))",
    "--spec-text-secondary": "hsl(var(--muted-foreground))",
    "--spec-border-color": "hsl(var(--border))",
  } as React.CSSProperties

  /* ------------------------------------------------------------
   * Design-mode transcript. Illustrative of the Design act: the
   * interrogation comes first, the spec files come out the far end.
   * ---------------------------------------------------------- */
  const logLines = [
    "design mode — 6 assumptions on the table",
    "",
    "?  your entities all reference each other by id.",
    "   what does Mongo buy you over Postgres here?",
    ">  flexible schema while we're still iterating",
    "?  which entity's shape do you expect to churn?",
    ">  ...only line_items, honestly",
    "!  recorded: churn is scoped to one embedded field",
    "?  so what breaks first at 50 rps — the write or the join?",
    "",
    "writing spec/",
    "  PRD.md        problem, users, scope        ✓",
    "  HLD.md        components and boundaries    ✓",
    "  LLD.md        modules, types, sequencing   ✓",
    "  api.md        endpoints and contracts      ✓",
    "  database.md   schema, indexes, migrations  ✓",
    "  risks.md      3 open · 2 accepted          ✓",
    "",
    "6 files written — every decision traced to an answer you gave.",
  ]

  return (
    <div
      className={`w-full h-full flex items-center justify-center p-4 relative ${className}`}
      style={{
        width,
        height,
        position: "relative",
        background: "transparent",
        ...themeVars,
      }}
      role="img"
      aria-label="Design-mode transcript: Pyrrhon interrogates the design, then writes the spec files"
    >
      {/* -------------------------------------------------------- */}
      {/* Console / Terminal panel                                */}
      {/* -------------------------------------------------------- */}
      <div
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: "340px",
          height: "239px",
          background: "linear-gradient(180deg, var(--spec-background-color) 0%, transparent 100%)",
          backdropFilter: "blur(7.907px)",
          borderRadius: "10px",
          overflow: "hidden",
        }}
      >
        {/* Inner translucent panel – replicates subtle overlay */}
        <div
          style={{
            position: "absolute",
            inset: "2px",
            borderRadius: "8px",
            background: "hsl(var(--foreground) / 0.08)",
          }}
        />

        {/* Log text */}
        <div
          style={{
            position: "relative",
            padding: "8px",
            height: "100%",
            overflow: "hidden",
            fontFamily: "var(--font-geist-mono), 'Geist Mono', 'SF Mono', Monaco, monospace",
            fontSize: "10px",
            lineHeight: "16px",
            color: "var(--spec-text-color)",
            whiteSpace: "pre",
          }}
        >
          {logLines.map((line, index) => (
            <p key={index} style={{ margin: 0 }}>
              {line}
            </p>
          ))}
        </div>

        {/* Inner border overlay */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            border: "0.791px solid var(--spec-border-color)",
            borderRadius: "10px",
            pointerEvents: "none",
          }}
        />
      </div>

      {/* -------------------------------------------------------- */}
      {/* Label chip                                              */}
      {/*
        The template floated a "spec/" pill here, dead centre over the file
        listing this card exists to show. Removed rather than relabelled:
        the listing already names every document it writes.
      */}
    </div>
  )
}

export default SpecOutput
