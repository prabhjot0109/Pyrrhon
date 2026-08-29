"use client"

import { motion, useReducedMotion, type HTMLMotionProps } from "framer-motion"
import type { ReactNode } from "react"

// Extends framer-motion's own div props, not React's HTMLAttributes. The two
// disagree on `onDrag` (motion replaces the native drag handler with its own
// pan-info signature), so spreading React's version into motion.div is a type
// error — one the template's `ignoreBuildErrors: true` was hiding.
interface AnimatedSectionProps extends Omit<HTMLMotionProps<"div">, "children"> {
  children: ReactNode
  delay?: number
}

export function AnimatedSection({ children, className, delay = 0, ...props }: AnimatedSectionProps) {
  // The CSS in globals.css kills transitions under prefers-reduced-motion, but
  // framer-motion animates through the Web Animations API and never sees that
  // rule, so it has to be asked here as well.
  const reduced = useReducedMotion()

  return (
    <motion.div
      // No `scale` on the way in. Scaling a full-bleed section resamples every
      // glyph inside it for the length of the transition, which read as the
      // text going soft and then snapping sharp. The vertical offset alone
      // reads as the same reveal without touching the raster.
      initial={reduced ? false : { opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ duration: 0.7, ease: [0.33, 1, 0.68, 1], delay: reduced ? 0 : delay }}
      className={className}
      {...props}
    >
      {children}
    </motion.div>
  )
}
