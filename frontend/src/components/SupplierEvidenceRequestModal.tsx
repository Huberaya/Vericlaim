"use client";

import { useEffect, useState } from "react";
import {
  createEvidenceRequest,
  generateEvidenceRequestTemplate,
  listCatalogProducts,
  listCatalogSuppliers,
  sendEvidenceRequest,
} from "@/lib/api";
import type {
  CatalogProduct,
  CatalogSupplier,
  EvidenceRequest,
  EvidenceRequestItem,
  PersistentEvidenceType,
} from "@/lib/types";

interface SupplierEvidenceRequestModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialClaimId?: string | null;
  initialClaimText?: string | null;
  initialSupplierId?: string | null;
  initialProductId?: string | null;
  onCreated?: (request: EvidenceRequest) => void;
}

const COMMON_EVIDENCE_TYPES: { label: string; value: PersistentEvidenceType }[] = [
  { label: "Bilan ACV / Rapport Carbone (ISO 14044)", value: "lca_report" },
  { label: "Certificat / Label Environnemental", value: "certificate" },
  { label: "Attestation Filière de Recyclage (Citeo / Léko)", value: "recycling_route" },
  { label: "Déclaration Environnementale Fournisseur", value: "environmental_declaration" },
  { label: "Rapport d'Essai Laboratoire", value: "lab_report" },
  { label: "Bilan Émissions GES (Scope 1/2/3)", value: "ghg_inventory" },
  { label: "Autre justificatif", value: "other" },
];

