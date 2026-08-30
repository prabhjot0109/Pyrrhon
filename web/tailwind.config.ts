import type { Config } from "tailwindcss"

/**
 * The type scale is a table, not a habit.
 *
 * Every heading on the site used to spell out its own size, leading and
 * tracking as four or five utilities that drifted apart between sections
 * (`text-5xl sm:text-6xl md:text-7xl lg:text-8xl font-bold leading-[1.05]
 * tracking-tight md:tracking-[-0.035em]`). Syne needs tracking that moves
 * with size — tight and negative at display sizes, open and positive at
 * label sizes — so getting it right by hand at every call site was never
 * going to hold. The `display-*` and `body-*` steps below bundle size,
 * leading, tracking and weight into one class each, and the sizes are
 * fluid `clamp()` so a heading has no breakpoint chain at all.
 */
const config = {
  darkMode: ["class"],
  content: [
    "./pages/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./app/**/*.{ts,tsx}",
    "./src/**/*.{ts,tsx}",
    "*.{js,ts,jsx,tsx,mdx}",
  ],
  prefix: "",
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: {
        "2xl": "1400px",
      },
    },
    extend: {
      fontFamily: {
        sans: ["var(--font-geist-sans)", "system-ui", "-apple-system", "BlinkMacSystemFont", "'Segoe UI'", "Roboto", "sans-serif"],
        mono: ["var(--font-geist-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "Consolas", "monospace"],
        display: ["var(--font-syne)", "var(--font-geist-sans)", "system-ui", "sans-serif"],
        syne: ["var(--font-syne)", "var(--font-geist-sans)", "system-ui", "sans-serif"],
      },
      fontSize: {
        /*
          Display steps. Syne, semibold throughout.

          These used to run extrabold (800) at the top two steps and top out
          at 6rem with -0.04em tracking, which is three intensifiers stacked
          on one word: Syne is already a wide, high-contrast face, so 800 at
          96px with the letters jammed together read as a shout rather than a
          headline. Weight is now a flat 600 across every step and hierarchy
          comes from size alone, which is the axis a reader actually ranks.

          Tracking still tightens as size grows — that part was right, large
          type genuinely needs it — but from a much shallower start, because
          -0.04em was closing the counters on Syne's rounder glyphs.
        */
        "display-xl": ["clamp(2.5rem, 1.55rem + 4.0vw, 4.25rem)", { lineHeight: "1.04", letterSpacing: "-0.022em", fontWeight: "600" }],
        "display-lg": ["clamp(1.875rem, 1.42rem + 1.95vw, 2.75rem)", { lineHeight: "1.1", letterSpacing: "-0.018em", fontWeight: "600" }],
        "display-md": ["clamp(1.5rem, 1.29rem + 0.9vw, 1.9rem)", { lineHeight: "1.2", letterSpacing: "-0.014em", fontWeight: "600" }],
        "display-sm": ["clamp(1.25rem, 1.15rem + 0.45vw, 1.5rem)", { lineHeight: "1.28", letterSpacing: "-0.01em", fontWeight: "600" }],
        "display-xs": ["1.0625rem", { lineHeight: "1.4", letterSpacing: "-0.003em", fontWeight: "600" }],
        // Syne below ~15px closes up badly, so the label step opens tracking
        // back out rather than inheriting the negative values above.
        "display-2xs": ["0.875rem", { lineHeight: "1.4", letterSpacing: "0.012em", fontWeight: "600" }],
        // The wordmark is its own step. It is the same two places every time
        // (header, footer) and it is not a heading, so it should not drift
        // when a heading step is retuned.
        wordmark: ["1.125rem", { lineHeight: "1", letterSpacing: "-0.004em", fontWeight: "600" }],

        // Body steps. Geist. Leading stays generous; these are read, not scanned.
        "body-lg": ["clamp(1.0625rem, 0.99rem + 0.35vw, 1.25rem)", { lineHeight: "1.6", letterSpacing: "-0.005em" }],
        "body": ["1rem", { lineHeight: "1.65" }],
        "body-sm": ["0.9375rem", { lineHeight: "1.6" }],
        "body-xs": ["0.8125rem", { lineHeight: "1.55" }],

        // The one label step. Mono, uppercase, wide — the deliberate counter
        // to Syne's width, which is what makes a section eyebrow read as a
        // different voice rather than a smaller heading.
        eyebrow: ["0.6875rem", { lineHeight: "1", letterSpacing: "0.22em", fontWeight: "600" }],
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
          dark: "hsl(var(--primary-dark))",
          light: "hsl(var(--primary-light))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
} satisfies Config

export default config
