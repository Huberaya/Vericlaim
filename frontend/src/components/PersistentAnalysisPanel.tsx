"use client";

import { useEffect, useState } from "react";
import EvidenceMatrixModal from "@/components/EvidenceMatrixModal";
import { HumanReviewModal } from "@/components/HumanReviewModal";
import {
  createIdempotencyKey,
  createPersistentAnalysis,
  getPersistentAnalysisVersion,
  retryPersistentAnalysis,
} from "@/lib/api";
import type {
  PersistentAnalysisDetail,
  PersistentAnalysisEnqueueResponse,
  PersistentAnalysisStatus,
  PersistentAnalysisVersionDetail,
  StoredDocumentVersion,
} from "@/lib/types";

type Props = {
  version: StoredDocumentVersion;
  canRun: boolean;
  supplierId?: string | null;
  productId?: string | null;
};

const STATUS_COPY: Record<PersistentAnalysisStatus, { label: string; detail: string; tone: string }> = {
  draft: { label: "Préparation", detail: "La version d’analyse est en préparation.", tone: "pending" },
  queued: {
    label: "Détection mise en file",
    detail: "Le worker distinct analysera uniquement les segments extraits et persistés.",
    tone: "pending",
  },
  extracting: { label: "Préparation des sources", detail: "Les sources sont vérifiées avant détection.", tone: "pending" },
  detecting_claims: {
    label: "Détection déterministe en cours",
    detail: "Le worker recherche les motifs lexicaux citables sans qualification juridique.",
    tone: "running",
  },
  checking_evidence: { label: "Vérification des preuves", detail: "Rapprochement probatoire en cours.", tone: "running" },
  scoring: { label: "Évaluation du risque", detail: "Calcul des indices de risque explicables.", tone: "running" },
  completed: {
    label: "Allégations & Preuves persistées",
    detail: "Les passages détectés, preuves associées et matrice de couverture sont consultables.",
    tone: "completed",
  },
  failed: {
    label: "Détection en échec",
    detail: "La version d’analyse est conservée. Une relance créera une nouvelle version sans écraser l’historique.",
    tone: "failed",
  },
  cancelled: { label: "Analyse annulée", detail: "Cette version n’a pas produit de nouveau résultat.", tone: "failed" },
};

function shortHash(value: string | null): string {
  return value ? `${value.slice(0, 16)}…` : "en attente";
}

