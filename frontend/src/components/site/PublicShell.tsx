import Link from "next/link";
import type { ReactNode } from "react";

/**
 * Shared chrome for the public pages (landing, pricing, demo, legal).
 *
 * The wordmark deliberately reads "VeriClaim" without the historical "AI"
 * suffix: the engine contains no AI model, and the audit flagged advertising it
 * as untenable. The formal rename (repo, domain, contracts) is a business
 * decision recorded as an open point — see audit/C11_evidence.md.
 */
export function PublicShell({
  children,
  current,
}: {
  children: ReactNode;
  current?: "home" | "pricing" | "demo" | "legal" | "identity";
}) {
  const links: Array<{ href: string; label: string; key: NonNullable<typeof current> }> = [
    { href: "/", label: "Produit", key: "home" },
    { href: "/pricing", label: "Tarifs", key: "pricing" },
    { href: "/demo", label: "Démonstration", key: "demo" },
    { href: "/legal", label: "Informations légales", key: "legal" },
    { href: "/aide", label: "Aide", key: "legal" },
  ];

  return (
    <div className="min-h-screen bg-canvas">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <Link href="/" className="flex items-center gap-3" aria-label="VeriClaim, accueil">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-forest text-base font-extrabold text-lime">
              V
            </span>
            <span className="text-base font-extrabold tracking-tight text-forest">
              VeriClaim
              <span className="block text-[9px] font-semibold uppercase tracking-[0.16em] text-slate-400">
                Regulatory intelligence
              </span>
            </span>
          </Link>
          <nav className="flex items-center gap-1 text-xs font-semibold text-slate-600">
            {links.map((link) => (
              <Link
                key={link.key}
                href={link.href}
                className={`rounded-lg px-3 py-2 transition ${
                  current === link.key
                    ? "bg-forest/5 text-forest"
                    : "hover:bg-slate-100 hover:text-forest"
                }`}
              >
                {link.label}
              </Link>
            ))}
            <Link
              href="/login"
              className="rounded-lg px-3 py-2 transition hover:bg-slate-100 hover:text-forest"
            >
              Se connecter
            </Link>
            <Link
              href="/app"
              className="ml-2 rounded-lg bg-forest px-4 py-2 text-white transition hover:bg-forest/90"
            >
              Accéder à la plateforme
            </Link>
          </nav>
        </div>
      </header>

      <main>{children}</main>

      <footer className="mt-20 border-t border-slate-200 bg-white">
        <div className="mx-auto grid max-w-6xl gap-8 px-6 py-12 text-xs text-slate-600 md:grid-cols-4">
          <div>
            <p className="font-bold text-forest">VeriClaim</p>
            <p className="mt-2 leading-relaxed">
              Moteur réglementaire déterministe pour les allégations environnementales
              produit, emballage et communication.
            </p>
          </div>
          <div>
            <p className="font-semibold text-forest">Produit</p>
            <ul className="mt-2 space-y-1">
              <li><Link className="hover:text-forest" href="/">Le moteur</Link></li>
              <li><Link className="hover:text-forest" href="/pricing">Tarifs et quotas</Link></li>
              <li><Link className="hover:text-forest" href="/demo">Démonstration</Link></li>
              <li><Link className="hover:text-forest" href="/signup">Créer un compte</Link></li>
              <li><Link className="hover:text-forest" href="/login">Connexion</Link></li>
              <li><Link className="hover:text-forest" href="/app">Plateforme</Link></li>
            </ul>
          </div>
          <div>
            <p className="font-semibold text-forest">Informations légales</p>
            <ul className="mt-2 space-y-1">
              <li><Link className="hover:text-forest" href="/legal">Sommaire légal</Link></li>
              <li><Link className="hover:text-forest" href="/legal/mentions-legales">Mentions légales</Link></li>
              <li><Link className="hover:text-forest" href="/legal/confidentialite">Confidentialité</Link></li>
              <li><Link className="hover:text-forest" href="/legal/dpa">Accord de traitement (DPA)</Link></li>
            </ul>
          </div>
          <div>
            <p className="font-semibold text-forest">Limites assumées</p>
            <p className="mt-2 leading-relaxed">
              Aucune IA générative, aucun LLM. Les verdicts sont des signaux de risque
              déterministes, ni un avis juridique ni une certification. La couverture
              du lexique est publiée dans chaque analyse.
            </p>
          </div>
        </div>
        <div className="border-t border-slate-100 px-6 py-5 text-center text-[11px] text-slate-400">
          Les informations légales de ce site sont des modèles en attente de validation
          par un conseil spécialisé (voir /legal).
        </div>
      </footer>
    </div>
  );
}
