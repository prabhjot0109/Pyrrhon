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
        // Display steps. Syne, extrabold at the top two so the wordmark and
        // the section heads carry the page. Tracking tightens as size grows.
        "display-xl": ["clamp(2.75rem, 1.30rem + 6.2vw, 6rem)", { lineHeight: "0.96", letterSpacing: "-0.04em", fontWeight: "800" }],
        "display-lg": ["clamp(2.25rem, 1.55rem + 3.0vw, 3.75rem)", { lineHeight: "1.02", letterSpacing: "-0.035em", fontWeight: "800" }],
        "display-md": ["clamp(1.75rem, 1.35rem + 1.7vw, 2.5rem)", { lineHeight: "1.1", letterSpacing: "-0.03em", fontWeight: "700" }],
        "display-sm": ["clamp(1.375rem, 1.2rem + 0.75vw, 1.75rem)", { lineHeight: "1.18", letterSpacing: "-0.02em", fontWeight: "700" }],
        "display-xs": ["1.0625rem", { lineHeight: "1.35", letterSpacing: "-0.005em", fontWeight: "700" }],
        // Syne below ~15px closes up badly, so the label step opens tracking
        // back out rather than inheriting the negative values above.
        "display-2xs": ["0.875rem", { lineHeight: "1.4", letterSpacing: "0.01em", fontWeight: "700" }],
        // The wordmark is its own step. It is the same two places every time
        // (header, footer) and it is not a heading, so it should not drift
        // when a heading step is retuned.
        wordmark: ["1.1875rem", { lineHeight: "1", letterSpacing: "-0.02em", fontWeight: "800" }],

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
