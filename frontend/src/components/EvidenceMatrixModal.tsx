"use client";

import { useEffect, useState } from "react";
import {
  getAnalysisEvidenceMatrix,
  linkClaimEvidence,
  listPersistentEvidence,
  unlinkClaimEvidence,
} from "@/lib/api";
import type {
  EvidenceMatrix,
  PersistentEvidence,
  PersistentEvidenceStatus,
} from "@/lib/types";

type Props = {
  analysisId: string;
  versionNumber?: number;
  canManage: boolean;
  onClose: () => void;
};

// C17 — vocabulaire du **constat** de couverture (ce que les faits disent), distinct du
// statut déclaré par un relecteur affiché juste à côté.
const OBSERVED_STATE_LABELS: Record<string, string> = {
  covered: "couvre l'allégation",
  partial: "portée non établie",
  expired: "périmée à la date de l'audit",
  out_of_scope: "hors du périmètre de l'audit",
  not_covering: "famille d'allégation non couverte",
  missing: "aucune pièce",
};

const COVERAGE_STATUS_BADGES: Record<
  PersistentEvidenceStatus,
  { label: string; badge: string; icon: string }
> = {
  present: {
    label: "Justifiée / Preuve valide",
    badge: "bg-emerald-950/80 text-emerald-300 border-emerald-700",
    icon: "🟢",
  },
  verified: {
    label: "Vérifiée par tiers",
    badge: "bg-blue-950/80 text-blue-300 border-blue-700",
    icon: "🛡️",
  },
  partial: {
    label: "Preuve partielle / À compléter",
    badge: "bg-amber-950/80 text-amber-300 border-amber-700",
    icon: "🟡",
  },
  expired: {
    label: "Preuve expirée",
    badge: "bg-rose-950/80 text-rose-300 border-rose-700",
    icon: "🔴",
  },
  out_of_scope: {
    label: "Preuve hors périmètre",
    badge: "bg-purple-950/80 text-purple-300 border-purple-700",
    icon: "🟣",
  },
  missing: {
    label: "Preuve absente / Non fournie",
    badge: "bg-red-950/80 text-red-300 border-red-700",
    icon: "🔴",
  },
  pending: {
    label: "En attente d'évaluation",
    badge: "bg-slate-800 text-slate-300 border-slate-700",
    icon: "⚪",
  },
  rejected: {
    label: "Preuve rejetée",
    badge: "bg-rose-950 text-rose-400 border-rose-800",
    icon: "❌",
  },
};

