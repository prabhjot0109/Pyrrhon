import type { Metadata } from 'next'
import { Geist, Geist_Mono } from 'next/font/google'
import { Analytics } from '@vercel/analytics/next'
import { SITE } from '@/lib/site'
import './globals.css'

const _geist = Geist({ subsets: ["latin"] });
const _geistMono = Geist_Mono({ subsets: ["latin"] });

const description =
  'A voice-first engineering agent that runs in your terminal. Every claim it makes about your code cites a real file:line, or it says it does not know. Free and open source.'

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
    <html lang="en" className="dark">
      <body className="font-sans antialiased">
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
