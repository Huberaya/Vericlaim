"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { PublicShell } from "@/components/site/PublicShell";
import demoCases from "@/lib/demo-cases.json";

/**
 * Public demonstration.
 *
 * The results below are not a mock-up: they were produced by running the real
 * pipeline (API → durable worker → persisted verdicts) over the sample texts, and
 * `backend/tests/test_public_claims.py` fails if this file drifts from what the
 * engine currently returns. Visitors can browse the cases; analysing their own
 * documents requires an account, so the page never invites anyone to send
 * confidential material to an unauthenticated endpoint.
 */

type DemoClaim = {
  claim_type: string;
  claim_text: string;
  status: string;
  confidence_score: number | null;
  confidence_level: string | null;
  review_reasons: string[];
};

type DemoVerdict = {
  claim_type: string;
  claim_text: string;
  rule_id: string;
  rule_title: string;
  verdict: string;
  severity: string;
  is_legal_violation: boolean;
  legal_force: string;
  law_reference: string;
};

type DemoCase = {
  id: string;
  label: string;
  text: string;
  comment: string;
  claims: DemoClaim[];
  verdicts: DemoVerdict[];
  overall_compliance: string | null;
  risk_score: number | null;
  review_required_claim_count: number;
};

const VERDICT_STYLES: Record<string, string> = {
  STRICTLY_PROHIBITED: "bg-rose-100 text-rose-800",
  NON_COMPLIANT: "bg-rose-100 text-rose-800",
  CONDITIONAL_REJECT: "bg-amber-100 text-amber-900",
  REVIEW_REQUIRED: "bg-sky-100 text-sky-900",
  NOT_APPLICABLE: "bg-slate-100 text-slate-600",
  COMPLIANT: "bg-emerald-100 text-emerald-800",
};

const REVIEW_REASON_LABELS: Record<string, string> = {
  segment_ocr: "segment OCR",
  document_extraction_review_required: "extraction à revoir",
  polarity_conflict: "polarité contradictoire",
  verdict_requires_review: "verdict à arbitrer",
};

