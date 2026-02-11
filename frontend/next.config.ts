import type { NextConfig } from "next";

const DEPLOY_ENV = process.env.TRUSTBOOK_ENV || process.env.MINIBOOK_ENV || "local";

const BACKEND_URL_BY_ENV: Record<string, string> = {
  local: "http://localhost:3456",
  // Add your deployment backend URLs here, e.g.:
  // test: "http://minibook-backend.example.com",
  // production: "https://trustbook-backend.example.com",
};

const FRONTEND_URL_BY_ENV: Record<string, string> = {
  local: "http://localhost:3457",
  // Add your deployment frontend URLs here, e.g.:
  // test: "http://minibook-front.example.com",
  // production: "https://trustbook-front.example.com",
};

const BACKEND_URL = BACKEND_URL_BY_ENV[DEPLOY_ENV] || BACKEND_URL_BY_ENV.local;

const NEXT_PUBLIC_BASE_URL =
  process.env.NEXT_PUBLIC_BASE_URL ||
  FRONTEND_URL_BY_ENV[DEPLOY_ENV] ||
  FRONTEND_URL_BY_ENV.local;

const NEXT_PUBLIC_API_BASE_URL = BACKEND_URL;

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_BASE_URL,
    NEXT_PUBLIC_API_BASE_URL,
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${BACKEND_URL}/api/:path*`,
      },
      {
        source: '/skill/:path*',
        destination: `${BACKEND_URL}/skill/:path*`,
      },
      {
        source: '/docs',
        destination: `${BACKEND_URL}/docs`,
      },
    ];
  },
};

export default nextConfig;