export default function EvidenceMatrixModal({
  analysisId,
  versionNumber,
  canManage,
  onClose,
}: Props) {
  const [matrix, setMatrix] = useState<EvidenceMatrix | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [availableEvidence, setAvailableEvidence] = useState<PersistentEvidence[]>([]);
  const [linkingClaimId, setLinkingClaimId] = useState<string | null>(null);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string>("");
  const [isLinking, setIsLinking] = useState(false);

  const loadMatrix = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getAnalysisEvidenceMatrix(analysisId, versionNumber);
      setMatrix(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Erreur lors du chargement de la matrice");
    } finally {
      setLoading(false);
    }
  };

  const loadAvailableEvidence = async () => {
    try {
      const res = await listPersistentEvidence({ limit: 100 });
      setAvailableEvidence(res.items);
    } catch {
      // non-blocking
    }
  };

  useEffect(() => {
    loadMatrix();
    loadAvailableEvidence();
  }, [analysisId, versionNumber]);

  const handleLinkSubmit = async (claimId: string) => {
    if (!selectedEvidenceId) return;
    setIsLinking(true);
    try {
      await linkClaimEvidence(claimId, {
        evidence_id: selectedEvidenceId,
        relation: "supports",
      });
      setLinkingClaimId(null);
      setSelectedEvidenceId("");
      await loadMatrix();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Échec de liaison de la preuve");
    } finally {
      setIsLinking(false);
    }
  };

  const handleUnlink = async (linkId: string) => {
    if (!confirm("Délier cette preuve de l'allégation ?")) return;
    try {
      await unlinkClaimEvidence(linkId);
      await loadMatrix();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Échec de déliaison");
    }
  };

  return (
    <div className="fixed inset-0 bg-black/85 backdrop-blur-md flex items-center justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl max-w-5xl w-full p-6 shadow-2xl my-8 flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex justify-between items-start pb-4 border-b border-slate-800">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xl">📊</span>
              <h2 className="text-xl font-bold text-white">Matrice Allégation ↔ Preuve Réglementaire</h2>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              Dossier d'analyse v{matrix?.version_number ?? versionNumber ?? 1} · Rapprochement et traçabilité des pièces probatoires.
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white p-1 rounded-lg bg-slate-800 text-lg"
          >
            ✕
          </button>
        </div>

        {/* Content body */}
        <div className="overflow-y-auto py-4 space-y-6 flex-1">
          {error && (
            <div className="p-3 bg-rose-950/60 border border-rose-800 text-rose-300 rounded-lg text-xs">
              ⚠️ {error}
            </div>
          )}

          {loading ? (
            <div className="py-16 text-center text-slate-400">
              <div className="animate-spin w-8 h-8 border-4 border-emerald-500 border-t-transparent rounded-full mx-auto mb-3"></div>
              Calcul de la couverture probatoire en cours...
            </div>
          ) : !matrix || matrix.matrix_rows.length === 0 ? (
            <div className="py-12 text-center text-slate-400 bg-slate-950/50 rounded-xl border border-slate-800">
              Aucune allégation détectée dans cette version d'analyse.
            </div>
          ) : (
            <>
              {/* KPI Summary Banner */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="bg-slate-950 p-3 rounded-xl border border-slate-800 text-center">
                  <div className="text-xs font-semibold text-slate-400">Total allégations</div>
                  <div className="text-2xl font-bold text-white mt-0.5">{matrix.total_claims}</div>
                </div>
                <div className="bg-emerald-950/40 p-3 rounded-xl border border-emerald-800 text-center">
                  <div className="text-xs font-semibold text-emerald-300">Justifiées (🟢)</div>
                  <div className="text-2xl font-bold text-emerald-400 mt-0.5">{matrix.claims_with_evidence}</div>
                </div>
                <div className="bg-rose-950/40 p-3 rounded-xl border border-rose-800 text-center">
                  <div className="text-xs font-semibold text-rose-300">Preuves manquantes (🔴)</div>
                  <div className="text-2xl font-bold text-rose-400 mt-0.5">{matrix.claims_missing_evidence}</div>
                </div>
                <div className="bg-amber-950/40 p-3 rounded-xl border border-amber-800 text-center">
                  <div className="text-xs font-semibold text-amber-300">Expirées / Incomplètes</div>
                  <div className="text-2xl font-bold text-amber-400 mt-0.5">
                    {matrix.claims_expired_evidence + matrix.claims_out_of_scope_evidence}
                  </div>
                </div>
              </div>

              {/* Matrix Rows */}
              <div className="space-y-4">
                <h3 className="text-sm font-semibold text-slate-300 uppercase tracking-wider">
                  Détail par allégation détectée
                </h3>

                {matrix.matrix_rows.map((row, idx) => {
                  const statusConfig = COVERAGE_STATUS_BADGES[row.coverage_status] || COVERAGE_STATUS_BADGES.pending;
                  const isLinkingThis = linkingClaimId === row.claim.id;

                  return (
                    <div
                      key={row.claim.id || idx}
                      className="bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-3"
                    >
                      {/* Top claim info */}
                      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-2">
                        <div className="flex items-center gap-2">
                          <span className="text-xs px-2 py-0.5 rounded bg-slate-800 font-mono text-emerald-400 border border-slate-700">
                            {row.claim.category.toUpperCase()}
                          </span>
                          <span className="text-xs text-slate-500">
                            Page {row.claim.citation.page_number ?? 1} · {row.claim.claim_type}
                          </span>
                        </div>
                        <span className={`text-xs px-2.5 py-1 rounded-full border font-medium ${statusConfig.badge}`}>
                          {statusConfig.icon} {statusConfig.label}
                        </span>
                      </div>

                      {/* Claim Text */}
                      <div className="p-3 bg-slate-900 rounded-lg border border-slate-800/80 text-sm text-slate-200 font-medium">
                        « {row.claim.claim_text} »
                      </div>

                      {/* Explanation box */}
                      <div className="text-xs text-slate-400 bg-slate-900/60 p-2.5 rounded border border-slate-800/60">
                        <span className="text-slate-300 font-semibold">Pourquoi cette conclusion ? </span>
                        {row.explanation}
                      </div>

                      {/* Linked Evidences */}
                      <div className="space-y-2 pt-1">
                        <div className="text-xs font-semibold text-slate-400 flex justify-between items-center">
                          <span>Justificatifs probatoires associés ({row.evidence_links.length}) :</span>
                          {canManage && !isLinkingThis && (
                            <button
                              onClick={() => {
                                setLinkingClaimId(row.claim.id);
                                setSelectedEvidenceId("");
                              }}
                              className="text-emerald-400 hover:text-emerald-300 transition text-xs flex items-center gap-1"
                            >
                              <span>+</span> Lier une preuve
                            </button>
                          )}
                        </div>

                        {/* Inline linking selector */}
                        {isLinkingThis && (
                          <div className="p-3 bg-slate-900 rounded-lg border border-emerald-800/60 space-y-2">
                            <label className="block text-xs font-medium text-emerald-300">
                              Sélectionner une pièce du registre :
                            </label>
                            <div className="flex gap-2">
                              <select
                                value={selectedEvidenceId}
                                onChange={(e) => setSelectedEvidenceId(e.target.value)}
                                className="flex-1 bg-slate-800 border border-slate-700 text-slate-200 text-xs rounded-lg p-2"
                              >
                                <option value="">-- Choisir une preuve enregistrée --</option>
                                {availableEvidence.map((ev) => (
                                  <option key={ev.id} value={ev.id}>
                                    [{ev.evidence_type}] {ev.reference || ev.issuer || "Sans réf"} {ev.expires_on ? `(exp: ${ev.expires_on})` : ""}
                                  </option>
                                ))}
                              </select>
                              <button
                                onClick={() => handleLinkSubmit(row.claim.id)}
                                disabled={!selectedEvidenceId || isLinking}
                                className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-xs font-medium disabled:opacity-50"
                              >
                                {isLinking ? "Liaison..." : "Valider"}
                              </button>
                              <button
                                onClick={() => setLinkingClaimId(null)}
                                className="px-2 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs"
                              >
                                Annuler
                              </button>
                            </div>
                          </div>
                        )}

                        {row.evidence_links.length === 0 ? (
                          <div className="text-xs text-rose-400 italic py-1">
                            ⚠️ Aucun justificatif fourni. Risque réglementaire élevé sans preuve traçable.
                          </div>
                        ) : (
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                            {row.evidence_links.map((lnk) => {
                              const ev = lnk.evidence;
                              const lnkStatus = COVERAGE_STATUS_BADGES[lnk.coverage_status] || COVERAGE_STATUS_BADGES.pending;

                              return (
                                <div
                                  key={lnk.id}
                                  className="p-2.5 bg-slate-900 border border-slate-800 rounded-lg text-xs flex justify-between items-start gap-2"
                                >
                                  <div>
                                    <div className="font-semibold text-slate-200">
                                      {ev?.reference || "Justificatif sans référence"}
                                    </div>
                                    <div className="text-slate-400">
                                      {ev?.issuer ? `Émis par ${ev.issuer}` : "Auto-déclaré"}
                                      {ev?.expires_on ? ` · Valide jusqu'au ${ev.expires_on}` : ""}
                                    </div>
                                    <div className="mt-1 flex flex-wrap items-center gap-1">
                                      <span className={`inline-block px-2 py-0.5 rounded text-[10px] border ${lnkStatus.badge}`}>
                                        {lnkStatus.label}
                                      </span>
                                      {lnk.observed_state && (
                                        <span className="inline-block px-2 py-0.5 rounded text-[10px] border border-slate-700 bg-slate-950 text-slate-300">
                                          Constat : {OBSERVED_STATE_LABELS[lnk.observed_state] ?? lnk.observed_state}
                                        </span>
                                      )}
                                    </div>
                                  </div>
                                  {canManage && (
                                    <button
                                      onClick={() => handleUnlink(lnk.id)}
                                      className="text-slate-500 hover:text-rose-400 text-sm p-1"
                                      title="Délier"
                                    >
                                      ✕
                                    </button>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>

        {/* Footer Disclaimer */}
        <div className="pt-4 border-t border-slate-800 flex flex-col md:flex-row justify-between items-center gap-3 text-xs text-slate-500">
          <p className="max-w-2xl text-[11px] leading-relaxed">
            {matrix?.disclaimer ||
              "VeriClaim fournit une aide au pré-audit. Les résultats ne constituent pas un avis juridique et doivent être validés par une personne compétente."}
          </p>
          <button
            onClick={onClose}
            className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg font-medium text-xs whitespace-nowrap"
          >
            Fermer la matrice
          </button>
        </div>
      </div>
    </div>
  );
}
