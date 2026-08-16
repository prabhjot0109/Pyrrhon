import Image from "next/image"

/**
 * The centrepiece slot.
 *
 * Voice is the one thing a README physically cannot demonstrate, so this is
 * where the recording belongs. Set NEXT_PUBLIC_DEMO_SRC to a video in public/
 * (e.g. "/demo/pyrrhon.mp4") and it renders the player; until then it falls
 * back to the still at public/images/dashboard-preview.png — swap that file
 * for a Pyrrhon screenshot and nothing else needs to change.
 */

const DEMO_SRC = process.env.NEXT_PUBLIC_DEMO_SRC
const POSTER = "/images/dashboard-preview.png"

export function DemoPlayer() {
  return (
    <div className="w-[calc(100vw-32px)] md:w-[1160px]">
      <div className="rounded-2xl border border-border bg-foreground/[0.03] p-2 shadow-2xl backdrop-blur-sm">
        {DEMO_SRC ? (
          <video
            src={DEMO_SRC}
            poster={POSTER}
            controls
            playsInline
            preload="metadata"
            className="w-full h-auto rounded-xl shadow-lg"
          />
        ) : (
          <Image
            src={POSTER}
            alt="Pyrrhon running in a terminal alongside the code it is discussing"
            width={1493}
            height={855}
            priority
            className="w-full h-auto rounded-xl shadow-lg"
          />
        )}
      </div>
    </div>
  )
}
