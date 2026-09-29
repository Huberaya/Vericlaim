"use client";

import { useState } from "react";
import { createApiKey } from "@/lib/api";
import type { ApiKeyCreatedResponse } from "@/lib/types";

interface ApiKeyCreateModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreated?: () => void;
}

const AVAILABLE_SCOPES = [
  { id: "audit:run", label: "Exécution des audits d'allégations (audit:run)" },
  { id: "audit:read", label: "Lecture des rapports d'analyse (audit:read)" },
  { id: "catalog:read", label: "Consultation du catalogue fournisseurs (catalog:read)" },
  { id: "catalog:manage", label: "Gestion du catalogue achats (catalog:manage)" },
  { id: "evidence:read", label: "Consultation du registre probatoire (evidence:read)" },
  { id: "evidence:manage", label: "Dépôt et liaison de preuves (evidence:manage)" },
  { id: "rules:read", label: "Consultation du Rule Book (rules:read)" },
];

export function ApiKeyCreateModal({ isOpen, onClose, onCreated }: ApiKeyCreateModalProps) {
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["audit:run", "catalog:read", "evidence:read"]);
  const [rateLimit, setRateLimit] = useState(120);
  const [expiresDays, setExpiresDays] = useState(365);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Result state (showing raw key)
  const [createdKey, setCreatedKey] = useState<ApiKeyCreatedResponse | null>(null);
  const [copied, setCopied] = useState(false);

  function handleToggleScope(scopeId: string) {
    setScopes((prev) =>
      prev.includes(scopeId) ? prev.filter((s) => s !== scopeId) : [...prev, scopeId]
    );
  }

  async function handleCreate() {
    if (!name.trim()) {
      setError("Veuillez renseigner un nom pour la clé d'API.");
      return;
    }
    if (scopes.length === 0) {
      setError("Veuillez sélectionner au moins une permission (scope).");
      return;
    }

    setIsLoading(true);
    setError(null);
    try {
      const result = await createApiKey({
        name: name.trim(),
        scopes,
        rate_limit_per_minute: rateLimit,
        expires_in_days: expiresDays || null,
      });
      setCreatedKey(result);
      onCreated?.();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Erreur lors de la création de la clé d'API");
    } finally {
      setIsLoading(false);
    }
  }

  function handleCopy() {
    if (!createdKey) return;
    navigator.clipboard.writeText(createdKey.raw_api_key);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4 overflow-y-auto">
      <div className="relative w-full max-w-lg rounded-xl bg-white shadow-2xl border border-slate-200 dark:bg-slate-900 dark:border-slate-800 flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4 dark:border-slate-800">
          <div>
            <h2 className="text-lg font-bold text-slate-900 dark:text-white flex items-center gap-2">
              <span>🔑</span> Nouvelle Clé d&apos;API Partenaire B2B
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Accès programmatique sécurisé avec quotas de requêtes et permissions bornées.
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200"
          >
            ✕
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4 text-xs">
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-red-700 dark:border-red-900 dark:bg-red-950/50 dark:text-red-300">
              {error}
            </div>
          )}

          {!createdKey ? (
            <>
              <div>
                <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                  Nom de l&apos;intégration / Application *
                </label>
                <input
                  type="text"
                  placeholder="ex. Intégration ERP SAP Achats"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">
                  Permissions &amp; Scopes d&apos;accès *
                </label>
                <div className="space-y-1.5 max-h-40 overflow-y-auto p-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-850">
                  {AVAILABLE_SCOPES.map((sc) => (
                    <label key={sc.id} className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={scopes.includes(sc.id)}
                        onChange={() => handleToggleScope(sc.id)}
                        className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                      />
                      <span className="text-slate-800 dark:text-slate-200">{sc.label}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                    Quota (Requêtes / minute)
                  </label>
                  <input
                    type="number"
                    min={10}
                    max={1000}
                    value={rateLimit}
                    onChange={(e) => setRateLimit(Number(e.target.value))}
                    className="w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                    Validité (Jours)
                  </label>
                  <input
                    type="number"
                    min={30}
                    max={730}
                    value={expiresDays}
                    onChange={(e) => setExpiresDays(Number(e.target.value))}
                    className="w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                  />
                </div>
              </div>
            </>
          ) : (
            <div className="space-y-4">
              <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/40 text-amber-950 dark:text-amber-200 space-y-1.5">
                <div className="flex items-center gap-2 font-bold text-xs uppercase">
                  <span>⚠️</span> Copiez votre clé d&apos;API maintenant
                </div>
                <p className="text-[11px] leading-relaxed">
                  Pour des raisons de sécurité, cette clé secrète ne sera <strong>plus jamais affichée</strong> après la fermeture de cette fenêtre.
                </p>
              </div>

              <div>
                <label className="block text-[10px] font-bold text-slate-500 uppercase mb-1">
                  Clé d&apos;API Secrète ({createdKey.prefix}…)
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    readOnly
                    value={createdKey.raw_api_key}
                    className="w-full font-mono text-xs rounded border border-slate-300 bg-slate-100 p-2 text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
                  />
                  <button
                    type="button"
                    onClick={handleCopy}
                    className="rounded bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-700 shrink-0"
                  >
                    {copied ? "Copié !" : "Copier"}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-slate-200 bg-slate-50 px-6 py-4 dark:border-slate-800 dark:bg-slate-900/50 flex justify-end gap-2">
          {!createdKey ? (
            <>
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-200 dark:text-slate-400 dark:hover:bg-slate-800"
              >
                Annuler
              </button>
              <button
                type="button"
                disabled={isLoading}
                onClick={handleCreate}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {isLoading ? "Génération..." : "Générer la clé d'API"}
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-700"
            >
              J&apos;ai bien copié la clé · Fermer
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
