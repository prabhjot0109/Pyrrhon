"use client"

import type React from "react"

// This card is about barge-in, so the panel shows the interrupt path rather
// than the template's generic toast-styling snippet (which also carried the
// only literal greens left in the project).
const SNIPPET = [
  "async def on_speech_started(self):",
  "",
  "    # The user started talking. Stop, now —",
  "    # not at the end of the current sentence.",
  "",
  "    await self.tts.cancel()",
  "    self.audio_out.flush()",
  "",
  "    self.turn.interrupt()",
  "    self.history.mark_truncated(self.spoken_so_far)",
]

const RealtimeCodingPreviews: React.FC = () => {
  const themeVars = {
    "--realtime-primary-color": "hsl(var(--primary))",
    "--realtime-background-editor": "hsl(var(--background) / 0.8)", // Tinted gray from background
    "--realtime-background-preview": "hsl(var(--background) / 0.8)", // Tinted gray from background
    "--realtime-text-color": "hsl(var(--foreground))",
    "--realtime-text-editor": "hsl(var(--foreground))",
    "--realtime-text-preview": "hsl(var(--primary-foreground))", // For button text
    "--realtime-border-color": "hsl(var(--border))",
    "--realtime-border-main": "hsl(var(--border))",
    "--realtime-connection-color": "hsl(var(--muted-foreground))",
  }

  return (
    <div
      className="" // Remove className prop if not used
      style={
        {
          width: "100%", // Use 100% for responsiveness within parent
          height: "100%", // Use 100% for responsiveness within parent
          position: "relative",
          background: "transparent",
          ...themeVars,
        } as React.CSSProperties
      }
      role="img"
      aria-label="Realtime Coding Previews interface showing split-screen code editor and live preview"
    >
      {/* Left Panel - Code Editor */}
      <div
        style={{
          position: "absolute",
          top: "46px",
          left: "50%",
          transform: "translateX(-50%)",
          width: "350px",
          height: "221px",
          background: "linear-gradient(180deg, var(--realtime-background-editor) 0%, transparent 100%)",
          backdropFilter: "blur(7.907px)",
          borderRadius: "9.488px",
          border: "1px solid var(--realtime-border-main)",
          overflow: "hidden",
          boxSizing: "border-box",
        }}
        data-name="code-editor"
      >
        <div
          style={{
            padding: "9.488px 9.492px",
            height: "100%",
            boxSizing: "border-box",
            position: "relative",
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            justifyContent: "flex-start",
          }}
        >
          <div
            style={{
              fontFamily: "var(--font-geist-mono), 'Geist Mono', 'SF Mono', Monaco, monospace",
              fontSize: "10.279px",
              lineHeight: "15.814px",
              letterSpacing: "-0.3163px",
              color: "var(--realtime-text-editor)",
              width: "545.453px",
              maxWidth: "100%",
              position: "relative",
              margin: 0,
              flexGrow: 1,
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
            }}
          >
            {SNIPPET.map((line, i) => (
              <p key={i} style={{ margin: 0, whiteSpace: "pre-wrap", fontWeight: 400, display: "block" }}>
                {line || " "}
              </p>
            ))}
          </div>
        </div>
      </div>

      {/* Right Panel - Live Preview */}
      <div
        style={{
          position: "absolute",
          top: "46px",
          left: "calc(50% + 87.499px)",
          transform: "translateX(-50%)",
          width: "175px",
          height: "221px",
          background: "linear-gradient(180deg, var(--realtime-background-preview) 0%, transparent 100%)",
          backdropFilter: "blur(7.907px)",
          borderRadius: "9.488px",
          borderTopRightRadius: "9.488px",
          // Removed the border property from here
          overflow: "hidden",
          boxSizing: "border-box",
        }}
        data-name="preview-panel"
      >
        <div
          style={{
            padding: "9.488px 9.492px",
            height: "100%",
            boxSizing: "border-box",
            position: "relative",
            overflow: "hidden",
            display: "flex",
            flexDirection: "row",
            alignItems: "flex-start",
            justifyContent: "center",
            background: "var(--realtime-background-preview)", // Applied solid background here
          }}
        >
          {/*
            The template floated a "Download for macOS" pill in the middle of this
            panel. Removed rather than relabelled. This card is about barge-in, and
            the pill sat on top of the code showing it.
          */}
        </div>
      </div>

      {/* Connection Line - Exact positioning from Figma */}
      <div
        style={{
          position: "absolute",
          left: "50%",
          top: "50%",
          transform: "translate(-50%, -50%)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {/* This div now directly contains the SVG for the vertical line */}
        <div
          style={{
            position: "relative",
            width: "2px", // Width of the line (stroke width)
            height: "285.088px", // Length of the line
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <svg
            width="2"
            height="285.088"
            viewBox="0 0 2 285.088"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            style={{
              position: "absolute",
              inset: 0,
              display: "block",
              maxWidth: "none",
              width: "100%",
              height: "100%",
            }}
          >
            <defs>
              <linearGradient id="connectionGradient" x1="1" y1="0" x2="1" y2="285.088" gradientUnits="userSpaceOnUse">
                <stop stopColor="var(--realtime-primary-color)" stopOpacity="0" />
                <stop offset="0.5" stopColor="var(--realtime-primary-color)" />
                <stop offset="1" stopColor="var(--realtime-primary-color)" stopOpacity="0" />
              </linearGradient>
            </defs>
            <path d="M1 0V285.088" stroke="url(#connectionGradient)" strokeWidth="2" />
          </svg>
        </div>
      </div>

      {/* Live Recording Indicator */}

      {/* Sync Indicator at connection point */}

      <style jsx>{`
        @keyframes pulse {
          0%, 100% { opacity: 0.4; }
          50% { opacity: 1; }
        }
      `}</style>
    </div>
  )
}

export default RealtimeCodingPreviews
