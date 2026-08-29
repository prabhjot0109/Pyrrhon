import type React from "react"

interface LanguageCoverageProps {
  className?: string
}

const LanguageCoverage: React.FC<LanguageCoverageProps> = ({ className = "" }) => {
  const themeVars = {
    "--oci-primary-color": "hsl(var(--primary))",
    "--oci-background-color": "hsl(var(--background))",
    "--oci-foreground-color": "hsl(var(--foreground))",
    "--oci-muted-foreground-color": "hsl(var(--muted-foreground))",
    "--oci-border-color": "hsl(var(--border))",
    "--oci-shadow-color": "rgba(0, 0, 0, 0.12)",
    "--oci-gradient-light-gray-start": "hsl(var(--foreground) / 0.2)",
    "--oci-gradient-light-gray-end": "transparent",
    // Not cast to CSSProperties here: this doubles as a lookup table, and
    // CSSProperties has no index signature for custom properties, so reading
    // themeVars["--oci-border-color"] off it is an error. The cast happens at
    // the one place it is actually applied as a style, below.
  } satisfies Record<string, string>

  // Helper component for rendering each logo box
  const LogoBox: React.FC<{
    logoSvg?: React.ReactNode
    isGradientBg?: boolean
  }> = ({ logoSvg, isGradientBg }) => {
    const boxStyle: React.CSSProperties = {
      width: "60px",
      height: "60px",
      position: "relative",
      borderRadius: "9px",
      border: `1px ${themeVars["--oci-border-color"]} solid`,
      display: "flex",
      justifyContent: "center",
      alignItems: "center",
      overflow: "hidden",
      flexShrink: 0,
    }

    const innerContentStyle: React.CSSProperties = {
      width: "36px",
      height: "36px",
      position: "relative",
      overflow: "hidden",
      display: "flex",
      justifyContent: "center",
      alignItems: "center",
    }

    if (isGradientBg) {
      boxStyle.background = `linear-gradient(180deg, ${themeVars["--oci-gradient-light-gray-start"]} 0%, ${themeVars["--oci-gradient-light-gray-end"]} 100%)`
      boxStyle.boxShadow = `0px 1px 2px ${themeVars["--oci-shadow-color"]}`
      boxStyle.backdropFilter = "blur(18px)"
      boxStyle.padding = "6px 8px"
    }

    return <div style={boxStyle}>{logoSvg && <div style={innerContentStyle}>{logoSvg}</div>}</div>
  }

  /**
   * The template put five brand marks in this grid — Figma, Vercel, GitHub,
   * Slack, VS Code — because it illustrated a card about integrations. This
   * card is about reading a codebase, so the grid names the five languages the
   * symbol index actually covers instead. Set as type rather than as logos: it
   * is truthful without borrowing anybody's trademark, and a monospace
   * extension sits better on a monochrome page than five brand palettes would.
   *
   * The list tracks pyrrhon/core/tools/languages.py.
   */
  const Ext = ({ label }: { label: string }) => (
    <span
      style={{
        fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
        fontSize: "13px",
        fontWeight: 600,
        letterSpacing: "-0.02em",
        color: themeVars["--oci-foreground-color"],
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  )

  // Rows 0-2 only. The card clips this illustration at a fixed height, so a
  // label placed on row 3 is drawn and never seen — which is where .go was.
  const LANGUAGES = [
    { ext: ".py", row: 0, col: 3 },
    { ext: ".go", row: 0, col: 7 },
    { ext: ".ts", row: 1, col: 5 },
    { ext: ".tsx", row: 2, col: 3 },
    { ext: ".js", row: 2, col: 7 },
  ]

  // Define the grid items with their respective logos and properties
  const gridItems = Array(40)
    .fill(null)
    .map((_, i) => {
      const item: { logoSvg?: React.ReactNode; isGradientBg?: boolean } = {}
      const row = Math.floor(i / 10)
      const col = i % 10

      // Assign logos to specific positions
      const placed = LANGUAGES.find((l) => l.row === row && l.col === col)
      if (placed) {
        item.logoSvg = <Ext label={placed.ext} />
        item.isGradientBg = true
      }
      return item
    })

  return (
    <div
      className={`w-full h-full relative ${className}`}
      style={{ ...themeVars } as React.CSSProperties}
      role="img"
      aria-label="A grid of source files, with the languages the symbol index covers: Python, TypeScript, TSX, JavaScript and Go"
    >
      {/* Background radial gradient */}
      <div
        style={{
          width: "377.33px",
          height: "278.08px",
          left: "0px",
          top: "24px",
          position: "absolute",
          background: `radial-gradient(ellipse 103.87% 77.04% at 52.56% -1.80%, 
            ${themeVars["--oci-foreground-color"]}00 0%, 
            ${themeVars["--oci-foreground-color"]}F5 15%, 
            ${themeVars["--oci-foreground-color"]}66 49%, 
            ${themeVars["--oci-foreground-color"]}F5 87%, 
            ${themeVars["--oci-foreground-color"]}00 100%)`,
        }}
      />

      {/* Main content container with backdrop blur */}
      <div
        style={{
          width: "377px",
          height: "265px",
          left: "0.34px",
          top: "43.42px",
          position: "absolute",
          backdropFilter: "blur(7.91px)",
          display: "flex",
          flexDirection: "column",
          justifyContent: "flex-start",
          alignItems: "center",
          gap: "16px",
        }}
      >
        {/* Render rows of logo boxes */}
        {Array.from({ length: 4 }).map((_, rowIndex) => (
          <div
            key={rowIndex}
            style={{ display: "flex", justifyContent: "flex-start", alignItems: "center", gap: "16px" }}
          >
            {gridItems.slice(rowIndex * 10, (rowIndex + 1) * 10).map((item, colIndex) => (
              <LogoBox key={colIndex} {...item} />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

export default LanguageCoverage
