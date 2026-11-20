import Link from "next/link";
import { PublicShell } from "@/components/site/PublicShell";
import { percent, publicClaims, publishedFacts } from "@/lib/public-claims";

/**
 * Public landing page.
 *
 * Every claim rendered here comes from `src/lib/public-claims.json` and is
 * verified against the running product by `backend/tests/test_public_claims.py`.
 * Nothing on this page is an aspiration: if a capability is not implemented and
 * tested, its sentence cannot be added, because the evidence key would fail.
 */

const STEPS = [
  {
    title: "1. Extraction du document",
    body: "Le texte est découpé en segments traçables (page, type, empreinte). L'OCR est isolé : un segment reconnu optiquement est marqué pour revue, jamais utilisé en silence.",
  },
  {
    title: "2. Détection lexicale",
    body: "Un lexique versionné repère les formulations à risque (recyclé, biodégradable, neutre en carbone, labels, « éco »...). Chaque détection publie son déclencheur, sa position exacte et sa polarité.",
  },
  {
    title: "3. Verdicts réglementaires",
    body: "Les allégations sont confrontées à un Rule Book versionné (AGEC, directive (UE) 2024/825, ISO 14021...) et aux preuves réellement enregistrées dans votre espace.",
  },
  {
    title: "4. Rapport signé et vérifiable",
    body: "Le rapport est rendu depuis la version d'analyse persistée, signé, et vérifiable par un tiers via une référence publique.",
  },
];

