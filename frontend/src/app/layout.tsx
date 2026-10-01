import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  // The historical product name carries "AI"; the engine contains no AI model.
  // The public site therefore positions the product on what is true (a
  // deterministic regulatory engine) and never sells the acronym. The formal
  // rename is a business decision, recorded in audit/C11_evidence.md.
  title: "VeriClaim — Moteur réglementaire déterministe pour allégations environnementales",
  description:
    "Détection lexicale déterministe, verdicts réglementaires sourcés, rapports signés et vérifiables. Aucun LLM, aucun transfert de vos documents à un tiers.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="fr">
      <body>{children}</body>
    </html>
  );
}
