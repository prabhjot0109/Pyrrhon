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
        waveColor={[0.31, 0.31, 0.31]}
        disableAnimation={false}
        enableMouseInteraction
        mouseRadius={0.3}
        colorNum={4}
        pixelSize={2}
        waveAmplitude={0.3}
        waveFrequency={3}
        waveSpeed={0.05}
      />
      {/* Fades the dither into the page instead of ending on a hard edge. */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-56 bg-gradient-to-b from-transparent to-background" />
    </div>
  )
}
