"use client"

import { Check, Copy } from "lucide-react"
import { useEffect, useRef, useState } from "react"

type Size = "hero" | "step"

/**
 * A shell command you are meant to run, with the copy button people reach for
 * anyway. Used in the hero and in every step of the install section, so the
 * command a visitor copies is the same string in both places.
 *
 * `navigator.clipboard` is undefined on a plain-HTTP origin and its promise
 * rejects when the document is not focused, so the failure path is real and
 * gets a visible state rather than a silent no-op.
 */
export function CommandBlock({
  command,
  label,
  size = "step",
  className = "",
}: {
  command: string
  /** What gets copied, if it differs from what is shown. */
  label?: string
  size?: Size
  className?: string
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle")
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command)
      setState("copied")
    } catch {
      setState("failed")
    }
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setState("idle"), 2000)
  }

  const hero = size === "hero"

  return (
    <div
      className={`group flex items-center gap-3 rounded-xl border border-border bg-foreground/[0.04] ${
        hero ? "py-2.5 pl-4 pr-2.5 sm:pl-5" : "py-2 pl-4 pr-2"
      } backdrop-blur-sm transition-colors hover:border-foreground/25 ${className}`}
    >
      <span aria-hidden className="select-none font-mono text-muted-foreground">
        $
      </span>
      <code
        className={`flex-1 overflow-x-auto whitespace-nowrap font-mono ${
          hero ? "text-[13px] sm:text-[15px]" : "text-[12.5px] sm:text-[13.5px]"
        } font-medium text-foreground`}
      >
        {label ?? command}
      </code>
      <button
        type="button"
        onClick={copy}
        aria-label={state === "copied" ? "Copied" : `Copy: ${command}`}
        className="shrink-0 rounded-lg p-2 text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground"
      >
        {state === "copied" ? (
          <Check className="h-4 w-4 text-foreground" />
        ) : (
          <Copy className="h-4 w-4" />
        )}
      </button>
      {/* Announced to screen readers; the icon swap is what everyone else sees. */}
      <span role="status" aria-live="polite" className="sr-only">
        {state === "copied" ? "Copied to clipboard" : state === "failed" ? "Copy failed" : ""}
      </span>
    </div>
  )
}
