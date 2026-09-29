"use client";

import { useState } from "react";
import { importPilotCatalog } from "@/lib/api";
import type { CatalogImportItem, CatalogImportResult } from "@/lib/types";

interface CatalogBatchImportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onImported?: () => void;
}

const SAMPLE_CSV = `Fournisseur Emballages Verts SAS;FR;contact@emballages-verts.fr;KRAFT-01;Sachet Papier Kraft 500g;packaging
Fournisseur Emballages Verts SAS;FR;contact@emballages-verts.fr;KRAFT-02;Sachet Papier Kraft 1kg;packaging
Verrerie Durable SARL;FR;achats@verrerie-durable.fr;POT-250;Bocal Verre Recyclé 250ml;glass
Plastiques Circulaires EU;DE;info@circular-plastics.de;PET-R50;Bouteille rPET 50% Recyclé;bottles`;

export function CatalogBatchImportModal({
  isOpen,
  onClose,
  onImported,
}: CatalogBatchImportModalProps) {
  const [inputText, setInputText] = useState("");
  const [delimiter, setDelimiter] = useState(";");
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<CatalogImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  function parseCSV(text: string): CatalogImportItem[] {
    const lines = text
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l.length > 0 && !l.startsWith("#"));

    const items: CatalogImportItem[] = [];
    for (const line of lines) {
      const parts = line.split(delimiter).map((p) => p.trim());
      if (parts.length >= 1 && parts[0]) {
        items.push({
          supplier_legal_name: parts[0],
          supplier_country: parts[1] || "FR",
          supplier_email: parts[2] || null,
          product_reference: parts[3] || null,
          product_name: parts[4] || null,
          product_category: parts[5] || "packaging",
        });
      }
    }
    return items;
  }

  async function handleImport() {
    setError(null);
    setResult(null);

    const items = parseCSV(inputText);
    if (items.length === 0) {
      setError("Veuillez renseigner au moins une ligne de données valide.");
      return;
    }

    setIsLoading(true);
    try {
      const res = await importPilotCatalog(items);
      setResult(res);
      onImported?.();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Erreur lors de l'import");
    } finally {
      setIsLoading(false);
    }
  }

  if (!isOpen) return null;

  const parsedCount = parseCSV(inputText).length;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4 overflow-y-auto">
      <div className="relative w-full max-w-2xl rounded-xl bg-white shadow-2xl border border-slate-200 dark:bg-slate-900 dark:border-slate-800 flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4 dark:border-slate-800">
          <div>
            <h2 className="text-lg font-bold text-slate-900 dark:text-white flex items-center gap-2">
              <span>📥</span> Import Contrôlé de Catalogue (Batch Pilote)
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Chargement rapide et idempotent des fournisseurs et références produits de l&apos;organisation.
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

          {result && (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 p-4 dark:border-emerald-900/60 dark:bg-emerald-950/30 space-y-2">
              <h4 className="font-bold text-emerald-950 dark:text-emerald-300 text-xs uppercase flex items-center gap-1.5">
                <span>✓</span> Rapport d&apos;Importation Terminé
              </h4>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-slate-800 dark:text-slate-200">
                <div className="bg-white p-2 rounded border border-emerald-100 dark:bg-slate-800 dark:border-slate-700">
                  <span className="text-[10px] text-slate-500 uppercase">Fournisseurs créés</span>
                  <div className="text-lg font-bold text-emerald-600">{result.suppliers_created}</div>
                </div>
                <div className="bg-white p-2 rounded border border-emerald-100 dark:bg-slate-800 dark:border-slate-700">
                  <span className="text-[10px] text-slate-500 uppercase">Fournisseurs réutilisés</span>
                  <div className="text-lg font-bold text-slate-700 dark:text-slate-300">{result.suppliers_reused}</div>
                </div>
                <div className="bg-white p-2 rounded border border-emerald-100 dark:bg-slate-800 dark:border-slate-700">
                  <span className="text-[10px] text-slate-500 uppercase">Produits créés</span>
                  <div className="text-lg font-bold text-emerald-600">{result.products_created}</div>
                </div>
                <div className="bg-white p-2 rounded border border-emerald-100 dark:bg-slate-800 dark:border-slate-700">
                  <span className="text-[10px] text-slate-500 uppercase">Produits réutilisés</span>
                  <div className="text-lg font-bold text-slate-700 dark:text-slate-300">{result.products_reused}</div>
                </div>
              </div>

              {result.errors.length > 0 && (
                <div className="rounded bg-rose-50 p-2 text-rose-700 border border-rose-200 space-y-1 text-[11px]">
                  <strong>Avertissements / Erreurs ({result.errors.length}) :</strong>
                  <ul className="list-disc list-inside">
                    {result.errors.map((err, i) => (
                      <li key={i}>{err}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          <div className="flex items-center justify-between">
            <span className="text-slate-600 dark:text-slate-300 font-semibold">
              Format attendu (CSV avec délimiteur) :
            </span>
            <button
              type="button"
              onClick={() => setInputText(SAMPLE_CSV)}
              className="text-xs font-semibold text-indigo-600 hover:underline dark:text-indigo-400"
            >
              ✨ Charger un exemple de lot pilote
            </button>
          </div>

          <p className="text-[11px] text-slate-500 font-mono bg-slate-50 p-2 rounded border border-slate-200 dark:bg-slate-800/40 dark:border-slate-800">
            Fournisseur ; Pays (ex. FR) ; Email ; Référence Produit ; Nom Produit ; Catégorie
          </p>

          <div>
            <textarea
              rows={8}
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder="Collez ici les lignes CSV..."
              className="w-full rounded-lg border border-slate-300 bg-white p-3 font-mono text-xs text-slate-900 focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
            />
            <div className="flex justify-between items-center text-[11px] text-slate-500 mt-1">
              <span>Lignes détectées : <strong>{parsedCount}</strong></span>
              <div className="flex items-center gap-1">
                <span>Délimiteur :</span>
                <select
                  value={delimiter}
                  onChange={(e) => setDelimiter(e.target.value)}
                  className="rounded border border-slate-300 bg-white px-1 py-0.5 text-xs dark:bg-slate-800"
                >
                  <option value=";">Point-virgule (;)</option>
                  <option value=",">Virgule (,)</option>
                  <option value="\t">Tabulation (TSV)</option>
                </select>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="border-t border-slate-200 bg-slate-50 px-6 py-4 dark:border-slate-800 dark:bg-slate-900/50 flex items-center justify-between">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-200 dark:text-slate-400 dark:hover:bg-slate-800"
          >
            Fermer
          </button>
          <button
            type="button"
            disabled={isLoading || parsedCount === 0}
            onClick={handleImport}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50 shadow-sm"
          >
            {isLoading ? "Importation..." : `Importer les ${parsedCount} lignes`}
          </button>
        </div>
      </div>
    </div>
  );
}
