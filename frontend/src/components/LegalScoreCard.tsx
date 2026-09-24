import type { ApiDecimal, RegulatoryAuditResponse } from "@/lib/types";

const COMPLIANCE_LABEL: Record<RegulatoryAuditResponse["overall_compliance"], string> = {
  COMPLIANT: "CONFORME",
  NON_COMPLIANT: "RISQUE ÉLEVÉ DE GREENWASHING",
  CONDITIONAL_REJECT: "PREUVE REQUISE",
  REVIEW_REQUIRED: "PREUVE REQUISE",
  UPCOMING_REQUIREMENTS: "PREUVE REQUISE",
  NO_CLAIMS_DETECTED: "AUCUNE ALLÉGATION DÉTECTÉE",
};

function amountAsNumber(value: ApiDecimal | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function euro(value: ApiDecimal | null | undefined): string {
  const parsed = amountAsNumber(value);
  if (parsed === null) return "Non chiffré";
  return new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(parsed);
}

function riskTone(score: number): "low" | "medium" | "high" {
  if (score >= 70) return "high";
  if (score >= 40) return "medium";
  return "low";
}

type Props = {
  report: RegulatoryAuditResponse | null;
  onDownloadPdf?: () => void;
  isDownloadingPdf?: boolean;
};

export default function LegalScoreCard({ report, onDownloadPdf, isDownloadingPdf }: Props) {
  if (!report) {
    return (
      <section className="surface-card score-card score-card-empty" aria-labelledby="score-title">
        <div className="card-heading score-heading">
          <div>
            <div className="section-eyebrow"><span className="step-chip">02</span> Synthèse de conformité</div>
            <h2 id="score-title" className="card-title">Score juridique</h2>
          </div>
          <span className="status-pill status-neutral">En attente</span>
        </div>
        <div className="empty-score-state">
          <div className="empty-shield" aria-hidden="true">⌁</div>
          <strong>Votre rapport apparaîtra ici</strong>
          <p>Les règles applicables, le niveau de preuve et l’exposition financière sont calculés après l’analyse du texte.</p>
        </div>
        <div className="empty-score-foot"><span>RÈGLES VERSIONNÉES</span><span>FR · UE</span></div>
      </section>
    );
  }

  const tone = riskTone(report.risk_score);
  const uniqueCritical = new Set(
    report.evaluations
      .filter((item) => item.is_legal_violation && item.severity === "CRITICAL")
      .map((item) => `${item.claim_id}:${item.rule_id}`),
  ).size;
  const uniqueNeedsReview = new Set(
    report.evaluations
      .filter((item) => ["CONDITIONAL_REJECT", "REVIEW_REQUIRED", "UPCOMING"].includes(item.verdict))
      .map((item) => item.claim_id),
  ).size;
  const maxFine = report.exposure_matrix.max_known_fixed_fine_eur;
  const maxFineNumber = amountAsNumber(maxFine);
  const maxFineSubject = report.exposure_matrix.max_known_fixed_fine_for;
  const maximumFineItem = report.exposure_matrix.items.find((item) => {
    const applicable = maxFineSubject === "personne physique"
      ? item.max_natural_person_eur
      : item.max_legal_person_eur;
    return amountAsNumber(applicable) === maxFineNumber && maxFineNumber !== null;
  });
  const complianceLabel = COMPLIANCE_LABEL[report.overall_compliance];

  return (
    <section className="surface-card score-card" aria-labelledby="score-title">
      <div className="card-heading score-heading">
        <div>
          <div className="section-eyebrow"><span className="step-chip">02</span> Synthèse de conformité</div>
          <h2 id="score-title" className="card-title">Résultat de l’audit</h2>
        </div>
        <span className={`status-pill status-${report.overall_compliance.toLowerCase()}`}>
          <span className="status-dot" aria-hidden="true" />{complianceLabel}
        </span>
      </div>

      <div className="score-gauge-row">
        <div className={`score-number score-${tone}`}>
          <strong>{report.risk_score}</strong><span>/100</span>
        </div>
        <div className="score-explanation">
          <div className="score-explanation-title">Indice de priorité de revue</div>
          <p>Ce score est un indicateur déterministe de revue, pas une probabilité d’infraction ni une décision administrative.</p>
        </div>
      </div>
      <div
        className={`risk-meter risk-meter-${tone}`}
        role="meter"
        aria-label="Indice de risque"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={report.risk_score}
      >
        <span style={{ width: `${Math.max(0, Math.min(100, report.risk_score))}%` }} />
      </div>
      <div className="risk-meter-labels"><span>FAIBLE</span><span>À SURVEILLER</span><span>ÉLEVÉ</span></div>

      <div className="score-stat-grid">
        <div className="score-stat score-stat-critical">
          <span className="stat-icon" aria-hidden="true">!</span>
          <span><strong>{uniqueCritical}</strong><small>INFRACTION(S) CRITIQUE(S) RETENUE(S)</small></span>
        </div>
        <div className="score-stat score-stat-warning">
          <span className="stat-icon" aria-hidden="true">⌕</span>
          <span><strong>{uniqueNeedsReview}</strong><small>ALLÉGATION(S) À CORRIGER / VÉRIFIER</small></span>
        </div>
      </div>

      <div className="fine-box">
        <div className="fine-box-top">
          <div>
            <div className="fine-eyebrow">PLAFOND FIXE IDENTIFIÉ</div>
            <div className="fine-amount">{maxFineNumber === null ? "Non chiffré" : `Jusqu’à ${euro(maxFine)}`}</div>
          </div>
          <div className="fine-icon" aria-hidden="true">§</div>
        </div>
        {maximumFineItem ? (
          <>
            <div className="fine-basis">{maximumFineItem.legal_basis ?? maximumFineItem.title}</div>
            <p className="fine-note">{maximumFineItem.note}</p>
          </>
        ) : (
          <p className="fine-note">Aucun plafond fixe n’est calculé pour les règles évaluées. Les risques généraux et civils peuvent rester non chiffrés.</p>
        )}
        <div className="fine-disclaimer">Plafond conditionnel : ni amende automatique, ni montant cumulatif. Vérifiez le fondement affiché.</div>
      </div>

      {onDownloadPdf && (
        <div style={{ marginTop: "14px", marginBottom: "6px" }}>
          <button
            type="button"
            className="button button-primary"
            style={{ width: "100%", padding: "10px 14px", fontSize: "11px", display: "flex", gap: "8px", alignItems: "center", justifyContent: "center" }}
            onClick={onDownloadPdf}
            disabled={isDownloadingPdf}
          >
            <span>{isDownloadingPdf ? "⏳" : "📥"}</span>
            <span>{isDownloadingPdf ? "Génération de l'attestation..." : "Télécharger l'Attestation d'Audit (PDF)"}</span>
          </button>
        </div>
      )}

      <div className="score-footer">
        <span>{report.detected_claims_count} allégation(s) détectée(s)</span>
        <span>{report.violations_count} constat(s) juridique(s) retenu(s)</span>
      </div>
    </section>
  );
}
