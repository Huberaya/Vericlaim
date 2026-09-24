import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VeriClaim AI — Audit réglementaire",
  description: "Moteur déterministe de contrôle des allégations environnementales produit.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="fr">
      <body>{children}</body>
    </html>
  );
}