export default function PersistentAnalysisPanel({ version, canRun, supplierId = null, productId = null }: Props) {
  const [queued, setQueued] = useState<PersistentAnalysisEnqueueResponse | null>(null);
  const [detail, setDetail] = useState<PersistentAnalysisVersionDetail | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [isMatrixOpen, setIsMatrixOpen] = useState(false);
  const [isReviewOpen, setIsReviewOpen] = useState(false);

  const currentAnalysis = detail?.analysis ?? queued?.analysis ?? null;
  const currentVersion = detail?.version ?? queued?.version ?? null;
  const currentJob = detail?.detection_job ?? queued?.detection_job ?? null;
  const isPending = currentVersion?.status === "queued" || currentVersion?.status === "detecting_claims";
  const status = currentVersion ? STATUS_COPY[currentVersion.status] : null;

  useEffect(() => {
    setQueued(null);
    setDetail(null);
    setError(null);
    setRefreshError(null);
    setIsStarting(false);
    setIsRetrying(false);
    setIsMatrixOpen(false);
    setIsReviewOpen(false);
  }, [version.id]);

  useEffect(() => {
    if (!currentAnalysis || !currentVersion || !isPending) return undefined;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const refresh = async () => {
      try {
        const fresh = await getPersistentAnalysisVersion(currentAnalysis.id, currentVersion.version_number);
        if (cancelled) return;
        setDetail(fresh);
        setRefreshError(null);
        if (fresh.version.status === "queued" || fresh.version.status === "detecting_claims") {
          timer = setTimeout(() => void refresh(), 2_500);
        }
      } catch (cause) {
        if (cancelled) return;
        setRefreshError(cause instanceof Error ? cause.message : "Le statut d’analyse n’a pas pu être actualisé.");
        timer = setTimeout(() => void refresh(), 5_000);
      }
    };

    timer = setTimeout(() => void refresh(), 750);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [currentAnalysis?.id, currentVersion?.version_number, isPending]);

  async function start() {
    if (!canRun || isStarting) return;
    setIsStarting(true);
    setError(null);
    setRefreshError(null);
    try {
      const result = await createPersistentAnalysis(version.id, createIdempotencyKey(), { supplierId, productId });
      setQueued(result);
      setDetail(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "La détection persistante n’a pas pu être mise en file.");
    } finally {
      setIsStarting(false);
    }
  }

  async function retry() {
    if (!currentAnalysis || isRetrying || !canRun) return;
    setIsRetrying(true);
    setError(null);
    setRefreshError(null);
    try {
      const result = await retryPersistentAnalysis(currentAnalysis.id, createIdempotencyKey("analysis-retry"));
      setQueued(result);
      setDetail(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "La nouvelle version d’analyse n’a pas pu être mise en file.");
    } finally {
      setIsRetrying(false);
    }
  }

  return (
    <section className="persistent-analysis" aria-label="Analyse persistante des allégations">
      <div className="persistent-analysis-heading">
        <div>
          <span className="persistent-analysis-eyebrow">03 · ANALYSE PERSISTANTE & PREUVES</span>
          <strong>Détecter les allégations et qualifier les preuves</strong>
          <p>Réutilise les segments extraits de cette version et permet le rapprochement avec le registre probatoire.</p>
        </div>
        {!currentVersion && canRun && (
          <button type="button" className="button button-primary persistent-analysis-action" disabled={isStarting} onClick={() => void start()}>
            {isStarting ? "Mise en file…" : "Détecter les allégations"}
          </button>
        )}
      </div>

      {!canRun && (
        <p className="persistent-analysis-access-note">Votre rôle peut consulter les documents, mais ne peut pas lancer une analyse persistante.</p>
      )}
      {error && <p className="inline-error" role="alert">{error}</p>}

      {currentVersion && status && (
        <div className={`persistent-analysis-status persistent-analysis-${status.tone}`} role="status">
          <div>
            <strong>{status.label}</strong>
            <p>{status.detail}</p>
            {currentJob && <small>Job {currentJob.status} · tentative {currentJob.attempt_count}/{currentJob.max_attempts}</small>}
            {refreshError && <small className="persistent-analysis-error" role="alert">{refreshError}</small>}
          </div>
          <span>V{currentVersion.version_number}</span>
        </div>
      )}

      {detail?.version.status === "completed" && (
        <>
          <div className="persistent-analysis-manifest">
            <span><small>ALLÉGATIONS</small><strong>{detail.claims.length}</strong></span>
            <span><small>MANIFESTE D’ENTRÉE</small><strong title={detail.version.input_manifest_sha256}>{shortHash(detail.version.input_manifest_sha256)}</strong></span>
            <span><small>RÉSULTAT SHA-256</small><strong title={detail.version.result_sha256 || undefined}>{shortHash(detail.version.result_sha256)}</strong></span>
          </div>

          <div className="my-3 flex flex-wrap justify-between items-center gap-2 p-3 bg-slate-900 border border-slate-800 rounded-lg">
            <span className="text-xs text-slate-300 font-medium">Contrôles Probatoires &amp; Arbitrage :</span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setIsMatrixOpen(true)}
                className="px-3 py-1.5 bg-emerald-700 hover:bg-emerald-600 text-white rounded text-xs font-semibold flex items-center gap-1.5 shadow transition"
              >
                <span>📊</span> Matrice Allégation ↔ Preuve
              </button>
              <button
                type="button"
                onClick={() => setIsReviewOpen(true)}
                className="px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 text-white rounded text-xs font-semibold flex items-center gap-1.5 shadow transition"
              >
                <span>⚖️</span> Arbitrage &amp; Validations (Chantier 6.3)
              </button>
            </div>
          </div>

          <div className="persistent-analysis-claims">
            {detail.claims.length === 0 ? (
              <p className="persistent-analysis-empty"><strong>Aucun motif du lexique n’a été détecté.</strong> Cette absence ne garantit pas l’absence d’allégation dans le document.</p>
            ) : detail.claims.map((claim) => (
              <article className="persistent-claim" key={claim.id}>
                <div className="persistent-claim-tags">
                  <span>{claim.claim_type.replaceAll("_", " ")}</span>
                  {claim.status === "review_required" && <span className="persistent-claim-review">OCR · revue requise</span>}
                </div>
                <blockquote>« {claim.claim_text} »</blockquote>
                <p>
                  Page {claim.citation.page_number ?? "non précisée"} · segment {claim.citation.document_segment_id?.slice(0, 8) ?? "indisponible"} · offsets {claim.start_offset ?? "?"}–{claim.end_offset ?? "?"}
                </p>
              </article>
            ))}
          </div>
        </>
      )}

      {currentVersion?.status === "failed" && canRun && (
        <button type="button" className="button button-secondary persistent-analysis-retry" disabled={isRetrying} onClick={() => void retry()}>
          {isRetrying ? "Nouvelle version en file…" : "Créer une nouvelle version"}
        </button>
      )}

      {/* Evidence Matrix Modal */}
      {isMatrixOpen && currentAnalysis && currentVersion && (
        <EvidenceMatrixModal
          analysisId={currentAnalysis.id}
          versionNumber={currentVersion.version_number}
          canManage={canRun}
          onClose={() => setIsMatrixOpen(false)}
        />
      )}

      {/* Human Review & Validations Modal */}
      {isReviewOpen && currentAnalysis && currentVersion && (
        <HumanReviewModal
          analysisId={currentAnalysis.id}
          versionNumber={currentVersion.version_number}
          claims={detail?.claims || []}
          isOpen={isReviewOpen}
          onClose={() => setIsReviewOpen(false)}
        />
      )}

      <p className="persistent-analysis-disclaimer">Détection déterministe et qualification probatoire : traçabilité des pièces sans avis d'autorité ni décision juridique automatique.</p>
    </section>
  );
}

