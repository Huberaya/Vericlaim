"use client";

import { useEffect, useState } from "react";
import {
  createApiKey,
  createTenant,
  fetchApiKeys,
  fetchCurrentTenant,
  getStoredApiKey,
  revokeApiKey,
  setStoredApiKey,
} from "@/lib/api";
import type { ApiKeyItem, TenantResponse } from "@/lib/types";

export default function TenantApiKeyView() {
  const [currentKey, setCurrentKey] = useState<string>("");
  const [tenant, setTenant] = useState<TenantResponse | null>(null);
  const [apiKeys, setApiKeys] = useState<ApiKeyItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Formulaire nouveau Tenant
  const [newOrgName, setNewOrgName] = useState("");
  const [newOrgSlug, setNewOrgSlug] = useState("");
  const [newOrgTier, setNewOrgTier] = useState("enterprise");

  // Formulaire nouvelle clé
  const [newKeyName, setNewKeyName] = useState("");
  const [generatedSecretKey, setGeneratedSecretKey] = useState<string | null>(null);

  useEffect(() => {
    const key = getStoredApiKey() || "";
    setCurrentKey(key);
    loadTenantData(key);
  }, []);

  async function loadTenantData(activeKey?: string) {
    setIsLoading(true);
    setError(null);
    try {
      const tenantData = await fetchCurrentTenant();
      setTenant(tenantData);
      if (activeKey) {
        try {
          const keysData = await fetchApiKeys();
          setApiKeys(keysData.keys);
        } catch {
          setApiKeys([]);
        }
      } else {
        setApiKeys([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur de chargement du tenant");
    } finally {
      setIsLoading(false);
    }
  }

  const handleApplyKey = (keyToApply: string) => {
    const trimmed = keyToApply.trim();
    setStoredApiKey(trimmed || null);
    setCurrentKey(trimmed);
    setSuccessMessage(trimmed ? "Clé API appliquée avec succès !" : "Mode Démo Public restauré.");
    setTimeout(() => setSuccessMessage(null), 3500);
    loadTenantData(trimmed);
  };

  const handleCreateTenant = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newOrgName.trim()) return;
    setIsLoading(true);
    setError(null);
    try {
      const res = await createTenant(newOrgName.trim(), newOrgSlug.trim() || undefined, newOrgTier);
      const generatedKey = res.initial_api_key.key;
      setStoredApiKey(generatedKey);
      setCurrentKey(generatedKey);
      setGeneratedSecretKey(generatedKey);
      setNewOrgName("");
      setNewOrgSlug("");
      setSuccessMessage(`Organisation « ${res.organization.name} » créée avec succès !`);
      await loadTenantData(generatedKey);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur lors de la création de l'organisation");
    } finally {
      setIsLoading(false);
    }
  };

  const handleGenerateKey = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newKeyName.trim()) return;
    setIsLoading(true);
    setError(null);
    try {
      const res = await createApiKey(newKeyName.trim(), ["audit:read", "audit:write", "batch:run"]);
      setGeneratedSecretKey(res.key);
      setNewKeyName("");
      setSuccessMessage("Nouvelle clé API générée. Copiez-la immédiatement !");
      await loadTenantData(currentKey);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur lors de la génération de la clé");
    } finally {
      setIsLoading(false);
    }
  };

  const handleRevokeKey = async (keyId: string) => {
    if (!confirm("Êtes-vous sûr de vouloir révoquer cette clé API ? Tout appel avec celle-ci sera immédiatement refusé.")) {
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      await revokeApiKey(keyId);
      setSuccessMessage("Clé révoquée avec succès.");
      setTimeout(() => setSuccessMessage(null), 3000);
      await loadTenantData(currentKey);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Erreur lors de la révocation");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="tenant-view space-y-6" style={{ maxWidth: 1040, margin: "0 auto", padding: "1.5rem 1rem" }}>
      {/* Header & Status Card */}
      <div className="panel" style={{ background: "var(--card-bg, #ffffff)", border: "1px solid var(--border-color, #e2e8f0)", borderRadius: 12, padding: "1.5rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "1rem" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
              <h2 style={{ fontSize: "1.35rem", fontWeight: 700, margin: 0, color: "var(--heading-color, #0f172a)" }}>
                Isolation Multi-Tenant & Clés API
              </h2>
              <span
                style={{
                  padding: "0.2rem 0.6rem",
                  borderRadius: 20,
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  background: currentKey ? "#dcfce7" : "#f1f5f9",
                  color: currentKey ? "#166534" : "#475569",
                  border: currentKey ? "1px solid #86efac" : "1px solid #cbd5e1",
                }}
              >
                {currentKey ? "🔒 Tenant Isolé (Privé)" : "🌐 Mode Public / Démo"}
              </span>
            </div>
            <p style={{ margin: "0.5rem 0 0", color: "#64748b", fontSize: "0.9rem" }}>
              Chaque organisation dispose d&apos;un registre cryptographique immuable étanche. Les audits et analyses
              sont strictement isolés par tenant via des clés d&apos;authentification <code style={{ color: "#0284c7" }}>X-API-Key</code>.
            </p>
          </div>

          <div style={{ display: "flex", gap: "0.5rem" }}>
            {currentKey && (
              <button
                type="button"
                onClick={() => handleApplyKey("")}
                className="btn-secondary"
                style={{ fontSize: "0.82rem", padding: "0.4rem 0.8rem", borderRadius: 6, border: "1px solid #cbd5e1" }}
              >
                Quitter le tenant (Mode Public)
              </button>
            )}
          </div>
        </div>

        {/* Info Cartouche Tenant Actif */}
        {tenant && (
          <div
            style={{
              marginTop: "1.25rem",
              padding: "1rem",
              borderRadius: 8,
              background: "#f8fafc",
              border: "1px solid #e2e8f0",
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              gap: "1rem",
            }}
          >
            <div>
              <span style={{ fontSize: "0.75rem", textTransform: "uppercase", color: "#64748b", fontWeight: 600 }}>
                Organisation Active
              </span>
              <div style={{ fontWeight: 700, fontSize: "1.05rem", color: "#1e293b" }}>{tenant.name}</div>
              <div style={{ fontSize: "0.78rem", color: "#94a3b8" }}>Slug: {tenant.slug}</div>
            </div>

            <div>
              <span style={{ fontSize: "0.75rem", textTransform: "uppercase", color: "#64748b", fontWeight: 600 }}>
                Tier de Souscription
              </span>
              <div style={{ fontWeight: 600, fontSize: "0.95rem", color: "#0369a1", textTransform: "uppercase" }}>
                {tenant.tier}
              </div>
              <div style={{ fontSize: "0.78rem", color: "#94a3b8" }}>Quota alloué : illimité</div>
            </div>

            <div>
              <span style={{ fontSize: "0.75rem", textTransform: "uppercase", color: "#64748b", fontWeight: 600 }}>
                Audits Cloisonnés
              </span>
              <div style={{ fontWeight: 700, fontSize: "1.05rem", color: "#1e293b" }}>
                {tenant.total_audits_count} rapport(s)
              </div>
              <div style={{ fontSize: "0.78rem", color: "#94a3b8" }}>100% étanche à ce tenant</div>
            </div>

            <div>
              <span style={{ fontSize: "0.75rem", textTransform: "uppercase", color: "#64748b", fontWeight: 600 }}>
                Clés API Actives
              </span>
              <div style={{ fontWeight: 700, fontSize: "1.05rem", color: "#1e293b" }}>
                {tenant.api_keys_count} active(s)
              </div>
              <div style={{ fontSize: "0.78rem", color: "#94a3b8" }}>Contrôlées par scopes</div>
            </div>
          </div>
        )}
      </div>

      {/* Notifications */}
      {error && (
        <div style={{ padding: "0.75rem 1rem", background: "#fef2f2", border: "1px solid #f87171", borderRadius: 8, color: "#991b1b", fontSize: "0.9rem" }}>
          ⚠️ {error}
        </div>
      )}
      {successMessage && (
        <div style={{ padding: "0.75rem 1rem", background: "#f0fdf4", border: "1px solid #4ade80", borderRadius: 8, color: "#166534", fontSize: "0.9rem" }}>
          ✓ {successMessage}
        </div>
      )}

      {/* Popup / Alerte affichant la clé secrète générée */}
      {generatedSecretKey && (
        <div style={{ padding: "1.25rem", background: "#eff6ff", border: "2px solid #3b82f6", borderRadius: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h4 style={{ margin: 0, color: "#1e40af", fontWeight: 700 }}>
              🔑 Votre clé API a été générée avec succès
            </h4>
            <button
              type="button"
              onClick={() => setGeneratedSecretKey(null)}
              style={{ background: "none", border: 0, cursor: "pointer", fontSize: "1.1rem", color: "#64748b" }}
            >
              ✕
            </button>
          </div>
          <p style={{ margin: "0.5rem 0", fontSize: "0.85rem", color: "#1e3a8a" }}>
            Copiez cette clé immédiatement. Pour des raisons de sécurité cryptographique, elle ne sera plus jamais affichée :
          </p>
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.5rem" }}>
            <input
              type="text"
              readOnly
              value={generatedSecretKey}
              style={{ flex: 1, padding: "0.6rem 0.8rem", fontFamily: "monospace", fontSize: "0.95rem", background: "#ffffff", border: "1px solid #93c5fd", borderRadius: 6 }}
            />
            <button
              type="button"
              onClick={() => {
                navigator.clipboard.writeText(generatedSecretKey);
                alert("Clé API copiée dans le presse-papier !");
              }}
              style={{ padding: "0.6rem 1rem", background: "#2563eb", color: "#ffffff", border: 0, borderRadius: 6, fontWeight: 600, cursor: "pointer" }}
            >
              Copier
            </button>
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: "1.5rem" }}>
        {/* Section 1 : Basculer de clé / Saisie manuelle */}
        <div className="panel" style={{ background: "var(--card-bg, #ffffff)", border: "1px solid #e2e8f0", borderRadius: 12, padding: "1.25rem" }}>
          <h3 style={{ fontSize: "1.05rem", fontWeight: 700, margin: "0 0 0.5rem", color: "#1e293b" }}>
            Connexion par Clé API
          </h3>
          <p style={{ fontSize: "0.85rem", color: "#64748b", margin: "0 0 1rem" }}>
            Renseignez votre clé <code style={{ color: "#0284c7" }}>vk_live_...</code> pour isoler vos audits dans votre tenant.
          </p>

          <div style={{ display: "flex", gap: "0.5rem" }}>
            <input
              type="password"
              placeholder="vk_live_xxxxxxxxxxxxxxxx"
              value={currentKey}
              onChange={(e) => setCurrentKey(e.target.value)}
              style={{ flex: 1, padding: "0.6rem", borderRadius: 6, border: "1px solid #cbd5e1", fontSize: "0.9rem", fontFamily: "monospace" }}
            />
            <button
              type="button"
              onClick={() => handleApplyKey(currentKey)}
              disabled={isLoading}
              style={{ padding: "0.6rem 1rem", background: "#0f172a", color: "#ffffff", border: 0, borderRadius: 6, fontWeight: 600, cursor: "pointer" }}
            >
              Appliquer
            </button>
          </div>
        </div>

        {/* Section 2 : Créer un nouveau Tenant Organisation */}
        <div className="panel" style={{ background: "var(--card-bg, #ffffff)", border: "1px solid #e2e8f0", borderRadius: 12, padding: "1.25rem" }}>
          <h3 style={{ fontSize: "1.05rem", fontWeight: 700, margin: "0 0 0.5rem", color: "#1e293b" }}>
            Créer une Organisation (Nouveau Tenant)
          </h3>
          <p style={{ fontSize: "0.85rem", color: "#64748b", margin: "0 0 1rem" }}>
            Crée un espace de conformité dédié et provisionne instantanément sa première clé API.
          </p>

          <form onSubmit={handleCreateTenant} style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
            <input
              type="text"
              required
              placeholder="Nom de l'organisation (ex: Carrefour Bio, Sephora...)"
              value={newOrgName}
              onChange={(e) => setNewOrgName(e.target.value)}
              style={{ padding: "0.55rem", borderRadius: 6, border: "1px solid #cbd5e1", fontSize: "0.88rem" }}
            />
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <input
                type="text"
                placeholder="Slug optionnel (ex: carrefour-bio)"
                value={newOrgSlug}
                onChange={(e) => setNewOrgSlug(e.target.value)}
                style={{ flex: 1, padding: "0.55rem", borderRadius: 6, border: "1px solid #cbd5e1", fontSize: "0.88rem" }}
              />
              <select
                value={newOrgTier}
                onChange={(e) => setNewOrgTier(e.target.value)}
                style={{ padding: "0.55rem", borderRadius: 6, border: "1px solid #cbd5e1", fontSize: "0.88rem" }}
              >
                <option value="enterprise">Enterprise</option>
                <option value="standard">Standard</option>
              </select>
            </div>
            <button
              type="submit"
              disabled={isLoading || !newOrgName.trim()}
              style={{ padding: "0.6rem", background: "#059669", color: "#ffffff", border: 0, borderRadius: 6, fontWeight: 600, cursor: "pointer" }}
            >
              + Initialiser l&apos;Organisation & Générer la Clé
            </button>
          </form>
        </div>
      </div>

      {/* Section 3 : Clés API enregistrées dans le Tenant actif */}
      {currentKey && (
        <div className="panel" style={{ background: "var(--card-bg, #ffffff)", border: "1px solid #e2e8f0", borderRadius: 12, padding: "1.5rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", flexWrap: "wrap", gap: "0.5rem" }}>
            <div>
              <h3 style={{ fontSize: "1.1rem", fontWeight: 700, margin: 0, color: "#1e293b" }}>
                Clés d&apos;Accès de l&apos;Organisation
              </h3>
              <p style={{ margin: "0.25rem 0 0", fontSize: "0.85rem", color: "#64748b" }}>
                Gérez les tokens d&apos;API utilisés par vos scrapers de sites, pipelines CI/CD ou outils ERP.
              </p>
            </div>

            {/* Formulaire ajout clé rapide */}
            <form onSubmit={handleGenerateKey} style={{ display: "flex", gap: "0.5rem" }}>
              <input
                type="text"
                required
                placeholder="Nom de la clé (ex: Scraper E-commerce)"
                value={newKeyName}
                onChange={(e) => setNewKeyName(e.target.value)}
                style={{ padding: "0.45rem 0.65rem", borderRadius: 6, border: "1px solid #cbd5e1", fontSize: "0.85rem" }}
              />
              <button
                type="submit"
                disabled={isLoading || !newKeyName.trim()}
                style={{ padding: "0.45rem 0.85rem", background: "#2563eb", color: "#ffffff", border: 0, borderRadius: 6, fontWeight: 600, fontSize: "0.85rem", cursor: "pointer" }}
              >
                + Générer une clé
              </button>
            </form>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem", textAlign: "left" }}>
              <thead>
                <tr style={{ borderBottom: "2px solid #e2e8f0", color: "#64748b" }}>
                  <th style={{ padding: "0.6rem 0.5rem" }}>Nom</th>
                  <th style={{ padding: "0.6rem 0.5rem" }}>Préfixe Clé</th>
                  <th style={{ padding: "0.6rem 0.5rem" }}>Scopes</th>
                  <th style={{ padding: "0.6rem 0.5rem" }}>Date de création</th>
                  <th style={{ padding: "0.6rem 0.5rem" }}>Dernier usage</th>
                  <th style={{ padding: "0.6rem 0.5rem" }}>Statut</th>
                  <th style={{ padding: "0.6rem 0.5rem", textAlign: "right" }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {apiKeys.length === 0 ? (
                  <tr>
                    <td colSpan={7} style={{ padding: "1.5rem", textAlign: "center", color: "#94a3b8" }}>
                      Aucune clé listée pour cette organisation.
                    </td>
                  </tr>
                ) : (
                  apiKeys.map((k) => (
                    <tr key={k.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                      <td style={{ padding: "0.75rem 0.5rem", fontWeight: 600, color: "#1e293b" }}>{k.name}</td>
                      <td style={{ padding: "0.75rem 0.5rem" }}>
                        <code style={{ background: "#f1f5f9", padding: "0.2rem 0.4rem", borderRadius: 4, color: "#0f172a" }}>
                          {k.key_prefix}••••••••
                        </code>
                      </td>
                      <td style={{ padding: "0.75rem 0.5rem" }}>
                        <div style={{ display: "flex", gap: "0.25rem", flexWrap: "wrap" }}>
                          {k.scopes.map((s) => (
                            <span key={s} style={{ fontSize: "0.7rem", background: "#e0f2fe", color: "#0369a1", padding: "0.1rem 0.4rem", borderRadius: 4 }}>
                              {s}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td style={{ padding: "0.75rem 0.5rem", color: "#64748b" }}>
                        {new Date(k.created_at_utc).toLocaleDateString("fr-FR")}
                      </td>
                      <td style={{ padding: "0.75rem 0.5rem", color: "#64748b" }}>
                        {k.last_used_at_utc ? new Date(k.last_used_at_utc).toLocaleTimeString("fr-FR") : "Jamais"}
                      </td>
                      <td style={{ padding: "0.75rem 0.5rem" }}>
                        <span style={{ fontSize: "0.75rem", fontWeight: 600, color: k.is_active ? "#166534" : "#991b1b" }}>
                          {k.is_active ? "● Actif" : "○ Révoqué"}
                        </span>
                      </td>
                      <td style={{ padding: "0.75rem 0.5rem", textAlign: "right" }}>
                        {k.is_active && (
                          <button
                            type="button"
                            onClick={() => handleRevokeKey(k.id)}
                            style={{ padding: "0.3rem 0.6rem", background: "#fef2f2", color: "#dc2626", border: "1px solid #fca5a5", borderRadius: 6, fontSize: "0.78rem", cursor: "pointer" }}
                          >
                            Révoquer
                          </button>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
