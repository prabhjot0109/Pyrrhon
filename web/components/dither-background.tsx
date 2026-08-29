'use client'

import dynamic from 'next/dynamic'

// three.js + postprocessing is by far the largest thing this site ships, and
// it draws a decorative background. Loading it with ssr:false keeps it out of
// the server render (a WebGL canvas has nothing to contribute there) and off
// the critical path, so the headline and the demo paint before the shader
// compiles. The wrapper is its own client component because next/dynamic with
// ssr:false is not allowed inside a Server Component.
const Dither = dynamic(() => import('./Dither'), { ssr: false })

/**
 * Fills its nearest positioned ancestor, which is the hero wrapper in
 * app/page.tsx. Scoping it that way — rather than positioning it against the
 * document and guessing a max-height — is what makes it scroll away with the
 * hero and stop at the hero's edge, whatever that edge turns out to be.
 */
export function DitherBackground() {
  return (
    <div aria-hidden="true" className="absolute inset-0 z-0 overflow-hidden">
      <Dither
        waveColor={[0.26, 0.26, 0.26]}
        disableAnimation={false}
        enableMouseInteraction
        mouseRadius={0.3}
        colorNum={4}
        pixelSize={2}
        waveAmplitude={0.3}
        waveFrequency={3}
        waveSpeed={0.05}
      />

      {/*
        Three scrims, and each one is load-bearing rather than decorative.

        The wave crest is close to white where it peaks, and it peaks wherever
        the noise happens to put it — which was directly behind the headline
        and the nav. Grey text on a moving near-white field is not a contrast
        ratio you can reason about, so the copy gets its own ground instead of
        the shader being tuned until it happens to miss.

        Centre: an ellipse over the column the copy occupies. Dark where the
        words are, gone by the edges, so the dither still reads as itself in
        the corners.
      */}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_72%_58%_at_50%_30%,hsl(var(--background)/0.94)_0%,hsl(var(--background)/0.78)_45%,transparent_100%)]" />
      {/* Top: the header is fixed and transparent over the hero, so its links
          need a ground of their own before the user has scrolled. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 h-44 bg-gradient-to-b from-background via-background/75 to-transparent" />
      {/* Bottom: fades the dither into the page instead of a hard edge. */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-56 bg-gradient-to-b from-transparent to-background" />
    </div>
  )
}