export default function LandingPage() {
  return (
    <PublicShell current="home">
      <section className="mx-auto max-w-6xl px-6 pt-16 pb-10">
        <p className="text-xs font-bold uppercase tracking-[0.18em] text-slate-500">
          Conformité des allégations environnementales
        </p>
        <h1 className="mt-4 max-w-3xl text-4xl font-extrabold leading-tight tracking-tight text-forest md:text-5xl">
          Vérifiez vos allégations environnementales avant qu&apos;un régulateur ne le fasse.
        </h1>
        <p className="mt-6 max-w-2xl text-base leading-relaxed text-slate-700">
          VeriClaim est un <strong>moteur réglementaire déterministe, reproductible et
          auditable</strong>. Il détecte les allégations à risque dans vos supports,
          les confronte aux textes applicables et aux preuves que vous détenez, et
          produit un rapport opposable — sans envoyer vos documents à un modèle d&apos;IA.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link
            href="/demo"
            className="rounded-xl bg-forest px-6 py-3 text-sm font-semibold text-white transition hover:bg-forest/90"
          >
            Voir une analyse réelle, sans compte
          </Link>
          <Link
            href="/app"
            className="rounded-xl border border-slate-300 bg-white px-6 py-3 text-sm font-semibold text-forest transition hover:border-forest"
          >
            Créer un compte ou se connecter
          </Link>
        </div>
        <p className="mt-4 text-[11px] text-slate-500">
          La démonstration utilise de vrais résultats du moteur, produits hors ligne sur
          des textes d&apos;exemple. Vos propres documents ne sont pas transmis par cette page.
        </p>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-10">
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((step) => (
            <article key={step.title} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-panel">
              <h2 className="text-sm font-bold text-forest">{step.title}</h2>
              <p className="mt-2 text-xs leading-relaxed text-slate-600">{step.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-10">
        <h2 className="text-2xl font-extrabold tracking-tight text-forest">
          Ce que nous affirmons, et comment c&apos;est vérifiable
        </h2>
        <p className="mt-3 max-w-3xl text-sm leading-relaxed text-slate-600">
          Chaque phrase ci-dessous correspond à un comportement du produit contrôlé par un test
          à chaque exécution de la suite. Aucune affirmation de cette page n&apos;existe uniquement
          dans un argumentaire commercial.
        </p>
        <ul className="mt-6 grid gap-3 md:grid-cols-2">
          {publicClaims.map((claim) => (
            <li key={claim.id} className="rounded-2xl border border-slate-200 bg-white p-5">
              <p className="text-sm font-semibold text-forest">{claim.text}</p>
              <p className="mt-2 text-xs leading-relaxed text-slate-600">{claim.detail}</p>
              <p className="mt-3 font-mono text-[10px] uppercase tracking-wider text-slate-400">
                preuve : {claim.evidence_key}
              </p>
            </li>
          ))}
        </ul>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-10">
        <div className="rounded-2xl border border-slate-200 bg-white p-6">
          <h2 className="text-lg font-extrabold text-forest">Chiffres publiés</h2>
          <div className="mt-5 grid gap-6 md:grid-cols-4">
            <div>
              <p className="text-3xl font-extrabold text-forest">
                {percent(publishedFacts.corpus_detection_rate)}
              </p>
              <p className="mt-1 text-xs text-slate-600">
                des {publishedFacts.corpus_positive_cases} cas positifs du corpus de référence détectés
              </p>
            </div>
            <div>
              <p className="text-3xl font-extrabold text-forest">
                {percent(publishedFacts.corpus_false_positive_rate)}
              </p>
              <p className="mt-1 text-xs text-slate-600">
                de faux positifs sur les {publishedFacts.corpus_negative_cases} cas négatifs
              </p>
            </div>
            <div>
              <p className="text-3xl font-extrabold text-forest">{publishedFacts.rule_count}</p>
              <p className="mt-1 text-xs text-slate-600">règles réglementaires versionnées</p>
            </div>
            <div>
              <p className="font-mono text-sm font-bold text-forest">{publishedFacts.lexicon_version}</p>
              <p className="mt-1 text-xs text-slate-600">révision du lexique de détection</p>
            </div>
          </div>
          <p className="mt-5 rounded-xl bg-canvas p-4 text-[11px] leading-relaxed text-slate-600">
            <strong>Ce que ces chiffres ne disent pas.</strong> {publishedFacts.corpus_note} Un texte
            hors lexique, une image ou un pictogramme ne sont pas détectés — et une allégation non
            détectée ne signifie pas que le support est conforme. La révision du lexique et cette
            limite sont publiées dans chaque analyse.
          </p>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-10">
        <h2 className="text-2xl font-extrabold tracking-tight text-forest">
          Ce que le produit n&apos;est pas
        </h2>
        <div className="mt-6 grid gap-4 md:grid-cols-3">
          <article className="rounded-2xl border border-slate-200 bg-white p-5">
            <h3 className="text-sm font-bold text-forest">Pas une IA</h3>
            <p className="mt-2 text-xs leading-relaxed text-slate-600">
              Aucun LLM, aucun modèle statistique, aucune génération de texte. Le nom commercial
              historique contient « AI » : nous ne l&apos;utilisons pas comme argument. Un moteur
              déterministe se rejoue et se vérifie, un modèle se discute.
            </p>
          </article>
          <article className="rounded-2xl border border-slate-200 bg-white p-5">
            <h3 className="text-sm font-bold text-forest">Pas un avis juridique</h3>
            <p className="mt-2 text-xs leading-relaxed text-slate-600">
              Les verdicts sont des signaux de risque fondés sur des textes cités, datés et
              versionnés. Ils ne remplacent ni un juriste, ni une décision d&apos;autorité. Le
              statut d&apos;une transposition nationale inconnue du moteur donne « revue requise »,
              jamais « interdit ».
            </p>
          </article>
          <article className="rounded-2xl border border-slate-200 bg-white p-5">
            <h3 className="text-sm font-bold text-forest">Pas une boîte noire</h3>
            <p className="mt-2 text-xs leading-relaxed text-slate-600">
              Chaque verdict publie sa règle, sa référence légale, ses étapes de raisonnement, ses
              preuves manquantes et son niveau de confiance — calculé par une rubrique publiée,
              jamais par un modèle.
            </p>
          </article>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-14">
        <div className="rounded-3xl bg-forest px-8 py-10 text-white">
          <h2 className="text-2xl font-extrabold tracking-tight">
            Essayer sur un texte d&apos;exemple, puis sur vos documents
          </h2>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/80">
            La démonstration montre des analyses réellement produites par le moteur. Pour analyser
            vos propres supports, créez un compte : vos documents restent dans votre espace et ne
            sont transmis à aucun service tiers.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link
              href="/demo"
              className="rounded-xl bg-lime px-6 py-3 text-sm font-bold text-forest transition hover:bg-lime/90"
            >
              Ouvrir la démonstration
            </Link>
            <Link
              href="/pricing"
              className="rounded-xl border border-white/25 px-6 py-3 text-sm font-semibold text-white transition hover:border-white/60"
            >
              Voir les tarifs et quotas
            </Link>
          </div>
        </div>
      </section>
    </PublicShell>
  );
}
