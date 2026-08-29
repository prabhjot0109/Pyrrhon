import { Header } from "@/components/header"
import { HeroSection } from "@/components/hero-section"
import { DemoPlayer } from "@/components/demo-player"
import { AboutSection } from "@/components/about-section"
import { BentoSection } from "@/components/bento-section"
import { InstallSection } from "@/components/install-section"
import { FAQSection } from "@/components/faq-section"
import { FooterSection } from "@/components/footer-section"
import { AnimatedSection } from "@/components/animated-section"
import { DitherBackground } from "@/components/dither-background"

export default function LandingPage() {
  return (
    // No overflow-x-hidden here. Setting it on an in-page element forces
    // overflow-y to compute to `auto`, which turns this div into a second
    // scroll container. The clipping is on <body> in globals.css instead.
    <div id="top" className="min-h-screen">
      {/*
        Outside the hero wrapper, because it is fixed to the viewport now.
        Mounted inside, it scrolled away with the dither and never returned.
      */}
      <Header />

      {/*
        The hero zone, and the only place the dithered canvas exists. It is
        `relative`, so the canvas fills exactly this box, scrolls away with it,
        and stops at its edge — everything below is flat --background.
      */}
      <div className="relative isolate pb-16 md:pb-24">
        <DitherBackground />
        <main className="relative z-10 mx-auto flex max-w-[1320px] flex-col items-center">
          <HeroSection />
          <AnimatedSection className="mt-12 flex w-full justify-center md:mt-16">
            <DemoPlayer />
          </AnimatedSection>
        </main>
      </div>

      <AnimatedSection className="mx-auto max-w-[1320px]" delay={0.1}>
        <AboutSection />
      </AnimatedSection>

      <AnimatedSection id="features-section" className="mx-auto max-w-[1320px] scroll-mt-24" delay={0.1}>
        <BentoSection />
      </AnimatedSection>

      <AnimatedSection className="mx-auto max-w-[1320px]" delay={0.1}>
        <InstallSection />
      </AnimatedSection>

      <AnimatedSection className="mx-auto max-w-[1320px]" delay={0.1}>
        <FAQSection />
      </AnimatedSection>

      <AnimatedSection className="mx-auto max-w-[1320px]" delay={0.1}>
        <FooterSection />
      </AnimatedSection>
    </div>
  )
}
