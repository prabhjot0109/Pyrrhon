"use client"

import { Check, Copy, X } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"

type Size = "hero" | "step"

/**
 * Put `text` on the clipboard, and say whether it landed.
 *
 * The async Clipboard API is the right path and gets tried first, but it is
 * gated on a **secure context** — and that is not a hypothetical edge here.
 * `next dev` prints two addresses; `http://localhost:3000` is treated as
 * secure and `http://192.168.x.x:3000` is not, so on the Network URL, on any
 * plain-HTTP preview, and inside some in-app webviews, `navigator.clipboard`
 * is `undefined` and `writeText` is a `TypeError` before it is a promise.
 * It also rejects with `NotAllowedError` when the document is not focused,
 * which a click ought to have guaranteed and does not always.
 *
 * `document.execCommand("copy")` is deprecated and universally implemented,
 * and it has no secure-context requirement — which makes it the fallback
 * rather than dead legacy to delete. The textarea is `readOnly` and parked
 * off-screen so selecting it neither scrolls the page nor raises a keyboard
 * on mobile, and the caller's own selection is put back afterwards: copying
 * a command should not silently discard the text somebody had highlighted.
 */
async function writeClipboard(text: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Fall through. The reasons writeText rejects — no focus, a denied
      // permission — are all ones execCommand does not share.
    }
  }

  const area = document.createElement("textarea")
  area.value = text
  area.readOnly = true
  area.style.position = "fixed"
  area.style.top = "0"
  area.style.left = "-9999px"
  document.body.appendChild(area)

  const selection = document.getSelection()
  const previous = selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null

  try {
    area.select()
    area.setSelectionRange(0, text.length)
    return document.execCommand("copy")
  } catch {
    return false
  } finally {
    area.remove()
    if (selection && previous) {
      selection.removeAllRanges()
      selection.addRange(previous)
    }
  }
}

/**
 * The clipboard write, its two-second acknowledgement and its failure path,
 * in one place.
 *
 * The failure path is real — see `writeClipboard` — so it gets a state, and
 * that state has to *look* different from idle. It did not: `failed` and
 * `idle` both rendered the same grey copy icon, so a button that could not
 * reach the clipboard was indistinguishable from one nobody had pressed. A
 * state nobody can see is not a failure path, it is a silent no-op with
 * extra steps.
 *
 * That is also the reason this is a hook rather than an inline handler per
 * call site: there are two shapes of command on the page — the single-line
 * <CommandBlock /> and the multi-line <pre> in each install step — and only
 * one of them should own the knowledge of how copying goes wrong.
 */
function useCopy(text: string) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle")
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  const copy = useCallback(async () => {
    const ok = await writeClipboard(text)
    setState(ok ? "copied" : "failed")
    if (timer.current) clearTimeout(timer.current)
    // The failure notice sits twice as long as the tick. A tick confirms
    // something you already believe happened; a cross has to be read.
    timer.current = setTimeout(() => setState("idle"), ok ? 2000 : 4000)
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
        aria-label={
          state === "copied"
            ? "Copied"
            : state === "failed"
              ? "Copy failed. Select the command and copy it by hand."
              : label
                ? `Copy ${label}`
                : `Copy: ${text}`
        }
        // The hover ink is suppressed while a result is showing. Without that,
        // hover:text-foreground repaints the tick grey the moment the pointer
        // stops moving — and the pointer is necessarily still on the button,
        // because it just clicked it.
        title={state === "failed" ? "Could not reach the clipboard — select the command and copy it by hand." : undefined}
        className={`shrink-0 rounded-lg p-2 transition-colors ${
          state === "copied"
            ? "bg-success/10 text-success"
            : state === "failed"
              ? "bg-destructive/10 text-destructive"
              : "text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
        } ${className}`}
      >
        {state === "copied" ? (
          <Check className="h-4 w-4" />
        ) : state === "failed" ? (
          <X className="h-4 w-4" />
        ) : (
          <Copy className="h-4 w-4" />
        )}
      </button>
      {/* Announced to screen readers; the icon swap is what everyone else sees. */}
      <span role="status" aria-live="polite" className="sr-only">
        {state === "copied"
          ? "Copied to clipboard"
          : state === "failed"
            ? "Copy failed. Select the command and copy it by hand."
            : ""}
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
  /**
   * What is *shown*, if it differs from what gets copied. `command` is always
   * the string that reaches the clipboard. The comment here used to say the
   * opposite, which is a trap rather than a typo: acting on it would put the
   * display text on the clipboard and ship a command nobody can run.
   */
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