export function SupplierEvidenceRequestModal({
  isOpen,
  onClose,
  initialClaimId,
  initialClaimText,
  initialSupplierId,
  initialProductId,
  onCreated,
}: SupplierEvidenceRequestModalProps) {
  const [suppliers, setSuppliers] = useState<CatalogSupplier[]>([]);
  const [products, setProducts] = useState<CatalogProduct[]>([]);
  const [selectedSupplierId, setSelectedSupplierId] = useState<string>(initialSupplierId || "");
  const [selectedProductId, setSelectedProductId] = useState<string>(initialProductId || "");
  const [targetEvidenceType, setTargetEvidenceType] = useState<PersistentEvidenceType>("environmental_declaration");

  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [items, setItems] = useState<EvidenceRequestItem[]>([]);
  const [newItemName, setNewItemName] = useState("");
  const [newItemType, setNewItemType] = useState("environmental_declaration");
  const [dueAt, setDueAt] = useState("");

  const [isLoading, setIsLoading] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    async function loadCatalog() {
      try {
        const [suppItems, prodItems] = await Promise.all([
          listCatalogSuppliers(),
          listCatalogProducts(),
        ]);
        setSuppliers(suppItems);
        setProducts(prodItems);

        if (initialSupplierId) setSelectedSupplierId(initialSupplierId);
        else if (suppItems.length > 0 && !selectedSupplierId) setSelectedSupplierId(suppItems[0].id);

        if (initialProductId) setSelectedProductId(initialProductId);

        // Auto trigger template generation if claim is provided
        if (initialClaimId || initialClaimText) {
          await handleGenerateTemplate(initialClaimId, initialSupplierId, initialProductId);
        } else {
          // Default due date: +14 days
          const d = new Date();
          d.setDate(d.getDate() + 14);
          setDueAt(d.toISOString().slice(0, 10));
        }
      } catch (err: unknown) {
        console.error("Failed to load catalog for request modal:", err);
      }
    }
    loadCatalog();
  }, [isOpen, initialClaimId, initialSupplierId, initialProductId]);

  async function handleGenerateTemplate(
    claimId?: string | null,
    suppId?: string | null,
    prodId?: string | null,
    evType?: PersistentEvidenceType | null,
  ) {
    setIsGenerating(true);
    setError(null);
    try {
      const tmpl = await generateEvidenceRequestTemplate({
        claim_id: claimId || initialClaimId || null,
        supplier_id: suppId || selectedSupplierId || null,
        product_id: prodId || selectedProductId || null,
        target_evidence_type: evType || targetEvidenceType || null,
      });

      setSubject(tmpl.subject);
      setMessage(tmpl.message);
      setItems(tmpl.requested_items);

      const d = new Date();
      d.setDate(d.getDate() + tmpl.suggested_due_days);
      setDueAt(d.toISOString().slice(0, 10));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Erreur lors de la génération du modèle");
    } finally {
      setIsGenerating(false);
    }
  }

  function handleAddItem() {
    if (!newItemName.trim()) return;
    setItems((prev) => [...prev, { name: newItemName.trim(), type: newItemType }]);
    setNewItemName("");
  }

  function handleRemoveItem(idx: number) {
    setItems((prev) => prev.filter((_, i) => i !== idx));
  }

  async function handleSubmit(andSend: boolean) {
    if (!selectedSupplierId) {
      setError("Veuillez sélectionner un fournisseur destinataire.");
      return;
    }
    if (!subject.trim()) {
      setError("Veuillez renseigner un objet pour la demande.");
      return;
    }
    if (!message.trim()) {
      setError("Veuillez renseigner un message d'explication.");
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      let created = await createEvidenceRequest({
        supplier_id: selectedSupplierId,
        product_id: selectedProductId || null,
        claim_id: initialClaimId || null,
        subject: subject.trim(),
        message: message.trim(),
        requested_items: items,
        due_at: dueAt ? new Date(dueAt).toISOString() : null,
      });

      if (andSend) {
        created = await sendEvidenceRequest(created.id);
      }

      onCreated?.(created);
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Erreur lors de l'enregistrement de la demande.");
    } finally {
      setIsLoading(false);
    }
  }

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4 overflow-y-auto">
      <div className="relative w-full max-w-3xl rounded-xl bg-white shadow-2xl border border-slate-200 dark:bg-slate-900 dark:border-slate-800 flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4 dark:border-slate-800">
          <div>
            <h2 className="text-lg font-bold text-slate-900 dark:text-white flex items-center gap-2">
              <svg className="h-5 w-5 text-indigo-600 dark:text-indigo-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
              Demande Formelle de Preuve Fournisseur
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Émission d&apos;une demande contradictoire pour étayer une allégation environnementale (Directive 2024/825 &amp; AGEC).
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
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/50 dark:text-red-300">
              {error}
            </div>
          )}

          {initialClaimText && (
            <div className="rounded-lg bg-indigo-50/70 border border-indigo-100 p-3.5 dark:bg-indigo-950/30 dark:border-indigo-900/50">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-indigo-600 dark:text-indigo-400">
                Allégation à étayer :
              </span>
              <p className="mt-1 text-sm font-medium text-slate-800 dark:text-slate-200">
                &ldquo;{initialClaimText}&rdquo;
              </p>
            </div>
          )}

          {/* Supplier & Product Selection */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Fournisseur Destinataire *
              </label>
              <select
                value={selectedSupplierId}
                onChange={(e) => setSelectedSupplierId(e.target.value)}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
              >
                <option value="">-- Sélectionner un fournisseur --</option>
                {suppliers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.legal_name} {s.country_code ? `(${s.country_code})` : ""}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Produit Concerné (Optionnel)
              </label>
              <select
                value={selectedProductId}
                onChange={(e) => setSelectedProductId(e.target.value)}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
              >
                <option value="">-- Aucun produit spécifique --</option>
                {products
                  .filter((p) => !selectedSupplierId || p.supplier_id === selectedSupplierId)
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.reference})
                    </option>
                  ))}
              </select>
            </div>
          </div>

          {/* Model Generator Helper */}
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-slate-50 border border-slate-200 p-3 dark:bg-slate-800/50 dark:border-slate-700">
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-slate-600 dark:text-slate-300">
                Générateur de modèle :
              </span>
              <select
                value={targetEvidenceType}
                onChange={(e) => setTargetEvidenceType(e.target.value as PersistentEvidenceType)}
                className="rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-800 dark:border-slate-600 dark:bg-slate-700 dark:text-slate-200"
              >
                {COMMON_EVIDENCE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              disabled={isGenerating}
              onClick={() => handleGenerateTemplate(initialClaimId, selectedSupplierId, selectedProductId, targetEvidenceType)}
              className="inline-flex items-center gap-1.5 rounded bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-100 disabled:opacity-50 dark:bg-indigo-950/60 dark:text-indigo-300 dark:hover:bg-indigo-900"
            >
              {isGenerating ? "Génération..." : "✨ Régénérer le modèle légal"}
            </button>
          </div>

          {/* Subject & Due Date */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="md:col-span-2">
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Objet du message *
              </label>
              <input
                type="text"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                placeholder="ex. Demande d'attestation de recyclabilité"
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
                Date d&apos;échéance (Due Date)
              </label>
              <input
                type="date"
                value={dueAt}
                onChange={(e) => setDueAt(e.target.value)}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-white"
              />
            </div>
          </div>

          {/* Message Content */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1">
              Corps de la demande *
            </label>
            <textarea
              rows={6}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              className="w-full rounded-lg border border-slate-300 bg-white p-3 text-xs font-mono text-slate-900 focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
            />
          </div>

          {/* Requested Items List */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-2">
              Justificatifs et Documents Spécifiques Requis ({items.length})
            </label>
            <div className="space-y-2 mb-3">
              {items.map((item, idx) => (
                <div
                  key={idx}
                  className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-800"
                >
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-indigo-100 px-2 py-0.5 text-[10px] font-medium text-indigo-800 dark:bg-indigo-900/60 dark:text-indigo-300">
                      {item.type}
                    </span>
                    <span className="text-xs font-medium text-slate-800 dark:text-slate-200">
                      {item.name}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleRemoveItem(idx)}
                    className="text-xs text-red-500 hover:text-red-700"
                  >
                    Supprimer
                  </button>
                </div>
              ))}
              {items.length === 0 && (
                <p className="text-xs text-slate-400 italic">Aucun élément spécifique ajouté.</p>
              )}
            </div>

            {/* Add Item form */}
            <div className="flex gap-2">
              <select
                value={newItemType}
                onChange={(e) => setNewItemType(e.target.value)}
                className="rounded border border-slate-300 bg-white px-2 py-1.5 text-xs dark:border-slate-700 dark:bg-slate-800 dark:text-white"
              >
                {COMMON_EVIDENCE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
              <input
                type="text"
                placeholder="Intitulé du document exigé..."
                value={newItemName}
                onChange={(e) => setNewItemName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    handleAddItem();
                  }
                }}
                className="flex-1 rounded border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
              />
              <button
                type="button"
                onClick={handleAddItem}
                className="rounded bg-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-800 hover:bg-slate-300 dark:bg-slate-700 dark:text-slate-200 dark:hover:bg-slate-600"
              >
                + Ajouter
              </button>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-slate-200 bg-slate-50 px-6 py-4 dark:border-slate-800 dark:bg-slate-900/50">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-200 dark:text-slate-400 dark:hover:bg-slate-800"
          >
            Annuler
          </button>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={isLoading}
              onClick={() => handleSubmit(false)}
              className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
            >
              Enregistrer brouillon
            </button>
            <button
              type="button"
              disabled={isLoading}
              onClick={() => handleSubmit(true)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700 disabled:opacity-50"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
              </svg>
              {isLoading ? "Traitement..." : "Enregistrer et Envoyer"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
