"use client"

import { motion, type HTMLMotionProps } from "framer-motion"
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
  return (
    <motion.div
      initial={{ opacity: 0, y: 20, scale: 0.98 }}
      whileInView={{ opacity: 1, y: 0, scale: 1 }}
      viewport={{ once: true }}
      transition={{ duration: 0.8, ease: [0.33, 1, 0.68, 1], delay }}
      className={className}
      {...props}
    >
      {children}
    </motion.div>
  )
}
