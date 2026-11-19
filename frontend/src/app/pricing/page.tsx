import Link from "next/link";
import { PublicShell } from "@/components/site/PublicShell";
import { requestUrl } from "@/lib/api";

/**
 * Page publique des tarifs (C13) — le lien « Tarifs » de l'en-tête public.
 *
 * Deux règles, héritées du reste du produit :
 *
 * 1. **La page lit le produit, elle ne le recopie pas.** Les offres viennent de
 *    `GET /api/v1/billing/plans`, la route qui publie le catalogue **réellement
 *    appliqué** par le code (`app/billing/plans.py`). Une grille tarifaire
 *    recopiée dans une page statique diverge : c'est exactement ce qu'un client
 *    constate en recevant une erreur de quota qui ne correspond pas à la page.
 *
 * 2. **Aucun prix n'est présenté comme validé s'il ne l'est pas.** Le catalogue
 *    porte `pricing_confirmed` / `pricing_status` : tant qu'un humain n'a pas
 *    validé les prix, la page l'écrit au-dessus des montants, pas en note de bas
 *    de page. Et si aucun prestataire n'encaisse réellement, la page le dit
 *    aussi : elle n'ouvre pas un bouton de souscription qui ne mène nulle part.
 *
 * Si l'API est injoignable, la page **n'affiche aucun tarif** : elle le dit.
 * Inventer des prix pour remplir une page est précisément le défaut que C13
 * corrige côté backend.
 */

type PlanQuotas = {
  documents_per_month: number;
  ocr_pages_per_month: number;
  seats: number;
  retention_months: number;
};

type Plan = {
  code: string;
  name: string;
  tagline: string;
  price_cents_per_month_excl_vat: number | null;
  price_label: string;
  quotas: PlanQuotas;
  entitlements: string[];
  limits_are_reference_values: boolean;
};

type Catalogue = {
  catalogue_version: string;
  pricing_status: string;
  pricing_confirmed: boolean;
  overage_policy: string;
  provider: string;
  collects_money: boolean;
  selling_enabled: boolean;
  plans: Plan[];
  trial_plan_code: string;
  trial_days: number;
  notes: string[];
};

export const dynamic = "force-dynamic";

const numberFormatter = new Intl.NumberFormat("fr-FR");

function quotaRows(plan: Plan): Array<{ label: string; value: string }> {
  return [
    {
      label: "Documents importés",
      value: `${numberFormatter.format(plan.quotas.documents_per_month)} / mois`,
    },
    {
      label: "Pages analysées",
      value: `${numberFormatter.format(plan.quotas.ocr_pages_per_month)} / mois`,
    },
    {
      label: "Sièges",
      value: numberFormatter.format(plan.quotas.seats),
    },
    {
      label: "Conservation couverte",
      value: `${plan.quotas.retention_months} mois`,
    },
  ];
}

function EntitlementLine({ code }: { code: string }) {
  // Le libellé humain vit côté produit (`app/billing/plans.py`) ; l'API publie le
  // code appliqué. La page traduit les codes qu'elle sait afficher et montre le
  // code brut sinon — jamais une promesse inventée.
  const labels: Record<string, string> = {
    api_keys: "Clés d'API et intégrations machine à machine",
  };
  return <li>✓ {labels[code] ?? code}</li>;
}

