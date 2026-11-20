"use client";

import { useEffect, useState } from "react";
import {
  getAnalysisEvidenceMatrix,
  getVersionValidations,
  recordValidation,
} from "@/lib/api";
import type {
  EvidenceMatrix,
  PersistentClaim,
  Validation,
  ValidationDecision,
} from "@/lib/types";
import { SupplierEvidenceRequestModal } from "./SupplierEvidenceRequestModal";

const CONFIDENCE_LEVELS: Record<
  string,
  { label: string; hint: string; className: string }
> = {
  high: {
    label: "Élevée",
    hint: "Formulation ancrée, lisible et vérifiable dans le texte.",
    className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300",
  },
  medium: {
    label: "Moyenne",
    hint: "Détection lisible mais dépendante du contexte (polarité, portée).",
    className: "bg-amber-100 text-amber-800 dark:bg-amber-950/80 dark:text-amber-300",
  },
  low: {
    label: "Faible",
    hint: "Détection fragile : à relire avant tout usage.",
    className: "bg-orange-100 text-orange-800 dark:bg-orange-950/80 dark:text-orange-300",
  },
  human_review_required: {
    label: "Revue humaine requise",
    hint: "OCR, extraction douteuse, conflit de polarité ou verdict qui demande un arbitrage.",
    className: "bg-rose-100 text-rose-800 dark:bg-rose-950/80 dark:text-rose-300",
  },
};

const REVIEW_REASON_LABELS: Record<string, string> = {
  segment_ocr: "segment OCR",
  document_extraction_review_required: "extraction à revoir",
  polarity_conflict: "polarité contradictoire",
  verdict_requires_review: "verdict à arbitrer",
};

function confidenceBadge(claim: PersistentClaim) {
  const level = claim.confidence_level ?? null;
  if (level === null || claim.confidence_score === null) {
    return {
      label: "Non publiée",
      hint: "Analyse antérieure à la rubrique de confiance (confidence-rubric-v1).",
      className: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
      score: null as number | null,
    };
  }
  const known = CONFIDENCE_LEVELS[level];
  return {
    label: known?.label ?? level,
    hint: known?.hint ?? "",
    className: known?.className ?? "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
    score: claim.confidence_score,
  };
}

interface HumanReviewPanelProps {
  analysisId: string;
  versionNumber: number;
  claims: PersistentClaim[];
  onRefresh?: () => void;
}

