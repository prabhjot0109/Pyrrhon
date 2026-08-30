"use client"

import { Check, Copy } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"

type Size = "hero" | "step"

/**
 * The clipboard write, its two-second acknowledgement and its failure path,
 * in one place.
 *
 * `navigator.clipboard` is undefined on a plain-HTTP origin and its promise
 * rejects when the document is not focused, so the failure path is real and
 * gets a visible state rather than a silent no-op. That is the reason this is
 * a hook rather than an inline handler duplicated per call site: there are two
 * shapes of command on the page — the single-line <CommandBlock /> and the
 * multi-line <pre> in each install step — and only one of them should own the
 * knowledge of how copying goes wrong.
 */
function useCopy(text: string) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle")
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setState("copied")
    } catch {
      setState("failed")
    }
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setState("idle"), 2000)
  }, [text])

  return { state, copy }
}

/**
 * The button on its own, for command surfaces that are not a <CommandBlock />.
 *
 * `label` describes what is being copied and reaches the accessible name, so
 * a screen reader hears "Copy install command" rather than four identical
 * "Copy" buttons down the install steps.
 */
export function CopyButton({
  text,
  label,
  className = "",
}: {
  text: string
  label?: string
  className?: string
}) {
  const { state, copy } = useCopy(text)

  return (
    <>
      <button
        type="button"
        onClick={copy}
        aria-label={state === "copied" ? "Copied" : label ? `Copy ${label}` : `Copy: ${text}`}
        className={`shrink-0 rounded-lg p-2 text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground ${className}`}
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
    </>
  )
}

/**
 * A shell command you are meant to run, with the copy button people reach for
 * anyway. Used in the hero and in every step of the install section, so the
 * command a visitor copies is the same string in both places.
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
      <CopyButton text={command} />
    </div>
  )
}
