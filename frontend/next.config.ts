import type { NextConfig } from "next";

const backendUrl = (process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  allowedDevOrigins: ["*.e2b.app", "localhost", "127.0.0.1"],
  async headers() {
    // E-04 : l'interface ne portait aucun en-tête de sécurité. Ces trois
    // directives sont sûres pour Next.js (pas de script-src, donc rien qui
    // puisse casser les bundles inline de développement) et couvrent les deux
    // risques réels de l'application : l'encadrement (clickjacking sur les
    // formulaires d'import et de connexion) et le sniffing de contenu.
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'" },
        ],
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
      // Sondes d'exploitation servies par le backend : sans ces règles, elles
      // tombent sur le routeur Next.js (404 HTML) au lieu de remonter l'état
      // réel du runtime (base, stockage, antivirus).
      {
        source: "/healthz",
        destination: `${backendUrl}/healthz`,
      },
      {
        source: "/readyz",
        destination: `${backendUrl}/readyz`,
      },
    ];
  },
};

export default nextConfig;
