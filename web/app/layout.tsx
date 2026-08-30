import type { Metadata } from 'next'
import { Geist, Geist_Mono, Syne } from 'next/font/google'
import { Analytics } from '@vercel/analytics/next'
import { SITE } from '@/lib/site'
import './globals.css'

const geist = Geist({
  subsets: ['latin'],
  variable: '--font-geist-sans',
  display: 'swap',
})

const geistMono = Geist_Mono({
  subsets: ['latin'],
  variable: '--font-geist-mono',
  display: 'swap',
})

const syne = Syne({
  subsets: ['latin'],
  variable: '--font-syne',
  display: 'swap',
  weight: ['400', '500', '600', '700', '800'],
})

// Kept in step with the hero subhead in components/hero-section.tsx. A search
// result and the first line of the page saying two different things is the
// bounce; there are only two of them, so they are matched by hand rather than
// hoisted into lib/site with a wrapper nobody else would use.
const description =
  'A voice-first engineering agent that runs in your terminal. Ask how an unfamiliar codebase works, or think out loud through a system before you build it — it answers from what is really in the repo, and tells you when it does not know. Free and open source.'

export const metadata: Metadata = {
  title: {
    default: `${SITE.name} — talk to your codebase`,
    template: `%s · ${SITE.name}`,
  },
  description,
  applicationName: SITE.name,
  keywords: [
    'voice coding agent',
    'terminal AI agent',
    'codebase question answering',
    'grounded code citations',
    'open source AI agent',
  ],
  openGraph: {
    type: 'website',
    siteName: SITE.name,
    title: `${SITE.name} — talk to your codebase`,
    description,
  },
  twitter: {
    card: 'summary_large_image',
    title: `${SITE.name} — talk to your codebase`,
    description,
  },
  icons: {
    icon: [
      {
        url: '/icon-light-32x32.png',
        media: '(prefers-color-scheme: light)',
      },
      {
        url: '/icon-dark-32x32.png',
        media: '(prefers-color-scheme: dark)',
      },
      {
        url: '/icon.svg',
        type: 'image/svg+xml',
      },
    ],
    apple: '/apple-icon.png',
  },
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html
      lang="en"
      className={`dark ${geist.variable} ${geistMono.variable} ${syne.variable}`}
    >
      <body className="font-sans antialiased selection:bg-foreground/15 selection:text-foreground text-foreground bg-background">
        {/*
          The dithered canvas used to live here, spanning the document. It now
          belongs to the hero wrapper in app/page.tsx, which is the only place
          it should ever be visible.
        */}
        {children}
        {process.env.NODE_ENV === 'production' && <Analytics />}
      </body>
    </html>
  )
}
