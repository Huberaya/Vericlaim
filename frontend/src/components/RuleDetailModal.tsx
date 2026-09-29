"use client";

import { useEffect, useState } from "react";
import { getRegulatoryRuleDetail, submitRuleReview } from "@/lib/api";
import type {
  ConfidenceLevel,
  RegulatoryRuleDetail,
  ReviewStatus,
} from "@/lib/types";

interface RuleDetailModalProps {
  ruleId: string;
  isOpen: boolean;
  canManage: boolean;
  onClose: () => void;
  onUpdated?: () => void;
}

export function RuleDetailModal({
  ruleId,
  isOpen,
  canManage,
  onClose,
  onUpdated,
}: RuleDetailModalProps) {
  const [rule, setRule] = useState<RegulatoryRuleDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Review submission state
  const [isReviewing, setIsReviewing] = useState(false);
  const [reviewStatus, setReviewStatus] = useState<ReviewStatus>("approved_legal");
  const [confidenceLevel, setConfidenceLevel] = useState<ConfidenceLevel>("high");
  const [legalNotes, setLegalNotes] = useState("");
  const [isSubmittingReview, setIsSubmittingReview] = useState(false);

  useEffect(() => {
    if (!isOpen || !ruleId) return;
    async function loadDetail() {
      setIsLoading(true);
      setError(null);
      try {
        const detail = await getRegulatoryRuleDetail(ruleId);
        setRule(detail);
        setReviewStatus(detail.governance_review.review_status);
        setConfidenceLevel(detail.governance_review.confidence_level);
        setLegalNotes(detail.governance_review.legal_notes || "");
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Erreur lors du chargement de la règle");
      } finally {
        setIsLoading(false);
      }
    }
    loadDetail();
  }, [isOpen, ruleId]);

  async function handleSaveReview() {
    if (!rule) return;
    setIsSubmittingReview(true);
    try {
      const updated = await submitRuleReview(rule.rule_id, {
        review_status: reviewStatus,
        confidence_level: confidenceLevel,
        legal_notes: legalNotes.trim() || null,
      });
      setRule(updated);
      setIsReviewing(false);
      onUpdated?.();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Erreur lors de l'enregistrement de la revue");
    } finally {
      setIsSubmittingReview(false);
    }
  }

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4 overflow-y-auto">
      <div className="relative w-full max-w-4xl rounded-xl bg-white shadow-2xl border border-slate-200 dark:bg-slate-900 dark:border-slate-800 flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-start justify-between border-b border-slate-200 px-6 py-4 dark:border-slate-800">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs font-semibold px-2 py-0.5 rounded bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                {rule?.rule_id || ruleId}
              </span>
              {rule?.jurisdiction === "FR" && (
                <span className="rounded bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-700 border border-blue-200 dark:bg-blue-950/40 dark:border-blue-900 dark:text-blue-300">
                  🇫🇷 Droit Français
                </span>
              )}
              {rule?.jurisdiction === "EU" && (
                <span className="rounded bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-700 border border-indigo-200 dark:bg-indigo-950/40 dark:border-indigo-900 dark:text-indigo-300">
                  🇪🇺 Droit Européen
                </span>
              )}
              {rule?.jurisdiction === "INTERNATIONAL" && (
                <span className="rounded bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-700 border border-amber-200 dark:bg-amber-950/40 dark:border-amber-900 dark:text-amber-300">
                  🌐 Norme Internationale / ISO
                </span>
              )}
            </div>
            <h2 className="mt-1 text-lg font-bold text-slate-900 dark:text-white">
              {rule?.title || "Chargement..."}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6 text-xs">
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-red-700 dark:border-red-900 dark:bg-red-950/50 dark:text-red-300">
              {error}
            </div>
          )}

          {isLoading && (
            <div className="py-8 text-center text-slate-400">
              Chargement des données réglementaires...
            </div>
          )}

          {rule && (
            <>
              {/* Coverage Warning Banner */}
              {rule.incomplete_coverage_warning && (
                <div className="rounded-lg border border-amber-300 bg-amber-50/80 p-3.5 dark:border-amber-800 dark:bg-amber-950/30 text-amber-900 dark:text-amber-200">
                  <div className="flex items-start gap-2">
                    <span className="text-base">⚠️</span>
                    <div>
                      <strong className="font-semibold">Avertissement de transposition / portée :</strong>
                      <p className="mt-0.5">{rule.incomplete_coverage_warning}</p>
                    </div>
                  </div>
                </div>
              )}

              {/* Core Attributes */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-800/40">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase">Force Juridique</span>
                  <div className="font-bold text-slate-900 dark:text-white mt-1">{rule.legal_force}</div>
                </div>
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-800/40">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase">Statut Légal</span>
                  <div className="font-bold text-slate-900 dark:text-white mt-1 capitalize">{rule.legal_status.replace("_", " ")}</div>
                </div>
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-800/40">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase">Sévérité</span>
                  <div className={`font-bold mt-1 ${rule.severity === "CRITICAL" ? "text-red-600" : rule.severity === "HIGH" ? "text-amber-600" : "text-blue-600"}`}>
                    {rule.severity}
                  </div>
                </div>
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-800/40">
                  <span className="text-[10px] font-semibold text-slate-500 uppercase">Date d&apos;entrée en vigueur</span>
                  <div className="font-bold text-slate-900 dark:text-white mt-1">
                    {rule.effective_from ? new Date(rule.effective_from).toLocaleDateString("fr-FR") : "Non fixée"}
                  </div>
                </div>
              </div>

              {/* Scope & Applicability */}
              <div className="space-y-1">
                <h4 className="font-bold text-slate-800 dark:text-slate-200 uppercase text-[11px] tracking-wider">
                  Champ d&apos;application &amp; Périmètre
                </h4>
                <p className="text-slate-700 dark:text-slate-300 leading-relaxed bg-slate-50 p-3 rounded-lg border border-slate-200 dark:bg-slate-800/40 dark:border-slate-800">
                  {rule.scope}
                </p>
              </div>

              {/* Exact Official Legal Citations */}
              <div className="space-y-3">
                <h4 className="font-bold text-slate-800 dark:text-slate-200 uppercase text-[11px] tracking-wider flex items-center gap-1.5">
                  <span>📜</span> Citations Textuelles Officielles ({rule.official_citations.length})
                </h4>
                <div className="space-y-3">
                  {rule.official_citations.map((citation, idx) => (
                    <div
                      key={idx}
                      className="rounded-lg border border-indigo-100 bg-indigo-50/40 p-3.5 dark:border-indigo-900/50 dark:bg-indigo-950/20 space-y-1.5"
                    >
                      <div className="flex items-center justify-between">
                        <strong className="text-indigo-950 dark:text-indigo-300 font-semibold">
                          {citation.article}
                        </strong>
                        <a
                          href={citation.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-[11px] font-semibold text-indigo-600 hover:underline dark:text-indigo-400"
                        >
                          Source officielle ↗
                        </a>
                      </div>
                      <p className="text-slate-600 dark:text-slate-400 text-[11px]">
                        {citation.source_title}
                      </p>
                      <blockquote className="border-l-2 border-indigo-400 pl-3 italic text-slate-800 dark:text-slate-200 font-serif">
                        &ldquo;{citation.text_excerpt}&rdquo;
                      </blockquote>
                    </div>
                  ))}
                </div>
              </div>

              {/* Sanctions Profile */}
              {rule.sanction && (
                <div className="space-y-2">
                  <h4 className="font-bold text-slate-800 dark:text-slate-200 uppercase text-[11px] tracking-wider flex items-center gap-1.5">
                    <span>⚖️</span> Sanctions Administratives Encourues
                  </h4>
                  <div className="rounded-lg border border-red-200 bg-red-50/50 p-3.5 dark:border-red-900/50 dark:bg-red-950/20 space-y-1.5 text-slate-800 dark:text-slate-200">
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-semibold">
                      <span>Plafond Personne Morale : <strong className="text-red-700 dark:text-red-400 font-bold">{rule.sanction.max_legal_person_eur ? `${rule.sanction.max_legal_person_eur.toLocaleString("fr-FR")} €` : "Non fixé"}</strong></span>
                      <span>Plafond Personne Physique : <strong>{rule.sanction.max_natural_person_eur ? `${rule.sanction.max_natural_person_eur.toLocaleString("fr-FR")} €` : "Non fixé"}</strong></span>
                    </div>
                    <p className="text-[11px] text-slate-600 dark:text-slate-400">
                      <strong>Base légale :</strong> {rule.sanction.legal_basis}
                    </p>
                    {rule.sanction.notes && (
                      <p className="text-[11px] text-slate-500 italic">
                        Note : {rule.sanction.notes}
                      </p>
                    )}
                  </div>
                </div>
              )}

              {/* Safe Harbors & Exemptions */}
              {rule.safe_harbors.length > 0 && (
                <div className="space-y-2">
                  <h4 className="font-bold text-slate-800 dark:text-slate-200 uppercase text-[11px] tracking-wider flex items-center gap-1.5">
                    <span>🛡️</span> Régimes d&apos;Exemption &amp; Safe Harbors ({rule.safe_harbors.length})
                  </h4>
                  <div className="space-y-2">
                    {rule.safe_harbors.map((sh) => (
                      <div
                        key={sh.safe_harbor_id}
                        className="rounded-lg border border-emerald-200 bg-emerald-50/40 p-3 dark:border-emerald-900/50 dark:bg-emerald-950/20 space-y-1"
                      >
                        <div className="font-semibold text-emerald-950 dark:text-emerald-300">
                          {sh.title}
                        </div>
                        <ul className="list-disc list-inside text-[11px] text-slate-700 dark:text-slate-300 space-y-0.5">
                          {sh.conditions.map((cond, i) => (
                            <li key={i}>{cond}</li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Governance & Review Sign-off */}
              <div className="space-y-2 border-t border-slate-200 pt-4 dark:border-slate-800">
                <div className="flex items-center justify-between">
                  <h4 className="font-bold text-slate-800 dark:text-slate-200 uppercase text-[11px] tracking-wider flex items-center gap-1.5">
                    <span>✅</span> Revue Juridique &amp; Gouvernance
                  </h4>
                  {canManage && !isReviewing && (
                    <button
                      type="button"
                      onClick={() => setIsReviewing(true)}
                      className="text-xs font-semibold text-indigo-600 hover:underline dark:text-indigo-400"
                    >
                      ✏️ Mettre à jour la revue
                    </button>
                  )}
                </div>

                {!isReviewing ? (
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-800/40 space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-emerald-100 text-emerald-800 px-2 py-0.5 text-[10px] font-bold dark:bg-emerald-950/60 dark:text-emerald-300 uppercase">
                        {rule.governance_review.review_status}
                      </span>
                      <span className="text-slate-500 text-[11px]">
                        Niveau de confiance : <strong>{rule.governance_review.confidence_level}</strong>
                      </span>
                    </div>
                    {rule.governance_review.reviewed_by && (
                      <p className="text-[11px] text-slate-600 dark:text-slate-400">
                        Revu par : <strong>{rule.governance_review.reviewed_by}</strong>
                        {rule.governance_review.reviewed_at && ` le ${new Date(rule.governance_review.reviewed_at).toLocaleDateString("fr-FR")}`}
                      </p>
                    )}
                    {rule.governance_review.legal_notes && (
                      <p className="text-[11px] text-slate-700 dark:text-slate-300 italic">
                        &ldquo;{rule.governance_review.legal_notes}&rdquo;
                      </p>
                    )}
                  </div>
                ) : (
                  <div className="rounded-lg border border-indigo-200 bg-indigo-50/50 p-4 dark:border-indigo-900 dark:bg-indigo-950/30 space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-[10px] font-bold uppercase text-slate-600 dark:text-slate-300 mb-1">
                          Statut de la revue
                        </label>
                        <select
                          value={reviewStatus}
                          onChange={(e) => setReviewStatus(e.target.value as ReviewStatus)}
                          className="w-full rounded border border-slate-300 bg-white p-1.5 text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                        >
                          <option value="approved_legal">Validée par juriste (approved_legal)</option>
                          <option value="under_review">En cours de revue (under_review)</option>
                          <option value="draft">Brouillon (draft)</option>
                          <option value="deprecated">Obsolète (deprecated)</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-[10px] font-bold uppercase text-slate-600 dark:text-slate-300 mb-1">
                          Niveau de confiance
                        </label>
                        <select
                          value={confidenceLevel}
                          onChange={(e) => setConfidenceLevel(e.target.value as ConfidenceLevel)}
                          className="w-full rounded border border-slate-300 bg-white p-1.5 text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                        >
                          <option value="high">Élevé (Jurisprudence &amp; textes clairs)</option>
                          <option value="medium">Moyen (Transposition ou directive récente)</option>
                          <option value="low">Faible (Incertitude d&apos;interprétation)</option>
                        </select>
                      </div>
                    </div>
                    <div>
                      <label className="block text-[10px] font-bold uppercase text-slate-600 dark:text-slate-300 mb-1">
                        Notes d&apos;expertise juridique
                      </label>
                      <textarea
                        rows={2}
                        value={legalNotes}
                        onChange={(e) => setLegalNotes(e.target.value)}
                        placeholder="Préciser l'analyse ou les conditions particulières..."
                        className="w-full rounded border border-slate-300 bg-white p-2 text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                      />
                    </div>
                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        onClick={() => setIsReviewing(false)}
                        className="rounded px-3 py-1 text-xs text-slate-600 hover:bg-slate-200 dark:text-slate-400"
                      >
                        Annuler
                      </button>
                      <button
                        type="button"
                        disabled={isSubmittingReview}
                        onClick={handleSaveReview}
                        className="rounded bg-indigo-600 px-3 py-1 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                      >
                        {isSubmittingReview ? "Enregistrement..." : "Valider l'audit de la règle"}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-slate-200 bg-slate-50 px-6 py-3 dark:border-slate-800 dark:bg-slate-900/50 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-slate-200 px-4 py-2 text-xs font-semibold text-slate-800 hover:bg-slate-300 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
          >
            Fermer
          </button>
        </div>
      </div>
    </div>
  );
}
