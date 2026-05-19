/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // `typedRoutes` was on under `experimental` before #32a, but it
  // doesn't gracefully handle dynamic search-param hrefs like
  // `/?filter=${slug}` — every dynamic href becomes a TS error.
  // Re-enable in #32b once routes settle.
};

export default nextConfig;
