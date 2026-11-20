import type { Metadata } from "next";
import Link from "next/link";
import { PublicShell } from "@/components/site/PublicShell";
import {
  displayValue,
  isMissing,
  legalFacts,
  rightLabel,
  type LegalField,
} from "@/lib/legal-facts";

/**
 * Informations légales (chantier C12).
 *
 * Two rules govern this page, and both are enforced by
 * `backend/tests/test_public_claims.py` rather than by good intentions:
 *
 * 1. Nothing company-specific is invented. The publisher's identity, the host and
 *    the data-protection contact are `null` in `legal-facts.json` and rendered as
 *    "à compléter". A plausible-looking SIREN would be a legal defect, not a
 *    cosmetic shortcut.
 * 2. The rights table is the product's own table. It is regenerated from
 *    `app/privacy/rights.py`; a right that is not served is published as not
 *    served, because a customer reading "effacement : outillé" must be able to
 *    send the request that the product actually serves.
 *
 * The external review status is displayed, not hidden: an audit of the code
 * cannot declare legal conformity, and this page must not imply otherwise.
 */

export const metadata: Metadata = {
  title: "Informations légales — VeriClaim",
  description:
    "Mentions légales, exercice des droits, cookies, durées de conservation et sous-traitants : ce que le produit fait réellement, et ce qui reste à valider.",
};

