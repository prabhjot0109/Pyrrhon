import type { Metadata } from "next";
import { Geist, Geist_Mono, Syne } from "next/font/google";
import { Analytics } from "@vercel/analytics/next";
import { SITE } from "@/lib/site";
import "./globals.css";
import Dither from "@/components/Dither";

const geist = Geist({
  subsets: ["latin"],
  variable: "--font-geist-sans",
  display: "swap",
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
  display: "swap",
});

const syne = Syne({
  subsets: ["latin"],
  variable: "--font-syne",
  display: "swap",
  weight: ["400", "500", "600", "700", "800"],
});

const description =
  "A voice-first engineering agent that runs in your terminal. Ask how an unfamiliar codebase works, or think out loud through a system before you build it — it answers from what is really in the repo, and tells you when it does not know. Free and open source.";

export const metadata: Metadata = {
  title: {
    default: `${SITE.name} — talk to your codebase`,
    template: `%s · ${SITE.name}`,
  },
  description,
  applicationName: SITE.name,
  keywords: [
    "voice coding agent",
    "terminal AI agent",
    "codebase question answering",
    "grounded code citations",
    "open source AI agent",
  ],
  openGraph: {
    type: "website",
    siteName: SITE.name,
    title: `${SITE.name} — talk to your codebase`,
    description,
  },
  twitter: {
    card: "summary_large_image",
    title: `${SITE.name} — talk to your codebase`,
    description,
  },
  icons: {
    icon: [
      {
        url: "/icon-light-32x32.png",
        media: "(prefers-color-scheme: light)",
      },
      {
        url: "/icon-dark-32x32.png",
        media: "(prefers-color-scheme: dark)",
      },
      {
        url: "/icon.svg",
        type: "image/svg+xml",
      },
    ],
    apple: "/apple-icon.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`dark ${geist.variable} ${geistMono.variable} ${syne.variable}`}
    >
      <body className="font-sans antialiased selection:bg-foreground/15 selection:text-foreground text-foreground bg-background">
        <div
          id="dither-hero-wrapper"
          className="absolute top-0 left-0 right-0 w-full overflow-hidden pointer-events-none z-0 min-h-0 h-[1000px]"
        >
          <Dither
            waveColor={[
              0.30980392156862746, 0.30980392156862746, 0.30980392156862746,
            ]}
            disableAnimation={false}
            enableMouseInteraction
            mouseRadius={0.3}
            colorNum={4}
            pixelSize={2}
            waveAmplitude={0.3}
            waveFrequency={3}
            waveSpeed={0.05}
          />
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-b from-transparent to-background" />
        </div>
        {children}
        {process.env.NODE_ENV === 'production' && <Analytics />}
      </body>
    </html>
  );
}
