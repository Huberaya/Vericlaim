"use client";

import { useEffect, useState } from "react";
import { violationSeverity, type ClaimEvaluation } from "@/lib/types";

type Props = {
  evaluations: ClaimEvaluation[];
  onClose: () => void;
};

const VERDICT_LABEL: Record<ClaimEvaluation["verdict"], string> = {
  STRICTLY_PROHIBITED: "Interdiction stricte",
  NON_COMPLIANT: "Non conforme",
  CONDITIONAL_REJECT: "Publication conditionnelle",
  REVIEW_REQUIRED: "Revue requise",
  COMPLIANT: "Conforme au périmètre",
  NOT_APPLICABLE: "Hors champ automatique",
  UPCOMING: "Exigence à venir",
  ADVISORY_ONLY: "Contrôle interne",
};

const LEGAL_FORCE_LABEL: Record<ClaimEvaluation["legal_force"], string> = {
  BINDING_FR: "Droit français contraignant",
  EU_DIRECTIVE_DATE_GATED: "Directive UE · date / transposition à vérifier",
  PROPOSAL_ONLY: "Proposition · non contraignante",
  VOLUNTARY_STANDARD: "Norme volontaire",
  INTERNAL_EVIDENCE_CONTROL: "Seuil probatoire interne",
};

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);

  async function copy() {
    setCopyError(false);
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopyError(true);
    }
  }

  return (
    <div className="copy-control">
      <button type="button" className="button button-secondary copy-button" onClick={() => void copy()}>
        <span aria-hidden="true">{copied ? "✓" : "▣"}</span>{copied ? "Copié" : label}
      </button>
      {copyError && <span className="copy-error" role="status">Copie indisponible dans ce navigateur — sélectionnez le texte manuellement.</span>}
    </div>
  );
}

export default function RemediationModal({ evaluations, onClose }: Props) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  if (evaluations.length === 0) return null;
  const claimText = evaluations[0].claim_text;

  return (
    <div className="modal-backdrop remediation-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="remediation-modal" role="dialog" aria-modal="true" aria-labelledby="remediation-title">
        <header className="remediation-header">
          <div>
            <div className="modal-eyebrow">FICHE D’ANALYSE · {evaluations.length} règle{evaluations.length > 1 ? "s" : ""}</div>
            <h2 id="remediation-title">Allégation et remédiation</h2>
          </div>
          <button type="button" className="modal-close-button" onClick={onClose} aria-label="Fermer la fiche">×</button>
        </header>
        <div className="remediation-scroll">
          <blockquote className="remediation-claim">« {claimText} »</blockquote>
          {evaluations.map((item) => {
            const severity = violationSeverity(item).toLowerCase();
            return (
              <article className="remediation-rule" key={`${item.claim_id}-${item.rule_id}`}>
                <div className="remediation-rule-topline">
                  <div>
                    <span className={`severity-pill severity-${severity}`}>{VERDICT_LABEL[item.verdict]}</span>
                    <h3>{item.rule_title}</h3>
                  </div>
                  <code>{item.rule_id}</code>
                </div>
                <div className="legal-force-line">{LEGAL_FORCE_LABEL[item.legal_force]}</div>
                <div className="law-box">
                  <div className="law-box-label">FONDEMENT / ARTICLE</div>
                  <p>{item.law_reference}</p>
                  {item.source_urls.length > 0 && (
                    <div className="source-links">
                      {item.source_urls.map((url) => (
                        <a key={url} href={url} target="_blank" rel="noreferrer">Source officielle ↗</a>
                      ))}
                    </div>
                  )}
                </div>

                <div className="remediation-copy-block">
                  <div className="modal-section-label">CE QUE CELA SIGNIFIE</div>
                  <p>{item.remediation.buyer_explanation}</p>
                </div>

                <div className="remediation-copy-block rewrite-block">
                  <div className="modal-section-label">RÉÉCRITURE RECOMMANDÉE</div>
                  <p>{item.remediation.recommended_rewrite}</p>
                </div>

                <div className="contract-block">
                  <div className="contract-heading">
                    <div>
                      <div className="modal-section-label">CLAUSE FOURNISSEUR · PRÊTE À COPIER</div>
                      <p>Texte modèle à adapter et faire valider par vos équipes juridiques.</p>
                    </div>
                    <CopyButton text={item.remediation.supplier_contract_clause} label="Copier la clause" />
                  </div>
                  <div className="contract-text">{item.remediation.supplier_contract_clause}</div>
                </div>

                {item.evidence_checks.length > 0 && (
                  <div className="evidence-review-block">
                    <div className="modal-section-label">CONTRÔLES DE PREUVE</div>
                    {item.evidence_checks.map((check) => (
                      <div className="evidence-review-row" key={check.check_name}>
                        <strong>{check.check_name.replaceAll("_", " ")}</strong>
                        <span>{check.status.replaceAll("_", " ")}</span>
                        <p>{check.detail}</p>
                        {check.missing_fields.length > 0 && <small>À compléter : {check.missing_fields.join(" · ")}</small>}
                      </div>
                    ))}
                  </div>
                )}

                {item.legal_caveat && <div className="legal-caveat">⚠ {item.legal_caveat}</div>}
                {item.remediation.required_actions.length > 0 && (
                  <details className="reasoning-details">
                    <summary>Étapes d’inférence et actions recommandées</summary>
                    <ol>
                      {item.reasoning_steps.map((step) => (
                        <li key={`${step.step}-${step.code}`}><strong>{step.code.replaceAll("_", " ")}</strong> — {step.finding}<small>{step.legal_significance}</small></li>
                      ))}
                    </ol>
                    <ul>{item.remediation.required_actions.map((action) => <li key={action}>{action}</li>)}</ul>
                  </details>
                )}
              </article>
            );
          })}
          <p className="modal-legal-disclaimer">Ce rapport est une aide à la conformité fondée sur le texte, les preuves déclarées et le Rule Book indiqué. Il ne constitue ni un avis juridique, ni une certification, ni un constat de l’administration.</p>
        </div>
      </section>
    </div>
  );
}
