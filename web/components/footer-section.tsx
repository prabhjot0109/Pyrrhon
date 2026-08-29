"use client"

import { SITE } from "@/lib/site"

// Inlined rather than imported: lucide-react deprecated its brand icons, so
// `Github` is on its way out of the package.
function GithubMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" className={className}>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  )
}

export function FooterSection() {
  // Only links that actually resolve. The template shipped three columns of
  // href="#" placeholders (Careers, Brand, API Reference...); a dead link is
  // worse than a missing one.
  const columns = [
    {
      heading: "Project",
      links: [
        { label: "Source", href: SITE.repo },
        { label: "README", href: SITE.links.readme },
        { label: "Vision and scope", href: SITE.links.vision },
        { label: "License", href: SITE.links.license },
      ],
    },
    {
      heading: "Install",
      links: [
        { label: `${SITE.name} on PyPI`, href: SITE.pypi },
        { label: "Issues", href: SITE.links.issues },
        { label: "Discussions", href: `${SITE.repo}/discussions` },
      ],
    },
  ]

  return (
    <footer className="section-rule mx-auto flex w-full max-w-[1320px] flex-col items-start justify-between gap-8 px-5 py-12 md:flex-row md:gap-0 md:py-[70px]">
      {/* Left Section: Logo, Description, Social Links */}
      <div className="flex flex-col justify-start items-start gap-6 p-4 md:p-8">
        <div className="flex gap-3 items-stretch justify-center">
          <div className="font-display text-wordmark text-foreground">{SITE.name}</div>
        </div>
        <p className="text-body-sm max-w-[280px] text-left text-muted-foreground">{SITE.tagline}</p>
        <div className="flex justify-start items-start gap-3">
          <a
            href={SITE.repo}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="GitHub"
            className="w-5 h-5 flex items-center justify-center"
          >
            <GithubMark className="w-full h-full text-muted-foreground hover:text-foreground transition-colors" />
          </a>
        </div>
      </div>
      {/* Right Section: link columns */}
      <div className="grid grid-cols-2 gap-8 md:gap-12 p-4 md:p-8 w-full md:w-auto">
        {columns.map((column) => (
          <div key={column.heading} className="flex flex-col justify-start items-start gap-4">
            <h3 className="font-mono text-eyebrow uppercase text-muted-foreground">{column.heading}</h3>
            <div className="flex flex-col justify-end items-start gap-2.5">
              {column.links.map((link) => (
                <a
                  key={link.label}
                  href={link.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-body-sm text-muted-foreground transition-colors hover:text-foreground"
                >
                  {link.label}
                </a>
              ))}
            </div>
          </div>
        ))}
      </div>
    </footer>
  )
}