function FieldList({ fields }: { fields: LegalField[] }) {
  return (
    <dl className="mt-4 divide-y divide-slate-100 rounded-xl border border-slate-200">
      {fields.map((field) => (
        <div key={field.label} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-4 py-3">
          <dt className="w-64 text-xs font-semibold uppercase tracking-wide text-slate-500">
            {field.label}
          </dt>
          <dd
            className={
              isMissing(field.value)
                ? "rounded-md bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-700"
                : "text-sm text-slate-800"
            }
          >
            {displayValue(field.value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-24 border-t border-slate-200 pt-10">
      <h2 className="text-lg font-extrabold tracking-tight text-forest">{title}</h2>
      <div className="mt-4 space-y-3 text-sm leading-relaxed text-slate-700">{children}</div>
    </section>
  );
}

export default function LegalPage() {
  const facts = legalFacts;
  const { rights, cookies } = facts;

  return (
    <PublicShell current="legal">
      <main className="mx-auto max-w-4xl px-6 py-14">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">
          Informations légales
        </p>
        <h1 className="mt-3 text-3xl font-extrabold tracking-tight text-forest">
          Ce que le service fait, ce qu&apos;il ne fait pas, et ce qui reste à valider
        </h1>
        <p className="mt-4 max-w-3xl text-sm leading-relaxed text-slate-600">
          Cette page n&apos;est pas une plaquette : chaque affirmation technique ci-dessous est
          vérifiable dans le code et couverte par un test qui échoue si elle devient fausse. Les
          mentions qui dépendent de l&apos;exploitation sont affichées comme manquantes plutôt que
          complétées au hasard.
        </p>

        <div className="mt-8 rounded-2xl border border-amber-300 bg-amber-50 p-5">
          <p className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-amber-200 px-3 py-1 text-[11px] font-bold uppercase tracking-wide text-amber-900">
              {facts.review.label}
            </span>
            <span className="text-xs font-semibold text-amber-900">
              Revue juridique externe : non réalisée
            </span>
          </p>
          <p className="mt-3 text-sm leading-relaxed text-amber-900">{facts.review.statement}</p>
          <p className="mt-3 text-xs leading-relaxed text-amber-800">
            {facts.review.missing_fields_notice}
          </p>
        </div>

        <nav className="mt-8 flex flex-wrap gap-2 text-xs font-semibold text-slate-600">
          {[
            ["editeur", facts.publisher.title],
            ["hebergement", facts.hosting.title],
            ["droits", facts.rights.title],
            ["cookies", facts.cookies.title],
            ["conservation", facts.retention.title],
            ["transferts", facts.transfers.title],
            ["limites", facts.gaps.title],
            ["documents", facts.documents.title],
          ].map(([anchor, label]) => (
            <a
              key={anchor}
              href={`#${anchor}`}
              className="rounded-lg border border-slate-200 px-3 py-1.5 transition hover:border-forest hover:text-forest"
            >
              {label}
            </a>
          ))}
        </nav>

        <div className="mt-10 space-y-10">
          <Section id="editeur" title={facts.publisher.title}>
            <FieldList fields={facts.publisher.fields} />
            <p className="text-xs text-slate-500">{facts.publisher.note}</p>
          </Section>

          <Section id="hebergement" title={facts.hosting.title}>
            <FieldList fields={facts.hosting.fields} />
            <p className="text-xs text-slate-500">{facts.hosting.note}</p>
          </Section>

          <Section id="droits" title={rights.title}>
            <p>
              Délai de réponse : <strong>{rights.response_window_months} mois</strong>, prolongeable
              de <strong>{rights.extension_months} mois</strong> sur motif écrit. Le délai est
              calculé par le système à partir de la date de réception réelle, et une prolongation ne
              remet pas la date d&apos;origine à zéro.
            </p>
            <p className="rounded-xl border border-slate-200 bg-white p-4 text-xs text-slate-600">
              <strong className="text-slate-800">Contact pour une demande : </strong>
              {rights.contact_email ?? "non configuré"}
              <span className="mt-1 block text-slate-500">{rights.contact_note}</span>
            </p>
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="w-full min-w-[46rem] border-collapse text-left text-xs">
                <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-4 py-3">Droit</th>
                    <th className="px-4 py-3">Référence</th>
                    <th className="px-4 py-3">État</th>
                    <th className="px-4 py-3">Ce que cela implique</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {rights.handling.map((item) => (
                    <tr key={item.request_type} className="align-top">
                      <td className="px-4 py-3 font-semibold text-slate-800">
                        {rightLabel(item.request_type)}
                      </td>
                      <td className="px-4 py-3 text-slate-500">{item.article}</td>
                      <td className="px-4 py-3">
                        <span
                          className={
                            item.automated
                              ? "rounded-full bg-forest/10 px-2.5 py-1 font-semibold text-forest"
                              : "rounded-full bg-slate-100 px-2.5 py-1 font-semibold text-slate-600"
                          }
                        >
                          {item.automated ? rights.automated_label : rights.manual_label}
                        </span>
                        {item.route ? (
                          <span className="mt-2 block font-mono text-[11px] text-slate-500">
                            {item.route}
                          </span>
                        ) : null}
                      </td>
                      <td className="px-4 py-3 text-slate-600">{item.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-slate-500">
              Source affichée sans recopie manuelle : tableau lu depuis{" "}
              <code className="rounded bg-slate-100 px-1.5 py-0.5">{rights.procedure_path}</code>,
              lui-même produit par <code className="rounded bg-slate-100 px-1.5 py-0.5">app/privacy/rights.py</code>.
              Un test compare les deux : la page ne peut pas décrire un autre produit que celui qui
              tourne.
            </p>
          </Section>

          <Section id="cookies" title={cookies.title}>
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="w-full min-w-[42rem] border-collapse text-left text-xs">
                <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-4 py-3">Nom</th>
                    <th className="px-4 py-3">Rôle</th>
                    <th className="px-4 py-3">Durée</th>
                    <th className="px-4 py-3">Attributs</th>
                    <th className="px-4 py-3">Consentement</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {cookies.items.map((cookie) => (
                    <tr key={cookie.name} className="align-top">
                      <td className="px-4 py-3 font-mono text-[11px] text-slate-800">
                        {cookie.name}
                      </td>
                      <td className="px-4 py-3 text-slate-600">{cookie.purpose}</td>
                      <td className="px-4 py-3 text-slate-600">{cookie.duration}</td>
                      <td className="px-4 py-3 text-slate-600">{cookie.attributes}</td>
                      <td className="px-4 py-3 text-slate-600">
                        {cookie.consent_required ? "requis" : "non requis (nécessaire)"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-slate-500">{cookies.note}</p>
          </Section>

          <Section id="conservation" title={facts.retention.title}>
            <ul className="list-disc space-y-2 pl-5">
              {facts.retention.rules.map((rule) => (
                <li key={rule}>{rule}</li>
              ))}
            </ul>
          </Section>

          <Section id="transferts" title={facts.transfers.title}>
            <p className="rounded-xl border border-forest/20 bg-forest/5 p-4 text-forest">
              {facts.transfers.no_ai_statement}
            </p>
            <p className="text-xs text-slate-500">
              Vérifié par :{" "}
              {facts.transfers.verified_by.map((reference, index) => (
                <span key={reference}>
                  {index > 0 ? ", " : ""}
                  <code className="rounded bg-slate-100 px-1.5 py-0.5">{reference}</code>
                </span>
              ))}
            </p>
            <p className="mt-2 font-semibold text-slate-800">Flux sortants de l&apos;instance</p>
            <ul className="list-disc space-y-1.5 pl-5">
              {facts.transfers.outbound_flows.map((flow) => (
                <li key={flow}>{flow}</li>
              ))}
            </ul>
          </Section>

          <Section id="limites" title={facts.gaps.title}>
            <ul className="list-disc space-y-2 pl-5">
              {facts.gaps.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </Section>

          <Section id="documents" title={facts.documents.title}>
            <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
              {facts.documents.items.map((document) => (
                <li key={document.path} className="flex flex-wrap items-baseline gap-x-3 px-4 py-3">
                  <span className="font-semibold text-slate-800">{document.title}</span>
                  <code className="font-mono text-[11px] text-slate-500">{document.path}</code>
                  <span className="ml-auto text-[11px] font-semibold uppercase tracking-wide text-amber-700">
                    {document.status}
                  </span>
                </li>
              ))}
            </ul>
            <p className="text-xs text-slate-500">{facts.documents.served_note}</p>
          </Section>
        </div>

        <div className="mt-14 rounded-2xl border border-slate-200 bg-white p-6">
          <p className="text-sm text-slate-600">
            Une question sur ces informations, ou une demande d&apos;exercice de droits à
            enregistrer ?{" "}
            <Link href="/" className="font-semibold text-forest underline">
              Revenir à la présentation du produit
            </Link>
            .
          </p>
        </div>
      </main>
    </PublicShell>
  );
}
