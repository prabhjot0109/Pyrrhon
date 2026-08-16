/** @type {import('next').NextConfig} */
const nextConfig = {
  // The v0 template shipped with typescript.ignoreBuildErrors: true, which
  // means a type error ships instead of failing the build. Removed: a broken
  // deploy should be caught by `next build`, not by a visitor.
  images: {
    unoptimized: true,
  },
}

export default nextConfig
