/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  // The dev server is bound to localhost, but the app is also reached through
  // 127.0.0.1. Without this, Next.js rejects the dev websocket handshake and
  // the browser logs "WebSocket connection to ws://127.0.0.1:3000/_next/hmr
  // failed" and hot reload silently stops working.
  allowedDevOrigins: ['127.0.0.1', 'localhost'],
  experimental: {
    // The rewrite proxy defaults to a 30s timeout, which killed every API
    // call that runs longer than that (a YouTube download or a fast-whisper
    // transcription both exceed it). 10 minutes covers the slowest real job.
    proxyTimeout: 10 * 60 * 1000,
  },
  async rewrites() {
    return [
      { source: '/api/:path*', destination: 'http://127.0.0.1:8000/api/:path*' },
    ]
  },
}
module.exports = nextConfig
