"use client";

import { useState } from "react";
import { verifyAuditPublic } from "@/lib/api";
import type { AuditVerificationResponse } from "@/lib/types";

export default function AuditVerificationView() {
  const [auditIdInput, setAuditIdInput] = useState("");
  const [result, setResult] = useState<AuditVerificationResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    const id = auditIdInput.trim();
    if (!id) return;
    setIsLoading(true);
    setError(null);
    setResult(null);
    try {
      const data = await verifyAuditPublic(id);
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur de communication avec le portail de vérification");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="verification-view space-y-6" style={{ maxWidth: 860, margin: "0 auto", padding: "1.5rem 1rem" }}>
      <div className="panel" style={{ background: "var(--card-bg, #ffffff)", border: "1px solid var(--border-color, #e2e8f0)", borderRadius: 12, padding: "1.75rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.5rem" }}>
          <span style={{ fontSize: "1.5rem" }}>🛡️</span>
          <h2 style={{ fontSize: "1.35rem", fontWeight: 700, margin: 0, color: "var(--heading-color, #0f172a)" }}>
            Portail Public de Vérification & Opposabilité
          </h2>
        </div>
        <p style={{ margin: "0 0 1.25rem", color: "#64748b", fontSize: "0.9rem" }}>
          Vérifiez instantanément l&apos;authenticité, le sceau d&apos;intégrité SHA-256 et la traçabilité immuable
          d&apos;une attestation d&apos;audit VeriClaim AI remise par un fournisseur ou un tiers.
        </p>

        <form onSubmit={handleVerify} style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          <input
            type="text"
            required
            placeholder="Exemple : 4b1a8d05-728b-4b2a-a9e1-628d68d7f52a"
            value={auditIdInput}
            onChange={(e) => setAuditIdInput(e.target.value)}
            style={{
              flex: 1,
              minWidth: 280,
              padding: "0.65rem 0.9rem",
              borderRadius: 8,
              border: "1px solid #cbd5e1",
              fontSize: "0.92rem",
              fontFamily: "monospace",
            }}
          />
          <button
            type="submit"
            disabled={isLoading || !auditIdInput.trim()}
            style={{
              padding: "0.65rem 1.25rem",
              background: "#0f172a",
              color: "#ffffff",
              border: 0,
              borderRadius: 8,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            {isLoading ? "Vérification cryptographique..." : "Vérifier le Sceau"}
          </button>
        </form>

        {error && (
          <div style={{ marginTop: "1rem", padding: "0.75rem 1rem", background: "#fef2f2", border: "1px solid #fca5a5", borderRadius: 8, color: "#991b1b", fontSize: "0.9rem" }}>
            ⚠️ {error}
          </div>
        )}

        {result && (
          <div
            style={{
              marginTop: "1.5rem",
              padding: "1.25rem",
              borderRadius: 10,
              background: result.is_valid ? "#f0fdf4" : result.status === "NOT_FOUND" ? "#f8fafc" : "#fef2f2",
              border: `1.5px solid ${result.is_valid ? "#86efac" : result.status === "NOT_FOUND" ? "#cbd5e1" : "#f87171"}`,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.5rem" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <span style={{ fontSize: "1.4rem" }}>
                  {result.is_valid ? "✅" : result.status === "NOT_FOUND" ? "❓" : "🚨"}
                </span>
                <strong style={{ fontSize: "1.05rem", color: result.is_valid ? "#166534" : result.status === "NOT_FOUND" ? "#475569" : "#991b1b" }}>
                  {result.is_valid
                    ? "ATTESTATION AUTHENTIQUE & SCEAU VALIDÉ"
                    : result.status === "NOT_FOUND"
                    ? "AUDIT NON RÉPERTORIÉ"
                    : "ALERTE DE RUPTURE D'INTÉGRITÉ"}
                </strong>
              </div>
              <span
                style={{
                  padding: "0.2rem 0.6rem",
                  borderRadius: 20,
                  fontSize: "0.75rem",
                  fontWeight: 700,
                  background: result.is_valid ? "#dcfce7" : "#fee2e2",
                  color: result.is_valid ? "#15803d" : "#b91c1c",
                }}
              >
                {result.status}
              </span>
            </div>

            <p style={{ margin: "0.75rem 0 1rem", fontSize: "0.88rem", color: "#334155" }}>
              {result.message}
            </p>

            {result.created_at_utc && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "0.75rem", fontSize: "0.82rem", background: "#ffffff", padding: "1rem", borderRadius: 8, border: "1px solid #e2e8f0" }}>
                <div>
                  <span style={{ color: "#64748b", display: "block" }}>Horodatage Certifié UTC :</span>
                  <strong>{new Date(result.created_at_utc).toLocaleString("fr-FR")}</strong>
                </div>

                <div>
                  <span style={{ color: "#64748b", display: "block" }}>Statut Réglementaire d&apos;Origine :</span>
                  <strong style={{ color: result.overall_compliance === "COMPLIANT" ? "#16a34a" : "#dc2626" }}>
                    {result.overall_compliance || "Non communiqué"}
                  </strong>
                </div>

                <div>
                  <span style={{ color: "#64748b", display: "block" }}>Infractions Légales Retenues :</span>
                  <strong>{result.violations_count ?? 0} infraction(s)</strong>
                </div>

                <div>
                  <span style={{ color: "#64748b", display: "block" }}>Score de Risque Attribué :</span>
                  <strong>{result.risk_score ?? 0} / 100</strong>
                </div>

                <div style={{ gridColumn: "1 / -1" }}>
                  <span style={{ color: "#64748b", display: "block" }}>Empreinte SHA-256 du Rapport d&apos;Audit :</span>
                  <code style={{ fontSize: "0.75rem", color: "#0284c7" }}>{result.report_sha256}</code>
                </div>

                <div style={{ gridColumn: "1 / -1" }}>
                  <span style={{ color: "#64748b", display: "block" }}>Hash du Bloc d&apos;Intégrité (Audit Ledger) :</span>
                  <code style={{ fontSize: "0.75rem", color: "#0f172a" }}>{result.record_hash}</code>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
