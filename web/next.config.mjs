import { fileURLToPath } from "node:url";

// Static export: NEXT_EXPORT=1 next build generates web/out for direct hosting
// under an nginx web root. Omit the variable in development to retain API proxying and hot reload.
const isExport = process.env.NEXT_EXPORT === "1";
// Vercel demo: use mocks throughout, with no backend or API proxy.
const isMock = process.env.NEXT_PUBLIC_MOCK === "1";

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Prevent parent lockfiles from affecting root inference and asset paths.
  turbopack: { root: fileURLToPath(new URL(".", import.meta.url)) },
  reactCompiler: true,
  // Allow LAN IP access to development assets/HMR; adjust as needed.
  // During development, allow any IPv4 origin for /_next/* and HMR, including changing LAN IPs.
  // Next forbids bare wildcard origins; segmented *.*.*.* matches any IPv4 address.
  allowedDevOrigins: ["*.*.*.*"],
  compiler: {
    removeConsole: process.env.NODE_ENV === "production",
  },
  ...(isExport
    ? {
        // Static export: no Node runtime or image optimization; each route yields <route>/index.html.
        output: "export",
        images: { unoptimized: true },
        trailingSlash: true,
      }
    : isMock
      ? {
          // Vercel mock demo: no backend or API proxy needed.
          images: { unoptimized: true },
        }
      : {
          // Development: proxy /api/* to the Go backend, default :8787, overridden by AUTOPENTEST_API.
          async rewrites() {
            const backend = process.env.AUTOPENTEST_API ?? "http://localhost:8787";
            return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
          },
        }),
};

export default nextConfig;
