/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    // Docker 환경: BACKEND_URL=http://step5-api:8000
    // 로컬 환경: 기본값 http://localhost:8000
    const backendUrl = process.env.BACKEND_URL || "http://localhost:8000";
    return [
      {
        source: "/api/backend/:path*",
        destination: `${backendUrl}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
