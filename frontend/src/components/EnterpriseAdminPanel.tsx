"use client";

import { useEffect, useState } from "react";
import {
  createLegalHold,
  getEnterpriseAlerts,
  getEnterpriseMetrics,
  listApiKeys,
  listLegalHolds,
  revokeApiKey,
} from "@/lib/api";
import type {
  ApiKeySummary,
  EnterpriseAlert,
  EnterpriseMetricsResponse,
  LegalHoldResponse,
} from "@/lib/types";
import { ApiKeyCreateModal } from "./ApiKeyCreateModal";

interface EnterpriseAdminPanelProps {
  canManage?: boolean;
}

export function EnterpriseAdminPanel({ canManage = false }: EnterpriseAdminPanelProps) {
  const [activeTab, setActiveTab] = useState<"apikeys" | "metrics" | "ediscovery" | "sso">("apikeys");

  // API keys
  const [apiKeys, setApiKeys] = useState<ApiKeySummary[]>([]);
  const [isKeyModalOpen, setIsKeyModalOpen] = useState(false);

  // Metrics
  const [metrics, setMetrics] = useState<EnterpriseMetricsResponse | null>(null);
  const [alerts, setAlerts] = useState<EnterpriseAlert[]>([]);

  // Legal Holds
  const [legalHolds, setLegalHolds] = useState<LegalHoldResponse[]>([]);
  const [caseRef, setCaseRef] = useState("");
  const [caseReason, setCaseReason] = useState("");
  const [isCreatingHold, setIsCreatingHold] = useState(false);

  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadData() {
    setIsLoading(true);
    setError(null);
    try {
      const [keysRes, metricsRes, alertsRes, holdsRes] = await Promise.all([
        listApiKeys(),
        getEnterpriseMetrics(),
        getEnterpriseAlerts(),
        listLegalHolds(),
      ]);
      setApiKeys(keysRes);
      setMetrics(metricsRes);
      setAlerts(alertsRes);
      setLegalHolds(holdsRes);
    } catch (err: unknown) {
      console.error("Failed to load enterprise data:", err);
      setError(err instanceof Error ? err.message : "Erreur de chargement des paramètres entreprise");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    loadData();
  }, []);

  async function handleRevokeKey(keyId: string) {
    if (!confirm("Êtes-vous sûr de vouloir révoquer cette clé d'API ? Les requêtes avec cette clé seront immédiatement rejetées.")) return;
    try {
      await revokeApiKey(keyId);
      await loadData();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Erreur de révocation");
    }
  }

  async function handleCreateHold() {
    if (!caseRef.trim() || !caseReason.trim()) {
      alert("Veuillez renseigner la référence du dossier et le motif légal.");
      return;
    }
    setIsCreatingHold(true);
    try {
      await createLegalHold({
        case_reference: caseRef.trim(),
        reason: caseReason.trim(),
      });
      setCaseRef("");
      setCaseReason("");
      await loadData();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Erreur lors de la création du gel légal");
    } finally {
      setIsCreatingHold(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
            <span className="text-indigo-600 dark:text-indigo-400">🏢</span>
            Administration Entreprise &amp; Gouvernance (Chantier 9)
          </h2>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Gestion des clés d&apos;API partenaires, observabilité temps réel, E-Discovery et synchronisation SSO / SCIM 2.0.
          </p>
        </div>

        {activeTab === "apikeys" && canManage && (
          <button
            onClick={() => setIsKeyModalOpen(true)}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700 transition"
          >
            <span>+</span> Nouvelle Clé d&apos;API Partenaire
          </button>
        )}
      </div>

      {/* Tabs */}
      <div className="flex border-b border-slate-200 dark:border-slate-800 text-xs font-semibold gap-4">
        <button
          onClick={() => setActiveTab("apikeys")}
          className={`pb-2.5 transition flex items-center gap-1.5 ${
            activeTab === "apikeys"
              ? "border-b-2 border-indigo-600 text-indigo-600 dark:text-indigo-400"
              : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
          }`}
        >
          <span>🔑</span> Clés d&apos;API &amp; Quotas ({apiKeys.length})
        </button>

        <button
          onClick={() => setActiveTab("metrics")}
          className={`pb-2.5 transition flex items-center gap-1.5 ${
            activeTab === "metrics"
              ? "border-b-2 border-indigo-600 text-indigo-600 dark:text-indigo-400"
              : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
          }`}
        >
          <span>📊</span> Observabilité &amp; Santé Système
        </button>

        <button
          onClick={() => setActiveTab("ediscovery")}
          className={`pb-2.5 transition flex items-center gap-1.5 ${
            activeTab === "ediscovery"
              ? "border-b-2 border-indigo-600 text-indigo-600 dark:text-indigo-400"
              : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
          }`}
        >
          <span>⚖️</span> E-Discovery &amp; Legal Holds ({legalHolds.length})
        </button>

        <button
          onClick={() => setActiveTab("sso")}
          className={`pb-2.5 transition flex items-center gap-1.5 ${
            activeTab === "sso"
              ? "border-b-2 border-indigo-600 text-indigo-600 dark:text-indigo-400"
              : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
          }`}
        >
          <span>🔒</span> SSO &amp; SCIM 2.0
        </button>
      </div>

      {/* Tab 1: API Keys */}
      {activeTab === "apikeys" && (
        <div className="space-y-4">
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-400">
                <tr>
                  <th className="py-3 px-4 font-semibold">Nom de l&apos;intégration</th>
                  <th className="py-3 px-4 font-semibold">Préfixe / Identifiant</th>
                  <th className="py-3 px-4 font-semibold">Permissions (Scopes)</th>
                  <th className="py-3 px-4 font-semibold">Quota Débit</th>
                  <th className="py-3 px-4 font-semibold">Statut</th>
                  <th className="py-3 px-4 font-semibold text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                {apiKeys.map((key) => (
                  <tr key={key.id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/40">
                    <td className="py-3 px-4 font-semibold text-slate-900 dark:text-white">
                      {key.name}
                    </td>
                    <td className="py-3 px-4 font-mono text-[11px] text-slate-600 dark:text-slate-400">
                      {key.prefix}…
                    </td>
                    <td className="py-3 px-4">
                      <div className="flex flex-wrap gap-1">
                        {key.scopes.map((s, idx) => (
                          <span
                            key={idx}
                            className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-mono text-slate-700 dark:bg-slate-800 dark:text-slate-300"
                          >
                            {s}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="py-3 px-4 text-slate-700 dark:text-slate-300">
                      {key.rate_limit_per_minute} req/min
                    </td>
                    <td className="py-3 px-4 whitespace-nowrap">
                      {key.is_active ? (
                        <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
                          Active
                        </span>
                      ) : (
                        <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-bold text-rose-800 dark:bg-rose-950 dark:text-rose-300">
                          Révoquée
                        </span>
                      )}
                    </td>
                    <td className="py-3 px-4 text-right whitespace-nowrap">
                      {key.is_active && canManage && (
                        <button
                          onClick={() => handleRevokeKey(key.id)}
                          className="rounded px-2.5 py-1 text-xs font-semibold text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/40"
                        >
                          Révoquer
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {apiKeys.length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-6 text-center text-slate-400">
                      Aucune clé d&apos;API active. Créez votre première clé pour connecter vos ERP ou PIM.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Tab 2: Metrics & Observability */}
      {activeTab === "metrics" && metrics && (
        <div className="space-y-4">
          {/* What the instance can affirm, and why. The status word is never shown
              without its justification: that was the defect this chantier fixed. */}
          <div
            className={
              "rounded-xl border p-3 text-xs " +
              (metrics.service_status === "healthy"
                ? "border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/40"
                : metrics.service_status === "degraded"
                  ? "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/40"
                  : "border-rose-200 bg-rose-50 dark:border-rose-900 dark:bg-rose-950/40")
            }
          >
            <div className="flex items-center gap-2">
              <span className="font-bold text-slate-900 dark:text-white uppercase tracking-wide">
                État du service : {metrics.service_status}
              </span>
              <span className="text-[10px] text-slate-500">
                base {metrics.database_status} · stockage {metrics.storage_status} · workers {metrics.workers_status}
              </span>
            </div>
            {metrics.status_reasons.length > 0 && (
              <ul className="mt-1 list-disc list-inside text-slate-600 dark:text-slate-300">
                {metrics.status_reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900 shadow-sm">
              <span className="text-[10px] font-semibold text-slate-500 uppercase">Processus en marche depuis</span>
              <div className="text-xl font-bold text-slate-900 dark:text-white mt-1">
                {Math.floor(metrics.uptime_seconds / 3600)}h {Math.floor((metrics.uptime_seconds % 3600) / 60)}m
              </div>
              {/* The previous badge announced "● 99,98% SLA" — a figure measured
                  nowhere, on a product that publishes no availability commitment.
                  Uptime of this process is what is actually known. */}
              <span className="text-[10px] text-slate-400">Mesure locale, remise à zéro au redémarrage</span>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900 shadow-sm">
              <span className="text-[10px] font-semibold text-slate-500 uppercase">Requêtes HTTP traitées</span>
              <div className="text-xl font-bold text-slate-900 dark:text-white mt-1">
                {metrics.total_api_requests.toLocaleString("fr-FR")}
              </div>
              <span className="text-[10px] text-slate-500">
                Erreurs 5xx : {metrics.error_rate_percent}% · Latence moyenne des requêtes :{" "}
                {metrics.average_analysis_latency_ms === null
                  ? "non mesurée"
                  : `${metrics.average_analysis_latency_ms} ms`}
              </span>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900 shadow-sm">
              <span className="text-[10px] font-semibold text-slate-500 uppercase">Empreinte du processus API</span>
              <div className="text-xl font-bold text-slate-900 dark:text-white mt-1">
                {metrics.memory_usage_mb === null ? "non mesuré" : `${metrics.memory_usage_mb} MB`}
              </div>
              <span className="text-[10px] text-slate-500">
                CPU : {metrics.cpu_utilization_percent === null ? "non mesuré" : `${metrics.cpu_utilization_percent}%`}
              </span>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900 shadow-sm">
              <span className="text-[10px] font-semibold text-slate-500 uppercase">Activité de votre organisation</span>
              <div className="text-xl font-bold text-slate-900 dark:text-white mt-1">
                {metrics.total_analyses_completed} analyses
              </div>
              <span className="text-[10px] text-slate-500">
                {metrics.tenant_audit_events} événements d&apos;audit · {metrics.open_alerts_count} alerte(s) ouverte(s)
              </span>
            </div>
          </div>

          {metrics.not_measured.length > 0 && (
            <p className="text-[10px] text-slate-500 dark:text-slate-400">
              Non mesuré par cette instance : {metrics.not_measured.join(", ")}.
            </p>
          )}
          <p className="text-[10px] text-slate-400 dark:text-slate-500">{metrics.metrics_note}</p>

          {/* System Alerts */}
          <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900 shadow-sm space-y-3">
            <h3 className="font-bold text-slate-900 dark:text-white text-xs uppercase tracking-wider flex items-center gap-2">
              <span>🔔</span> Alertes Système &amp; Sécurité ({alerts.length})
            </h3>
            <div className="space-y-2">
              {alerts.length === 0 && (
                <p className="text-[11px] text-slate-500">
                  Aucune alerte : aucun des seuils surveillés n&apos;est franchi, et les dépendances répondent.
                </p>
              )}
              {alerts.map((al) => (
                <div
                  key={al.id}
                  className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-800/40 flex items-start justify-between gap-3 text-xs"
                >
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2">
                      <span
                        className={
                          "rounded px-1.5 py-0.2 text-[10px] font-bold uppercase " +
                          (al.severity === "critical"
                            ? "bg-rose-100 text-rose-800"
                            : al.severity === "warning"
                              ? "bg-amber-100 text-amber-800"
                              : "bg-slate-200 text-slate-700")
                        }
                      >
                        {al.severity}
                      </span>
                      <span className="font-bold text-slate-900 dark:text-white">{al.title}</span>
                      <span className="rounded bg-indigo-100 text-indigo-800 px-1.5 py-0.2 text-[10px] font-mono">
                        {al.category}
                      </span>
                    </div>
                    <p className="text-slate-600 dark:text-slate-300 text-[11px]">{al.message}</p>
                  </div>
                  <span className="text-[10px] text-slate-400 whitespace-nowrap">
                    {new Date(al.occurred_at).toLocaleTimeString("fr-FR")}
                  </span>
                </div>
              ))}
            </div>
            {alerts.length > 0 && (
              <p className="text-[10px] text-slate-400">
                Alertes calculées à l&apos;instant à partir de vos données et de l&apos;état de l&apos;instance.
                Aucune n&apos;est acquittée automatiquement : l&apos;acquittement est un acte humain, et ce produit
                n&apos;en enregistre pas encore.
              </p>
            )}
          </div>
        </div>
      )}

      {/* Tab 3: Legal Holds & E-Discovery */}
      {activeTab === "ediscovery" && (
        <div className="space-y-6">
          {canManage && (
            <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900 shadow-sm space-y-3 text-xs">
              <h3 className="font-bold text-slate-900 dark:text-white text-xs uppercase tracking-wider flex items-center gap-2">
                <span>🔒</span> Poser un Gel Légal (Legal Hold Lock)
              </h3>
              <p className="text-slate-500 text-[11px]">
                Empêche toute suppression ou purge de données pour une organisation en cas d&apos;audit externe ou de contentieux.
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <input
                  type="text"
                  placeholder="Référence de l'affaire (ex. DGCCRF-2026-089)"
                  value={caseRef}
                  onChange={(e) => setCaseRef(e.target.value)}
                  className="rounded border border-slate-300 bg-white p-2 text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                />
                <input
                  type="text"
                  placeholder="Motif légal ou autorité requérante..."
                  value={caseReason}
                  onChange={(e) => setCaseReason(e.target.value)}
                  className="rounded border border-slate-300 bg-white p-2 text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                />
              </div>
              <div className="flex justify-end">
                <button
                  type="button"
                  disabled={isCreatingHold}
                  onClick={handleCreateHold}
                  className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                >
                  {isCreatingHold ? "Activation..." : "Activer le gel légal"}
                </button>
              </div>
            </div>
          )}

          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-400">
                <tr>
                  <th className="py-3 px-4 font-semibold">Référence Affaire</th>
                  <th className="py-3 px-4 font-semibold">Motif / Justification</th>
                  <th className="py-3 px-4 font-semibold">Date d&apos;activation</th>
                  <th className="py-3 px-4 font-semibold text-right">Statut</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                {legalHolds.map((h) => (
                  <tr key={h.id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/40">
                    <td className="py-3 px-4 font-mono font-bold text-slate-900 dark:text-white">
                      {h.case_reference}
                    </td>
                    <td className="py-3 px-4 text-slate-700 dark:text-slate-300">
                      {h.reason}
                    </td>
                    <td className="py-3 px-4 text-slate-500">
                      {new Date(h.created_at).toLocaleDateString("fr-FR")}
                    </td>
                    <td className="py-3 px-4 text-right">
                      <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
                        Actif
                      </span>
                    </td>
                  </tr>
                ))}
                {legalHolds.length === 0 && (
                  <tr>
                    <td colSpan={4} className="py-6 text-center text-slate-400">
                      Aucun gel légal actif. Les politiques de rétention standard s&apos;appliquent.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Tab 4: SSO & SCIM 2.0 */}
      {activeTab === "sso" && (
        <div className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900 shadow-sm space-y-4 text-xs">
          <div className="flex items-center gap-2">
            <span className="h-3 w-3 rounded-full bg-emerald-500 animate-pulse" />
            <h3 className="font-bold text-slate-900 dark:text-white text-sm">
              Fédération d&apos;Identité d&apos;Entreprise &amp; SCIM 2.0
            </h3>
          </div>

          <p className="text-slate-600 dark:text-slate-300 leading-relaxed">
            VeriClaim supporte l&apos;authentification unique (SSO OIDC / SAML) et le provisionnement automatique d&apos;utilisateurs via le protocole standard <strong>SCIM 2.0 (RFC 7644)</strong>.
          </p>

          <div className="space-y-2 font-mono text-[11px] bg-slate-50 p-4 rounded-lg border border-slate-200 dark:bg-slate-800 dark:border-slate-700 text-slate-800 dark:text-slate-200">
            <div><strong>Endpoint SCIM Utilisateurs :</strong> /api/v1/enterprise/scim/v2/Users</div>
            <div><strong>Authentification SCIM :</strong> Bearer Token / Clé d&apos;API avec scope admin</div>
            <div><strong>Mappage de rôles :</strong> Automatique (analyst / viewer / admin)</div>
          </div>
        </div>
      )}

      {/* Modal for creating API key */}
      {isKeyModalOpen && (
        <ApiKeyCreateModal
          isOpen={true}
          onClose={() => setIsKeyModalOpen(false)}
          onCreated={() => loadData()}
        />
      )}
    </div>
  );
}