export function HumanReviewPanel({
  analysisId,
  versionNumber,
  claims,
  onRefresh,
}: HumanReviewPanelProps) {
  const [matrix, setMatrix] = useState<EvidenceMatrix | null>(null);
  const [validations, setValidations] = useState<Validation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filter state
  const [decisionFilter, setDecisionFilter] = useState<string>("all");
  const [searchTerm, setSearchTerm] = useState("");

  // Review action modal / dialog state
  const [activeClaimForReview, setActiveClaimForReview] = useState<PersistentClaim | null>(null);
  const [reviewDecision, setReviewDecision] = useState<ValidationDecision>("validated");
  const [reviewComment, setReviewComment] = useState("");
  const [reviewRationale, setReviewRationale] = useState("");
  const [isSubmittingReview, setIsSubmittingReview] = useState(false);

  // Supplier request modal state
  const [requestModalClaim, setRequestModalClaim] = useState<PersistentClaim | null>(null);

  async function loadData() {
    setIsLoading(true);
    setError(null);
    try {
      const [mat, vals] = await Promise.all([
        getAnalysisEvidenceMatrix(analysisId, versionNumber),
        getVersionValidations(analysisId, versionNumber),
      ]);
      setMatrix(mat);
      setValidations(vals);
    } catch (err: unknown) {
      console.error("Failed to load review data:", err);
      setError(err instanceof Error ? err.message : "Erreur de chargement des données d'arbitrage");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    loadData();
  }, [analysisId, versionNumber]);

  // Map of claim_id -> Validation
  const validationMap = new Map<string, Validation>();
  for (const v of validations) {
    validationMap.set(v.claim_id, v);
  }

  const filteredClaims = claims.filter((claim: PersistentClaim) => {
    const val = validationMap.get(claim.id);
    const decision = val ? val.decision : "pending";

    if (decisionFilter !== "all" && decision !== decisionFilter) return false;
    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      const matchText = claim.claim_text.toLowerCase().includes(term);
      const matchCat = claim.category?.toLowerCase().includes(term);
      if (!matchText && !matchCat) return false;
    }
    return true;
  });

  const validatedCount = validations.filter((v) => v.decision === "validated").length;
  const contestedCount = validations.filter((v) => v.decision === "contested").length;
  const pendingCount = claims.length - validatedCount - contestedCount;

  function openReviewModal(claim: PersistentClaim, decision: ValidationDecision) {
    setActiveClaimForReview(claim);
    setReviewDecision(decision);
    const existing = validationMap.get(claim.id);
    setReviewComment(existing?.comment || "");
    setReviewRationale(existing?.rationale || "");
  }

  async function handleSaveReview() {
    if (!activeClaimForReview) return;
    setIsSubmittingReview(true);
    setError(null);
    try {
      await recordValidation({
        analysis_version_id: activeClaimForReview.analysis_version_id,
        claim_id: activeClaimForReview.id,
        decision: reviewDecision,
        comment: reviewComment.trim() || null,
        rationale: reviewRationale.trim() || null,
      });
      setActiveClaimForReview(null);
      await loadData();
      onRefresh?.();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Erreur lors de l'enregistrement de l'arbitrage");
    } finally {
      setIsSubmittingReview(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
            Total Allégations
          </span>
          <div className="mt-2 text-2xl font-bold text-slate-900 dark:text-white">
            {claims.length}
          </div>
          <p className="mt-1 text-xs text-slate-500">Version active v{versionNumber}</p>
        </div>

        <div className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-4 shadow-sm dark:border-emerald-900/50 dark:bg-emerald-950/20">
          <span className="text-xs font-semibold uppercase tracking-wider text-emerald-700 dark:text-emerald-400">
            Validées (Arbitrage Humain)
          </span>
          <div className="mt-2 text-2xl font-bold text-emerald-700 dark:text-emerald-400">
            {validatedCount}
          </div>
          <p className="mt-1 text-xs text-emerald-600 dark:text-emerald-500">Preuves jugées suffisantes</p>
        </div>

        <div className="rounded-xl border border-amber-200 bg-amber-50/50 p-4 shadow-sm dark:border-amber-900/50 dark:bg-amber-950/20">
          <span className="text-xs font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400">
            En Attente de Revue
          </span>
          <div className="mt-2 text-2xl font-bold text-amber-700 dark:text-amber-400">
            {pendingCount}
          </div>
          <p className="mt-1 text-xs text-amber-600 dark:text-amber-500">Nécessite contrôle expert</p>
        </div>

        <div className="rounded-xl border border-rose-200 bg-rose-50/50 p-4 shadow-sm dark:border-rose-900/50 dark:bg-rose-950/20">
          <span className="text-xs font-semibold uppercase tracking-wider text-rose-700 dark:text-rose-400">
            Contestées / Refusées
          </span>
          <div className="mt-2 text-2xl font-bold text-rose-700 dark:text-rose-400">
            {contestedCount}
          </div>
          <p className="mt-1 text-xs text-rose-600 dark:text-rose-500">Risque de greenwashing avéré</p>
        </div>
      </div>

      {/* Human in the loop Notice */}
      <div className="rounded-xl border border-indigo-200 bg-indigo-50/60 p-4 dark:border-indigo-900/60 dark:bg-indigo-950/30">
        <div className="flex items-start gap-3">
          <svg className="h-5 w-5 text-indigo-600 dark:text-indigo-400 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <div className="text-xs text-indigo-950 dark:text-indigo-200 leading-relaxed">
            <strong className="font-semibold text-indigo-900 dark:text-indigo-300">Gouvernance Human-in-the-Loop :</strong> Le moteur IA détecte et qualifie les allégations selon la directive européenne 2024/825 et la loi AGEC. Chaque décision d&apos;homologation finale ou de contestation reste sous la responsabilité exclusive d&apos;un auditeur ou juriste certifié.
          </div>
        </div>
      </div>

      {/* Filters and search */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setDecisionFilter("all")}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              decisionFilter === "all"
                ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900"
                : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"
            }`}
          >
            Tous ({claims.length})
          </button>
          <button
            onClick={() => setDecisionFilter("pending")}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              decisionFilter === "pending"
                ? "bg-amber-600 text-white"
                : "bg-amber-50 text-amber-700 hover:bg-amber-100 dark:bg-amber-950/40 dark:text-amber-300"
            }`}
          >
            En Attente ({pendingCount})
          </button>
          <button
            onClick={() => setDecisionFilter("validated")}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              decisionFilter === "validated"
                ? "bg-emerald-600 text-white"
                : "bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-950/40 dark:text-emerald-300"
            }`}
          >
            Validées ({validatedCount})
          </button>
          <button
            onClick={() => setDecisionFilter("contested")}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              decisionFilter === "contested"
                ? "bg-rose-600 text-white"
                : "bg-rose-50 text-rose-700 hover:bg-rose-100 dark:bg-rose-950/40 dark:text-rose-300"
            }`}
          >
            Contestées ({contestedCount})
          </button>
        </div>

        <input
          type="text"
          placeholder="Filtrer les allégations..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
        />
      </div>

      {/* Bandeau permanent : ce que le score est, et ce qu'il n'est pas. */}
      <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-[11px] leading-relaxed text-slate-600 dark:border-slate-800 dark:bg-slate-900/60 dark:text-slate-300">
        <p>
          <strong>Détection déterministe (confidence-rubric-v1), ne constitue pas un avis juridique.</strong>{" "}
          Le score de confiance additionne des éléments lisibles dans le texte (formulation ancrée,
          chiffre, qualificatif, numéro de certification, polarité, qualité de l&apos;extraction) :
          ce n&apos;est ni une probabilité, ni une appréciation du risque juridique, ni une décision
          d&apos;autorité. Une allégation marquée « revue humaine requise » doit être relue avant toute
          conclusion, et une allégation non détectée ne vaut pas conformité.
        </p>
      </div>

      {/* Claims Review Table */}
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <table className="w-full text-left text-xs">
          <thead className="border-b border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-400">
            <tr>
              <th className="py-3 px-4 font-semibold">Allégation environnementale</th>
              <th className="py-3 px-4 font-semibold">Catégorie</th>
              <th className="py-3 px-4 font-semibold">Confiance de détection</th>
              <th className="py-3 px-4 font-semibold">Statut Couverture Preuves</th>
              <th className="py-3 px-4 font-semibold">Décision d&apos;Arbitrage</th>
              <th className="py-3 px-4 font-semibold text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
            {filteredClaims.map((claim: PersistentClaim) => {
              const val = validationMap.get(claim.id);
              const decision = val ? val.decision : "pending";

              // Find evidence matrix row
              const matRow = matrix?.matrix_rows.find((r) => r.claim.id === claim.id);
              const coverage = matRow ? matRow.coverage_status : "missing";

              return (
                <tr key={claim.id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/40 transition">
                  <td className="py-3.5 px-4 max-w-sm">
                    <p className="font-semibold text-slate-900 dark:text-white leading-snug">
                      &ldquo;{claim.claim_text}&rdquo;
                    </p>
                    {val?.comment && (
                      <div className="mt-1 text-[11px] text-slate-500 italic bg-slate-50 p-1.5 rounded dark:bg-slate-800/60 dark:text-slate-400">
                        💬 &ldquo;{val.comment}&rdquo;
                        {val.reviewer_display_name && (
                          <span className="block not-italic font-medium text-slate-600 dark:text-slate-300 mt-0.5">
                            — {val.reviewer_display_name}
                          </span>
                        )}
                      </div>
                    )}
                  </td>
                  <td className="py-3.5 px-4 whitespace-nowrap">
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-300 uppercase tracking-wider">
                      {claim.category || claim.claim_type}
                    </span>
                  </td>
                  <td className="py-3.5 px-4">
                    {(() => {
                      const badge = confidenceBadge(claim);
                      return (
                        <div className="space-y-1">
                          <span
                            className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-semibold ${badge.className}`}
                            title={badge.hint}
                          >
                            {badge.label}
                            {badge.score !== null && (
                              <span className="font-normal opacity-80">
                                {Math.round(badge.score * 100)} %
                              </span>
                            )}
                          </span>
                          {(claim.review_reasons ?? []).length > 0 && (
                            <span className="block text-[10px] text-slate-500 dark:text-slate-400">
                              {(claim.review_reasons ?? [])
                                .map((reason) => REVIEW_REASON_LABELS[reason] ?? reason)
                                .join(" · ")}
                            </span>
                          )}
                        </div>
                      );
                    })()}
                  </td>
                  <td className="py-3.5 px-4 whitespace-nowrap">
                    {coverage === "verified" && (
                      <span className="inline-flex items-center gap-1 rounded bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300">
                        ● Complète ({matRow?.evidence_links.length || 0})
                      </span>
                    )}
                    {coverage === "expired" && (
                      <span className="inline-flex items-center gap-1 rounded bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800 dark:bg-amber-950/80 dark:text-amber-300">
                        ⚠ Preuve expirée
                      </span>
                    )}
                    {coverage === "missing" && (
                      <span className="inline-flex items-center gap-1 rounded bg-rose-100 px-2 py-0.5 text-[11px] font-semibold text-rose-800 dark:bg-rose-950/80 dark:text-rose-300">
                        ✕ Aucune preuve
                      </span>
                    )}
                  </td>
                  <td className="py-3.5 px-4 whitespace-nowrap">
                    {decision === "validated" && (
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-semibold text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300">
                        ✓ Validée
                      </span>
                    )}
                    {decision === "contested" && (
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-100 px-2.5 py-1 text-xs font-semibold text-rose-800 dark:bg-rose-950/80 dark:text-rose-300">
                        ✕ Contestée
                      </span>
                    )}
                    {decision === "pending" && (
                      <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-800 dark:bg-amber-950/80 dark:text-amber-300">
                        ⏳ En attente
                      </span>
                    )}
                  </td>
                  <td className="py-3.5 px-4 text-right whitespace-nowrap">
                    <div className="flex items-center justify-end gap-1.5">
                      <button
                        onClick={() => openReviewModal(claim, "validated")}
                        title="Valider l'allégation"
                        className="rounded bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-950/40 dark:text-emerald-300 dark:hover:bg-emerald-900"
                      >
                        Valider
                      </button>
                      <button
                        onClick={() => openReviewModal(claim, "contested")}
                        title="Contester l'allégation"
                        className="rounded bg-rose-50 px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-100 dark:bg-rose-950/40 dark:text-rose-300 dark:hover:bg-rose-900"
                      >
                        Contester
                      </button>
                      <button
                        onClick={() => setRequestModalClaim(claim)}
                        title="Demander justificatif au fournisseur"
                        className="rounded border border-indigo-200 bg-white px-2 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-50 dark:border-indigo-800 dark:bg-slate-800 dark:text-indigo-300 dark:hover:bg-indigo-950"
                      >
                        ✉ Demander preuve
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Review Dialog */}
      {activeClaimForReview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4">
          <div className="relative w-full max-w-lg rounded-xl bg-white p-6 shadow-2xl border border-slate-200 dark:bg-slate-900 dark:border-slate-800 space-y-4">
            <h3 className="text-base font-bold text-slate-900 dark:text-white">
              Arbitrage de l&apos;allégation
            </h3>

            <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300">
              <span className="font-semibold">Allégation :</span> &ldquo;{activeClaimForReview.claim_text}&rdquo;
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Décision d&apos;homologation
              </label>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setReviewDecision("validated")}
                  className={`flex-1 rounded-lg py-2 text-xs font-bold transition ${
                    reviewDecision === "validated"
                      ? "bg-emerald-600 text-white shadow"
                      : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"
                  }`}
                >
                  ✓ Valider la conformité
                </button>
                <button
                  type="button"
                  onClick={() => setReviewDecision("contested")}
                  className={`flex-1 rounded-lg py-2 text-xs font-bold transition ${
                    reviewDecision === "contested"
                      ? "bg-rose-600 text-white shadow"
                      : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"
                  }`}
                >
                  ✕ Contester / Rejeter
                </button>
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Commentaire d&apos;expertise (Optionnel)
              </label>
              <textarea
                rows={2}
                value={reviewComment}
                onChange={(e) => setReviewComment(e.target.value)}
                placeholder="ex. Certificat ISO 14044 vérifié auprès du tiers déclarant."
                className="w-full rounded-lg border border-slate-300 bg-white p-2.5 text-xs text-slate-900 focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Fondement juridique / Rationale
              </label>
              <input
                type="text"
                value={reviewRationale}
                onChange={(e) => setReviewRationale(e.target.value)}
                placeholder="ex. Conformité Art. L. 229-68 Code Environnement"
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-900 focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
              />
            </div>

            <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-200 dark:border-slate-800">
              <button
                type="button"
                onClick={() => setActiveClaimForReview(null)}
                className="rounded-lg px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
              >
                Annuler
              </button>
              <button
                type="button"
                disabled={isSubmittingReview}
                onClick={handleSaveReview}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700 disabled:opacity-50"
              >
                {isSubmittingReview ? "Enregistrement..." : "Enregistrer la décision"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Supplier Evidence Request Modal */}
      {requestModalClaim && (
        <SupplierEvidenceRequestModal
          isOpen={true}
          onClose={() => setRequestModalClaim(null)}
          initialClaimId={requestModalClaim.id}
          initialClaimText={requestModalClaim.claim_text}
          onCreated={() => {
            loadData();
            onRefresh?.();
          }}
        />
      )}
    </div>
  );
}
