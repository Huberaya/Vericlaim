"use client";

import { useEffect, useState } from "react";
import {
  fetchMonitoredTargets,
  createMonitoredTarget,
  deleteMonitoredTarget,
  runMonitoredTargetCheck,
  fetchMonitoredTargetHistory,
  runBatchWatcherChecks,
  fetchAuditById,
} from "@/lib/api";
import type {
  MonitoredTargetResponse,
  MonitoringLogResponse,
  RegulatoryAuditResponse,
} from "@/lib/types";

interface ComplianceWatcherViewProps {
  onLoadAuditReport?: (report: RegulatoryAuditResponse) => void;
}

export default function ComplianceWatcherView({ onLoadAuditReport }: ComplianceWatcherViewProps) {
  const [targets, setTargets] = useState<MonitoredTargetResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Form state
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [frequencyHours, setFrequencyHours] = useState(24);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Scanning state
  const [activeScanningTargetId, setActiveScanningTargetId] = useState<string | null>(null);
  const [isBatchScanning, setIsBatchScanning] = useState(false);

  // History modal state
  const [selectedTarget, setSelectedTarget] = useState<MonitoredTargetResponse | null>(null);
  const [historyLogs, setHistoryLogs] = useState<MonitoringLogResponse[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);

  const loadTargets = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await fetchMonitoredTargets();
      setTargets(res.targets);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur de chargement des cibles");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTargets();
  }, []);

  const handleAddTarget = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !url.trim()) return;

    try {
      setIsSubmitting(true);
      setError(null);
      await createMonitoredTarget({
        name: name.trim(),
        url: url.trim(),
        frequency_hours: frequencyHours,
      });
      setName("");
      setUrl("");
      setFrequencyHours(24);
      setSuccessMessage("Cible de surveillance e-commerce ajoutée avec succès.");
      await loadTargets();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur lors de l'ajout de la cible");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteTarget = async (targetId: string, targetName: string) => {
    if (!confirm(`Confirmer la suppression de la surveillance pour "${targetName}" ?`)) return;
    try {
      setError(null);
      await deleteMonitoredTarget(targetId);
      setSuccessMessage(`Surveillance de "${targetName}" supprimée.`);
      setTargets((prev) => prev.filter((t) => t.id !== targetId));
      if (selectedTarget?.id === targetId) {
        setSelectedTarget(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Échec de suppression");
    }
  };

  const handleRunSingleScan = async (target: MonitoredTargetResponse) => {
    try {
      setActiveScanningTargetId(target.id);
      setError(null);
      const res = await runMonitoredTargetCheck(target.id);
      const log = res.log as MonitoringLogResponse;
      setSuccessMessage(
        `Audit terminé pour ${target.name} : ${log.delta_status === "REGRESSION" ? "⚠️ RÉGRESSION DÉTECTÉE !" : "Statut : " + log.overall_compliance}`
      );
      await loadTargets();
      if (selectedTarget?.id === target.id) {
        await handleOpenHistory(target);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Échec du scan");
    } finally {
      setActiveScanningTargetId(null);
    }
  };

  const handleRunBatch = async () => {
    try {
      setIsBatchScanning(true);
      setError(null);
      const res = await runBatchWatcherChecks();
      setSuccessMessage(
        `Batch terminé : ${res.processed} cibles traitées, ${res.regressions_detected} régression(s) détectée(s).`
      );
      await loadTargets();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Échec de l'exécution batch");
    } finally {
      setIsBatchScanning(false);
    }
  };

  const handleOpenHistory = async (target: MonitoredTargetResponse) => {
    setSelectedTarget(target);
    setIsLoadingHistory(true);
    try {
      const res = await fetchMonitoredTargetHistory(target.id);
      setHistoryLogs(res.logs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur de chargement de l'historique");
    } finally {
      setIsLoadingHistory(false);
    }
  };

  const handleViewAudit = async (auditId: string) => {
    if (!onLoadAuditReport) return;
    try {
      const audit = await fetchAuditById(auditId);
      onLoadAuditReport(audit);
    } catch (err) {
      alert("Impossible de charger l'audit : " + (err instanceof Error ? err.message : String(err)));
    }
  };

  const getStatusBadge = (status: string, regression: boolean) => {
    if (regression) {
      return (
        <span style={{ background: "#fee2e2", color: "#991b1b", padding: "4px 8px", borderRadius: 6, fontSize: "0.75rem", fontWeight: 700 }}>
          ⚠️ RÉGRESSION
        </span>
      );
    }
    switch (status) {
      case "COMPLIANT":
        return (
          <span style={{ background: "#dcfce7", color: "#166534", padding: "4px 8px", borderRadius: 6, fontSize: "0.75rem", fontWeight: 600 }}>
            ✓ Conforme
          </span>
        );
      case "NON_COMPLIANT":
        return (
          <span style={{ background: "#fee2e2", color: "#991b1b", padding: "4px 8px", borderRadius: 6, fontSize: "0.75rem", fontWeight: 600 }}>
            ✕ Non-conforme
          </span>
        );
      case "CONDITIONAL":
        return (
          <span style={{ background: "#fef3c7", color: "#92400e", padding: "4px 8px", borderRadius: 6, fontSize: "0.75rem", fontWeight: 600 }}>
            ⚠ Sous conditions
          </span>
        );
      case "NO_CLAIMS_DETECTED":
        return (
          <span style={{ background: "#f1f5f9", color: "#475569", padding: "4px 8px", borderRadius: 6, fontSize: "0.75rem", fontWeight: 600 }}>
            ○ Aucune allégation
          </span>
        );
      default:
        return (
          <span style={{ background: "#f8fafc", color: "#64748b", padding: "4px 8px", borderRadius: 6, fontSize: "0.75rem", fontWeight: 600 }}>
            En attente
          </span>
        );
    }
  };

  const getDeltaBadge = (delta: string) => {
    switch (delta) {
      case "REGRESSION":
        return (
          <span style={{ background: "#ef4444", color: "#fff", padding: "2px 6px", borderRadius: 4, fontSize: "0.7rem", fontWeight: 700 }}>
            ⚠️ RÉGRESSION
          </span>
        );
      case "RESOLVED":
        return (
          <span style={{ background: "#10b981", color: "#fff", padding: "2px 6px", borderRadius: 4, fontSize: "0.7rem", fontWeight: 600 }}>
            ✓ RÉSOLU
          </span>
        );
      case "UNCHANGED":
        return (
          <span style={{ background: "#e2e8f0", color: "#475569", padding: "2px 6px", borderRadius: 4, fontSize: "0.7rem", fontWeight: 500 }}>
            INCHANGÉ
          </span>
        );
      default:
        return (
          <span style={{ background: "#dbeafe", color: "#1e40af", padding: "2px 6px", borderRadius: 4, fontSize: "0.7rem", fontWeight: 500 }}>
            INITIAL
          </span>
        );
    }
  };

  return (
    <div className="compliance-watcher-view space-y-6" style={{ maxWidth: 1100, margin: "0 auto", padding: "1.5rem 1rem" }}>
      {/* Hero card */}
      <div className="panel" style={{ background: "var(--card-bg, #ffffff)", border: "1px solid var(--border-color, #e2e8f0)", borderRadius: 12, padding: "1.75rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "1rem" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.5rem" }}>
              <span style={{ fontSize: "1.5rem" }}>📡</span>
              <h2 style={{ fontSize: "1.35rem", fontWeight: 700, margin: 0, color: "var(--heading-color, #0f172a)" }}>
                Compliance Watcher · Surveillance E-Commerce Continue
              </h2>
            </div>
            <p style={{ margin: "0 0 1rem", color: "#64748b", fontSize: "0.9rem", maxWidth: 750 }}>
              Surveillez en continu vos fiches produits e-commerce et pages marchandes.
              L&apos;agent extrait le contenu HTML, audite les nouvelles allégations écologiques
              selon le cadre AGEC / EmpCo et déclenche des alertes automatiques en cas de régression de conformité.
            </p>
          </div>
          <button
            type="button"
            onClick={handleRunBatch}
            disabled={isBatchScanning || targets.length === 0}
            style={{
              padding: "0.65rem 1.15rem",
              background: "#047857",
              color: "#ffffff",
              border: 0,
              borderRadius: 8,
              fontWeight: 600,
              fontSize: "0.88rem",
              cursor: isBatchScanning || targets.length === 0 ? "not-allowed" : "pointer",
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
            }}
          >
            <span>{isBatchScanning ? "⏳" : "⚡"}</span>
            <span>{isBatchScanning ? "Scan de la flotte en cours..." : "Scanner toutes les cibles échues"}</span>
          </button>
        </div>

        {error && (
          <div style={{ padding: "0.75rem 1rem", background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 8, color: "#991b1b", fontSize: "0.85rem", marginTop: "1rem" }}>
            {error}
          </div>
        )}
        {successMessage && (
          <div style={{ padding: "0.75rem 1rem", background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 8, color: "#166534", fontSize: "0.85rem", marginTop: "1rem" }}>
            {successMessage}
          </div>
        )}
      </div>

      {/* Add new target form */}
      <div className="panel" style={{ background: "var(--card-bg, #ffffff)", border: "1px solid var(--border-color, #e2e8f0)", borderRadius: 12, padding: "1.5rem" }}>
        <h3 style={{ fontSize: "1.1rem", fontWeight: 600, margin: "0 0 1rem", color: "#0f172a" }}>
          ➕ Ajouter une URL de produit ou boutique à surveiller
        </h3>
        <form onSubmit={handleAddTarget} style={{ display: "grid", gridTemplateColumns: "2fr 3fr 1fr auto", gap: "0.75rem", alignItems: "end" }}>
          <div>
            <label style={{ display: "block", fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: 4 }}>
              Nom du produit / Cible
            </label>
            <input
              type="text"
              required
              placeholder="Ex: Shampoing Solide Bio 250g"
              value={name}
              onChange={(e) => setName(e.target.value)}
              style={{ width: "100%", padding: "0.55rem 0.75rem", borderRadius: 6, border: "1px solid #cbd5e1", fontSize: "0.88rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: 4 }}>
              URL de la page marchande
            </label>
            <input
              type="url"
              required
              placeholder="https://boutique.example.fr/produits/shampoing"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              style={{ width: "100%", padding: "0.55rem 0.75rem", borderRadius: 6, border: "1px solid #cbd5e1", fontSize: "0.88rem" }}
            />
          </div>
          <div>
            <label style={{ display: "block", fontSize: "0.8rem", fontWeight: 600, color: "#475569", marginBottom: 4 }}>
              Fréquence
            </label>
            <select
              value={frequencyHours}
              onChange={(e) => setFrequencyHours(Number(e.target.value))}
              style={{ width: "100%", padding: "0.55rem 0.75rem", borderRadius: 6, border: "1px solid #cbd5e1", fontSize: "0.88rem", background: "#fff" }}
            >
              <option value={1}>Toutes les 1h</option>
              <option value={6}>Toutes les 6h</option>
              <option value={12}>Toutes les 12h</option>
              <option value={24}>Toutes les 24h</option>
              <option value={48}>Tous les 2 jours</option>
              <option value={168}>Toutes les semaines</option>
            </select>
          </div>
          <div>
            <button
              type="submit"
              disabled={isSubmitting}
              style={{
                padding: "0.55rem 1.25rem",
                background: "#0f172a",
                color: "#ffffff",
                border: 0,
                borderRadius: 6,
                fontWeight: 600,
                fontSize: "0.88rem",
                cursor: isSubmitting ? "not-allowed" : "pointer",
                height: 38,
              }}
            >
              {isSubmitting ? "Ajout..." : "Activer la veille"}
            </button>
          </div>
        </form>
      </div>

      {/* Monitored targets table */}
      <div className="panel" style={{ background: "var(--card-bg, #ffffff)", border: "1px solid var(--border-color, #e2e8f0)", borderRadius: 12, padding: "1.5rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
          <h3 style={{ fontSize: "1.1rem", fontWeight: 600, margin: 0, color: "#0f172a" }}>
            Cibles sous surveillance active ({targets.length})
          </h3>
          <button
            type="button"
            onClick={loadTargets}
            style={{ background: "none", border: 0, color: "#3b82f6", fontSize: "0.85rem", cursor: "pointer", fontWeight: 500 }}
          >
            ↻ Actualiser
          </button>
        </div>

        {loading ? (
          <div style={{ textAlign: "center", padding: "2rem", color: "#64748b" }}>Chargement des cibles surveillées...</div>
        ) : targets.length === 0 ? (
          <div style={{ textAlign: "center", padding: "2.5rem 1rem", border: "1px dashed #cbd5e1", borderRadius: 8 }}>
            <p style={{ margin: "0 0 0.5rem", color: "#475569", fontWeight: 600 }}>Aucune cible surveillée pour le moment.</p>
            <p style={{ margin: 0, color: "#94a3b8", fontSize: "0.85rem" }}>
              Ajoutez l&apos;URL d&apos;une fiche produit ci-dessus pour lancer la surveillance continue contre le greenwashing.
            </p>
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem", textAlign: "left" }}>
              <thead>
                <tr style={{ borderBottom: "2px solid #e2e8f0", color: "#475569", fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                  <th style={{ padding: "0.6rem 0.75rem" }}>Cible & URL</th>
                  <th style={{ padding: "0.6rem 0.75rem" }}>Statut</th>
                  <th style={{ padding: "0.6rem 0.75rem" }}>Score Risque</th>
                  <th style={{ padding: "0.6rem 0.75rem" }}>Fréquence</th>
                  <th style={{ padding: "0.6rem 0.75rem" }}>Dernier scan</th>
                  <th style={{ padding: "0.6rem 0.75rem", textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {targets.map((target) => (
                  <tr
                    key={target.id}
                    style={{
                      borderBottom: "1px solid #f1f5f9",
                      background: target.regression_detected ? "#fff1f2" : "transparent",
                    }}
                  >
                    <td style={{ padding: "0.85rem 0.75rem" }}>
                      <div style={{ fontWeight: 600, color: "#0f172a" }}>{target.name}</div>
                      <a
                        href={target.url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ fontSize: "0.78rem", color: "#3b82f6", textDecoration: "none", wordBreak: "break-all" }}
                      >
                        {target.url} ↗
                      </a>
                    </td>
                    <td style={{ padding: "0.85rem 0.75rem" }}>
                      {getStatusBadge(target.last_status, target.regression_detected)}
                      {target.last_violations_count !== null && target.last_violations_count > 0 && (
                        <div style={{ fontSize: "0.72rem", color: "#ef4444", marginTop: 4, fontWeight: 500 }}>
                          {target.last_violations_count} infraction(s)
                        </div>
                      )}
                    </td>
                    <td style={{ padding: "0.85rem 0.75rem" }}>
                      {target.last_risk_score !== null ? (
                        <span
                          style={{
                            fontWeight: 700,
                            color: target.last_risk_score > 50 ? "#dc2626" : target.last_risk_score > 20 ? "#d97706" : "#16a34a",
                          }}
                        >
                          {target.last_risk_score} / 100
                        </span>
                      ) : (
                        <span style={{ color: "#94a3b8" }}>-</span>
                      )}
                    </td>
                    <td style={{ padding: "0.85rem 0.75rem", color: "#64748b", fontSize: "0.82rem" }}>
                      {target.frequency_hours}h
                    </td>
                    <td style={{ padding: "0.85rem 0.75rem", fontSize: "0.82rem", color: "#64748b" }}>
                      {target.last_checked_at_utc ? new Date(target.last_checked_at_utc).toLocaleString("fr-FR") : "Jamais"}
                    </td>
                    <td style={{ padding: "0.85rem 0.75rem", textAlign: "right" }}>
                      <div style={{ display: "flex", gap: "0.4rem", justifyContent: "flex-end" }}>
                        <button
                          type="button"
                          onClick={() => handleRunSingleScan(target)}
                          disabled={activeScanningTargetId === target.id}
                          style={{
                            padding: "4px 10px",
                            background: "#e0f2fe",
                            color: "#0369a1",
                            border: 0,
                            borderRadius: 4,
                            fontSize: "0.78rem",
                            fontWeight: 600,
                            cursor: activeScanningTargetId === target.id ? "not-allowed" : "pointer",
                          }}
                        >
                          {activeScanningTargetId === target.id ? "Scan..." : "Scanner"}
                        </button>
                        <button
                          type="button"
                          onClick={() => handleOpenHistory(target)}
                          style={{
                            padding: "4px 10px",
                            background: "#f1f5f9",
                            color: "#334155",
                            border: 0,
                            borderRadius: 4,
                            fontSize: "0.78rem",
                            fontWeight: 600,
                            cursor: "pointer",
                          }}
                        >
                          Historique
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeleteTarget(target.id, target.name)}
                          style={{
                            padding: "4px 8px",
                            background: "#fee2e2",
                            color: "#b91c1c",
                            border: 0,
                            borderRadius: 4,
                            fontSize: "0.78rem",
                            cursor: "pointer",
                          }}
                        >
                          ✕
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* History modal */}
      {selectedTarget && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(15, 23, 42, 0.6)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 9999,
            padding: "1rem",
          }}
        >
          <div
            style={{
              background: "#ffffff",
              borderRadius: 12,
              maxWidth: 800,
              width: "100%",
              maxHeight: "85vh",
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.2)",
            }}
          >
            <div style={{ padding: "1.25rem 1.5rem", borderBottom: "1px solid #e2e8f0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <h3 style={{ margin: 0, fontSize: "1.15rem", fontWeight: 700, color: "#0f172a" }}>
                  Historique de surveillance : {selectedTarget.name}
                </h3>
                <div style={{ fontSize: "0.8rem", color: "#64748b", marginTop: 2 }}>{selectedTarget.url}</div>
              </div>
              <button
                type="button"
                onClick={() => setSelectedTarget(null)}
                style={{ background: "none", border: 0, fontSize: "1.35rem", cursor: "pointer", color: "#64748b" }}
              >
                ✕
              </button>
            </div>

            <div style={{ padding: "1.25rem 1.5rem", overflowY: "auto", flex: 1 }}>
              {isLoadingHistory ? (
                <div style={{ textAlign: "center", padding: "2rem", color: "#64748b" }}>Chargement des scans...</div>
              ) : historyLogs.length === 0 ? (
                <div style={{ textAlign: "center", padding: "2rem", color: "#64748b" }}>Aucun scan enregistré pour cette cible.</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                  {historyLogs.map((log) => (
                    <div
                      key={log.id}
                      style={{
                        padding: "1rem",
                        borderRadius: 8,
                        border: log.delta_status === "REGRESSION" ? "2px solid #ef4444" : "1px solid #e2e8f0",
                        background: log.delta_status === "REGRESSION" ? "#fef2f2" : "#f8fafc",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.5rem" }}>
                        <div>
                          <span style={{ fontSize: "0.8rem", color: "#64748b", fontWeight: 500 }}>
                            {new Date(log.executed_at_utc).toLocaleString("fr-FR")}
                          </span>
                          <span style={{ marginLeft: "0.6rem" }}>{getDeltaBadge(log.delta_status)}</span>
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                          <span style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                            Risque : {log.risk_score}/100
                          </span>
                          {getStatusBadge(log.overall_compliance, false)}
                        </div>
                      </div>

                      <div style={{ fontSize: "0.85rem", color: "#334155", margin: "0.5rem 0" }}>
                        <strong>{log.violations_count}</strong> infraction(s) réglementaire(s) détectée(s).
                      </div>

                      {log.detected_claims && log.detected_claims.length > 0 && (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.35rem", marginTop: "0.5rem" }}>
                          {log.detected_claims.map((claim, idx) => (
                            <span
                              key={idx}
                              style={{
                                background: "#ffffff",
                                border: "1px solid #cbd5e1",
                                padding: "2px 8px",
                                borderRadius: 4,
                                fontSize: "0.75rem",
                                color: "#1e293b",
                              }}
                            >
                              « {claim} »
                            </span>
                          ))}
                        </div>
                      )}

                      {onLoadAuditReport && (
                        <div style={{ marginTop: "0.75rem", textAlign: "right" }}>
                          <button
                            type="button"
                            onClick={() => {
                              setSelectedTarget(null);
                              handleViewAudit(log.audit_id);
                            }}
                            style={{
                              background: "none",
                              border: 0,
                              color: "#2563eb",
                              fontSize: "0.8rem",
                              fontWeight: 600,
                              cursor: "pointer",
                              textDecoration: "underline",
                            }}
                          >
                            Consulter l&apos;audit complet #{log.audit_id.slice(0, 8)} ↗
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div style={{ padding: "0.85rem 1.5rem", borderTop: "1px solid #e2e8f0", background: "#f8fafc", textAlign: "right" }}>
              <button
                type="button"
                onClick={() => setSelectedTarget(null)}
                style={{
                  padding: "0.5rem 1rem",
                  background: "#0f172a",
                  color: "#fff",
                  border: 0,
                  borderRadius: 6,
                  fontWeight: 600,
                  fontSize: "0.85rem",
                  cursor: "pointer",
                }}
              >
                Fermer
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
