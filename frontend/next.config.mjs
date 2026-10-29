/** @type {import('next').NextConfig} */
// Use 127.0.0.1, not "localhost": on Windows, Node resolves localhost to IPv6 (::1),
// but uvicorn listens on IPv4 only.
const backend = process.env.BACKEND_URL || "http://127.0.0.1:8000";

const nextConfig = {
  // `next build` also writes a self-contained server (.next/standalone) for the Docker image
  output: "standalone",
  // Allow slow assessments (news rate limit + AI calls) to finish before the proxy gives up.
  experimental: { proxyTimeout: 120000 },
  // Proxy API calls to FastAPI so the browser talks to one origin (no CORS setup needed).
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;