export default function DemoPage() {
  const cases = demoCases.cases as DemoCase[];
  const [activeId, setActiveId] = useState(cases[0]?.id ?? "");
  const active = useMemo(
    () => cases.find((item) => item.id === activeId) ?? cases[0],
    [activeId, cases],
  );

  if (!active) {
    return (
      <PublicShell current="demo">
        <div className="mx-auto max-w-6xl px-6 py-16 text-sm text-slate-600">
          Aucun cas de démonstration n&apos;est disponible dans cette version.
        </div>
      </PublicShell>
    );
  }

  return (
    <PublicShell current="demo">
      <section className="mx-auto max-w-6xl px-6 pt-14">
        <h1 className="text-3xl font-extrabold tracking-tight text-forest">
          Démonstration : analyses réellement produites par le moteur
        </h1>
        <p className="mt-4 max-w-3xl text-sm leading-relaxed text-slate-700">
          Les cinq textes ci-dessous ont été analysés par le pipeline complet, avec le support
          « {demoCases.context.surface} » en {demoCases.context.jurisdiction}. Ce que vous voyez est
          la sortie du moteur — verdicts, règles appliquées, niveaux de confiance, demandes de
          revue — et non une capture retouchée.
        </p>
        <p className="mt-3 max-w-3xl rounded-xl bg-white p-4 text-[11px] leading-relaxed text-slate-600">
          {demoCases.context.note} Moteur : {demoCases.engine.lexicon_version} · Rule Book{" "}
          <span className="font-mono">{demoCases.engine.rulebook_version}</span>.
          Un verdict dépend du support, de la juridiction et de la date : c&apos;est pourquoi ils
          sont figés et publiés dans chaque analyse.
        </p>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-8">
        <div className="flex flex-wrap gap-2">
          {cases.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setActiveId(item.id)}
              className={`rounded-xl border px-4 py-2 text-xs font-semibold transition ${
                item.id === active.id
                  ? "border-forest bg-forest text-white"
                  : "border-slate-300 bg-white text-slate-700 hover:border-forest"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="mt-6 grid gap-6 lg:grid-cols-[2fr,1fr]">
          <div className="space-y-6">
            <article className="rounded-2xl border border-slate-200 bg-white p-6">
              <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500">
                Texte analysé
              </h2>
              <p className="mt-3 text-sm leading-relaxed text-slate-800">« {active.text} »</p>
              <p className="mt-4 text-[11px] leading-relaxed text-slate-500">{active.comment}</p>
            </article>

            <article className="rounded-2xl border border-slate-200 bg-white p-6">
              <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500">
                Allégations détectées
              </h2>
              <ul className="mt-3 space-y-3">
                {active.claims.map((claim, index) => (
                  <li key={`${claim.claim_type}-${index}`} className="rounded-xl bg-canvas p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded bg-forest px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-white">
                        {claim.claim_type}
                      </span>
                      <span className="text-[10px] font-semibold text-slate-600">
                        statut : {claim.status}
                      </span>
                      {claim.confidence_score !== null && (
                        <span className="text-[10px] font-semibold text-slate-600">
                          confiance : {Math.round(claim.confidence_score * 100)} % ({claim.confidence_level})
                        </span>
                      )}
                      {claim.review_reasons.length > 0 && (
                        <span className="text-[10px] font-semibold text-amber-700">
                          revue : {claim.review_reasons.map((reason) => REVIEW_REASON_LABELS[reason] ?? reason).join(", ")}
                        </span>
                      )}
                    </div>
                    <p className="mt-2 text-xs text-slate-700">{claim.claim_text}</p>
                  </li>
                ))}
              </ul>
            </article>

            <article className="rounded-2xl border border-slate-200 bg-white p-6">
              <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500">
                Verdicts réglementaires
              </h2>
              <ul className="mt-3 space-y-3">
                {active.verdicts.map((verdict, index) => (
                  <li key={`${verdict.rule_id}-${index}`} className="rounded-xl border border-slate-200 p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`rounded px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${
                          VERDICT_STYLES[verdict.verdict] ?? "bg-slate-100 text-slate-600"
                        }`}
                      >
                        {verdict.verdict}
                      </span>
                      <span className="font-mono text-[10px] text-slate-500">{verdict.rule_id}</span>
                      <span className="text-[10px] text-slate-500">gravité : {verdict.severity}</span>
                      {verdict.is_legal_violation && (
                        <span className="rounded bg-rose-600 px-2 py-0.5 text-[10px] font-bold uppercase text-white">
                          violation
                        </span>
                      )}
                    </div>
                    <p className="mt-2 text-xs font-semibold text-forest">{verdict.rule_title}</p>
                    <p className="mt-1 text-[11px] leading-relaxed text-slate-600">
                      {verdict.law_reference}
                    </p>
                  </li>
                ))}
              </ul>
            </article>
          </div>

          <aside className="space-y-4">
            <div className="rounded-2xl border border-slate-200 bg-white p-5">
              <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500">
                Synthèse de l&apos;analyse
              </h2>
              <p className="mt-3 text-2xl font-extrabold text-forest">
                {active.overall_compliance ?? "—"}
              </p>
              <p className="mt-2 text-[11px] text-slate-600">
                Score de risque : {active.risk_score ?? "—"} / 100
              </p>
              <p className="mt-1 text-[11px] text-slate-600">
                Allégations à revoir : {active.review_required_claim_count}
              </p>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white p-5 text-[11px] leading-relaxed text-slate-600">
              <p className="font-semibold text-forest">Ce que cette page ne fait pas</p>
              <p className="mt-2">
                Elle n&apos;analyse pas votre texte : aucune donnée ne quitte votre navigateur, et
                l&apos;endpoint d&apos;analyse exige un compte. Pour vos propres documents,
                créez un espace de travail.
              </p>
              <p className="mt-2">
                Elle ne constitue pas un avis juridique : les verdicts citent les textes appliqués
                et leurs dates, à faire confirmer par un juriste.
              </p>
              <Link
                href="/app"
                className="mt-4 inline-block rounded-xl bg-forest px-4 py-2 text-xs font-semibold text-white transition hover:bg-forest/90"
              >
                Analyser mes documents
              </Link>
            </div>
          </aside>
        </div>
      </section>
    </PublicShell>
  );
}