export default async function PricingPage() {
  let catalogue: Catalogue | null = null;
  let unreachable = false;

  try {
    const response = await fetch(requestUrl("/api/v1/billing/plans"), {
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    catalogue = (await response.json()) as Catalogue;
  } catch {
    unreachable = true;
  }

  return (
    <PublicShell current="pricing">
      <main className="mx-auto max-w-6xl px-6 py-14">
        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-500">
          Offres et quotas
        </p>
        <h1 className="mt-2 text-4xl font-extrabold tracking-tight text-forest">
          Ce que chaque offre autorise, exactement.
        </h1>
        <p className="mt-3 max-w-3xl text-sm leading-relaxed text-slate-600">
          Les quotas ci-dessous sont ceux que le produit applique. Un quota atteint bloque
          l&apos;action suivante avec une erreur explicite qui dit quelle limite est atteinte et
          ce qu&apos;il reste : <strong>aucun dépassement n&apos;est facturé</strong>.
        </p>

        {unreachable && (
          <div className="mt-8 rounded-xl border border-amber-300 bg-amber-50 p-5 text-sm text-amber-900">
            <strong>Catalogue injoignable.</strong> Cette page affiche les offres telles que le
            produit les applique ; sans réponse de l&apos;API, elle n&apos;affiche <strong>aucun
            tarif</strong> plutôt qu&apos;une grille recopiée qui pourrait ne plus être la bonne.
            Réessayez dans un instant ou écrivez-nous depuis la page d&apos;aide.
          </div>
        )}

        {catalogue && (
          <>
            <div
              className={`mt-8 rounded-xl border p-5 text-sm ${
                catalogue.pricing_confirmed
                  ? "border-slate-200 bg-white text-slate-700"
                  : "border-amber-300 bg-amber-50 text-amber-900"
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <span className="font-semibold">
                  {catalogue.pricing_confirmed
                    ? "Prix validés commercialement."
                    : "Prix provisoires."}
                </span>
                <span className="text-xs uppercase tracking-wider">
                  Catalogue {catalogue.catalogue_version}
                </span>
              </div>
              <p className="mt-2 leading-relaxed">
                {catalogue.pricing_confirmed
                  ? "Les montants affichés sont ceux du catalogue en vigueur."
                  : `Montants ${catalogue.pricing_status}. Ils sont publiés tels quels pour que la grille ne se lise pas comme un engagement commercial qu'aucun humain n'a encore pris.`}
              </p>
              {catalogue.notes.map((note) => (
                <p key={note} className="mt-2 leading-relaxed">
                  {note}
                </p>
              ))}
            </div>

            <div className="mt-8 grid gap-6 md:grid-cols-3">
              {catalogue.plans.map((plan) => (
                <section
                  key={plan.code}
                  className="flex flex-col rounded-2xl border border-slate-200 bg-white p-6"
                >
                  <h2 className="text-lg font-extrabold text-forest">{plan.name}</h2>
                  <p className="mt-1 min-h-[40px] text-xs leading-relaxed text-slate-500">
                    {plan.tagline}
                  </p>
                  <p className="mt-4 text-2xl font-extrabold text-forest">{plan.price_label}</p>
                  {plan.price_cents_per_month_excl_vat === null && (
                    <p className="mt-1 text-xs text-slate-500">
                      Aucun montant publié : cette offre se traite au cas par cas.
                    </p>
                  )}
                  {plan.limits_are_reference_values && (
                    <p className="mt-2 rounded-lg bg-slate-50 p-2 text-[11px] leading-relaxed text-slate-600">
                      Quotas de <strong>référence</strong> : ce sont des plafonds indicatifs, pas
                      des plafonds négociés. Un contrat peut les ajuster.
                    </p>
                  )}

                  <dl className="mt-5 space-y-2 border-t border-slate-100 pt-4 text-xs">
                    {quotaRows(plan).map((row) => (
                      <div key={row.label} className="flex items-baseline justify-between gap-3">
                        <dt className="text-slate-500">{row.label}</dt>
                        <dd className="font-semibold text-slate-800">{row.value}</dd>
                      </div>
                    ))}
                  </dl>

                  {plan.entitlements.length > 0 && (
                    <ul className="mt-4 space-y-1 text-xs text-slate-600">
                      {plan.entitlements.map((code) => (
                        <EntitlementLine key={code} code={code} />
                      ))}
                    </ul>
                  )}
                </section>
              ))}
            </div>

            <div className="mt-8 grid gap-4 md:grid-cols-2">
              <div className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-700">
                <strong className="text-forest">Essai des {catalogue.trial_days} jours.</strong>
                <p className="mt-2 leading-relaxed">
                  Toute organisation démarre sur l&apos;offre{" "}
                  <strong>{catalogue.trial_plan_code}</strong> pendant {catalogue.trial_days}{" "}
                  jours, sans encaissement. L&apos;essai se calcule à partir de la date de
                  création de l&apos;organisation : il ne peut pas être relancé depuis
                  l&apos;interface, seulement par la création d&apos;une nouvelle organisation.
                </p>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-700">
                <strong className="text-forest">Souscrire aujourd&apos;hui.</strong>
                <p className="mt-2 leading-relaxed">
                  {catalogue.collects_money
                    ? "Le paiement est ouvert : la souscription se termine par un paiement réel chez notre prestataire."
                    : `Aucun encaissement n'est possible avec la configuration actuelle (prestataire « ${catalogue.provider} »). Les offres peuvent être consultées ; aucune souscription payante n'est ouverte, et cette page ne propose donc pas de bouton de paiement.`}
                </p>
                <div className="mt-3 flex flex-wrap gap-3 text-xs font-semibold">
                  <Link className="rounded-lg bg-forest px-3 py-2 text-white" href="/signup">
                    Créer un compte
                  </Link>
                  <Link className="rounded-lg border border-slate-300 px-3 py-2 text-forest" href="/aide">
                    Nous contacter
                  </Link>
                  <Link className="rounded-lg border border-slate-300 px-3 py-2 text-forest" href="/legal">
                    Conditions et mentions légales
                  </Link>
                </div>
              </div>
            </div>
          </>
        )}
      </main>
    </PublicShell>
  );
}
