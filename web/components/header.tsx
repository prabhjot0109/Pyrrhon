"use client"

import type React from "react"
import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import { Sheet, SheetClose, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Menu } from "lucide-react"
import Link from "next/link"
import { REPO_URL, SITE } from "@/lib/site"

const navItems = [
  { name: "About", href: "#about-section" },
  { name: "How it works", href: "#features-section" },
  { name: "Install", href: "#install-section" },
  { name: "FAQ", href: "#faq-section" },
]

/**
 * Fixed, not absolute.
 *
 * It used to be positioned inside the hero, which meant it scrolled away with
 * the dither and never came back — on a page this long there was no route to
 * the nav or to GitHub after the first screen without scrolling all the way
 * up. It is transparent over the hero and picks up a blurred ground and a
 * hairline once you leave it, so the dither is still the first thing you see.
 */
export function Header() {
  const [scrolled, setScrolled] = useState(false)
  const [active, setActive] = useState<string | null>(null)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24)
    onScroll()
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => window.removeEventListener("scroll", onScroll)
  }, [])

  useEffect(() => {
    // rootMargin pulls the detection band up to the top third of the viewport,
    // so a section counts as "current" when it is being read rather than when
    // its last pixel finally clears the bottom of the screen.
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting)
        if (visible.length > 0) setActive(`#${visible[0].target.id}`)
      },
      { rootMargin: "-20% 0px -70% 0px" },
    )
    for (const item of navItems) {
      const el = document.getElementById(item.href.slice(1))
      if (el) observer.observe(el)
    }
    return () => observer.disconnect()
  }, [])

  const handleScroll = (e: React.MouseEvent<HTMLAnchorElement>, href: string) => {
    e.preventDefault()
    document.getElementById(href.slice(1))?.scrollIntoView({ behavior: "smooth" })
  }

  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-colors duration-300 ${
        scrolled
          ? "border-b border-border bg-background/70 backdrop-blur-xl backdrop-saturate-150"
          : "border-b border-transparent bg-transparent"
      }`}
    >
      <div className="mx-auto flex h-16 max-w-[1320px] items-center justify-between px-5 md:px-8">
        <div className="flex items-center gap-8">
          <Link
            href="#top"
            onClick={(e) => {
              e.preventDefault()
              window.scrollTo({ top: 0, behavior: "smooth" })
            }}
            className="font-display text-wordmark text-foreground"
          >
            {SITE.name}
          </Link>
          <nav className="hidden items-center gap-1 md:flex">
            {navItems.map((item) => (
              <Link
                key={item.name}
                href={item.href}
                onClick={(e) => handleScroll(e, item.href)}
                aria-current={active === item.href ? "true" : undefined}
                className={`rounded-full px-3.5 py-1.5 text-[13.5px] font-medium transition-colors ${
                  active === item.href
                    ? "bg-foreground/10 text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {item.name}
              </Link>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href={SITE.pypi}
            target="_blank"
            rel="noopener noreferrer"
            className="hidden rounded-full px-3.5 py-1.5 font-mono text-[12.5px] font-medium text-muted-foreground transition-colors hover:text-foreground sm:block"
          >
            PyPI
          </Link>
          <Link href={REPO_URL} target="_blank" rel="noopener noreferrer" className="hidden md:block">
            <Button className="rounded-full bg-secondary px-5 text-[13.5px] font-medium text-secondary-foreground shadow-sm hover:bg-secondary/90">
              GitHub
            </Button>
          </Link>
          <Sheet>
            <SheetTrigger asChild className="md:hidden">
              <Button variant="ghost" size="icon" className="text-foreground">
                <Menu className="h-6 w-6" />
                <span className="sr-only">Toggle navigation menu</span>
              </Button>
            </SheetTrigger>
            <SheetContent side="bottom" className="border-t border-border bg-background text-foreground">
              <SheetHeader>
                <SheetTitle className="font-display text-display-sm text-left text-foreground">Navigation</SheetTitle>
              </SheetHeader>
              <nav className="mt-6 flex flex-col gap-1">
                {navItems.map((item) => (
                  // SheetClose closes the drawer on tap. Without it the panel
                  // stayed open over the section it had just scrolled to.
                  <SheetClose asChild key={item.name}>
                    <Link
                      href={item.href}
                      onClick={(e) => handleScroll(e, item.href)}
                      className="rounded-lg py-2.5 text-base font-medium text-muted-foreground transition-colors hover:text-foreground"
                    >
                      {item.name}
                    </Link>
                  </SheetClose>
                ))}
                <Link href={REPO_URL} target="_blank" rel="noopener noreferrer" className="mt-4 w-full">
                  <Button className="w-full rounded-full bg-secondary px-6 py-2 font-medium text-secondary-foreground shadow-sm hover:bg-secondary/90">
                    GitHub
                  </Button>
                </Link>
              </nav>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </header>
  )
}
