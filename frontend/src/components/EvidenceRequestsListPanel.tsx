"use client";

import { useEffect, useState } from "react";
import {
  deleteEvidenceRequest,
  listCatalogSuppliers,
  listEvidenceRequests,
  remindEvidenceRequest,
  sendEvidenceRequest,
  updateEvidenceRequest,
} from "@/lib/api";
import type {
  CatalogSupplier,
  EvidenceRequest,
  EvidenceRequestStatus,
} from "@/lib/types";
import { SupplierEvidenceRequestModal } from "./SupplierEvidenceRequestModal";

export function EvidenceRequestsListPanel() {
  const [requests, setRequests] = useState<EvidenceRequest[]>([]);
  const [suppliers, setSuppliers] = useState<CatalogSupplier[]>([]);
  const [selectedSupplierId, setSelectedSupplierId] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // New request modal
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [actionInProgress, setActionInProgress] = useState<string | null>(null);

  async function loadData() {
    setIsLoading(true);
    setError(null);
    try {
      const [suppItems, reqRes] = await Promise.all([
        listCatalogSuppliers(),
        listEvidenceRequests({
          supplier_id: selectedSupplierId || undefined,
          status: statusFilter !== "all" ? (statusFilter as EvidenceRequestStatus) : undefined,
          limit: 50,
        }),
      ]);
      setSuppliers(suppItems);
      setRequests(reqRes.items);
    } catch (err: unknown) {
      console.error("Failed to load evidence requests:", err);
      setError(err instanceof Error ? err.message : "Erreur lors du chargement des demandes.");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    loadData();
  }, [selectedSupplierId, statusFilter]);

  async function handleSend(reqId: string) {
    setActionInProgress(reqId);
    try {
      await sendEvidenceRequest(reqId);
      await loadData();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Erreur lors de l'envoi");
    } finally {
      setActionInProgress(null);
    }
  }

  async function handleRemind(reqId: string) {
    setActionInProgress(reqId);
    try {
      await remindEvidenceRequest(reqId);
      await loadData();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Erreur lors de la relance");
    } finally {
      setActionInProgress(null);
    }
  }

  async function handleMarkFulfilled(reqId: string) {
    setActionInProgress(reqId);
    try {
      await updateEvidenceRequest(reqId, { status: "fulfilled" });
      await loadData();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Erreur de mise à jour");
    } finally {
      setActionInProgress(null);
    }
  }

  async function handleDelete(reqId: string) {
    if (!confirm("Êtes-vous sûr de vouloir supprimer cette demande ?")) return;
    setActionInProgress(reqId);
    try {
      await deleteEvidenceRequest(reqId);
      await loadData();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Erreur de suppression");
    } finally {
      setActionInProgress(null);
    }
  }

  const getStatusBadge = (status: EvidenceRequestStatus) => {
    switch (status) {
      case "draft":
        return <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-300">Brouillon</span>;
      case "sent":
        return <span className="rounded-full bg-blue-100 px-2.5 py-0.5 text-xs font-semibold text-blue-800 dark:bg-blue-950/80 dark:text-blue-300">Envoyée (En attente)</span>;
      case "received":
        return <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-800 dark:bg-amber-950/80 dark:text-amber-300">Reçue (À vérifier)</span>;
      case "fulfilled":
        return <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-semibold text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300">✓ Complète</span>;
      case "overdue":
        return <span className="rounded-full bg-rose-100 px-2.5 py-0.5 text-xs font-semibold text-rose-800 dark:bg-rose-950/80 dark:text-rose-300">⚠ En retard</span>;
      case "cancelled":
        return <span className="rounded-full bg-slate-200 px-2.5 py-0.5 text-xs font-medium text-slate-500 line-through">Annulée</span>;
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
            <svg className="h-6 w-6 text-indigo-600 dark:text-indigo-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
            Demandes de Preuves Fournisseurs
          </h2>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Suivi centralisé des demandes contradictoires de certificats, bilans ACV et attestations de recyclabilité.
          </p>
        </div>

        <button
          onClick={() => setIsModalOpen(true)}
          className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-700 transition"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          Nouvelle Demande Fournisseur
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-white p-3 rounded-xl border border-slate-200 shadow-sm dark:bg-slate-900 dark:border-slate-800">
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setStatusFilter("all")}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              statusFilter === "all"
                ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900"
                : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"
            }`}
          >
            Toutes
          </button>
          <button
            onClick={() => setStatusFilter("draft")}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              statusFilter === "draft"
                ? "bg-slate-700 text-white"
                : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300"
            }`}
          >
            Brouillons
          </button>
          <button
            onClick={() => setStatusFilter("sent")}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              statusFilter === "sent"
                ? "bg-blue-600 text-white"
                : "bg-blue-50 text-blue-700 hover:bg-blue-100 dark:bg-blue-950/40 dark:text-blue-300"
            }`}
          >
            Envoyées
          </button>
          <button
            onClick={() => setStatusFilter("fulfilled")}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              statusFilter === "fulfilled"
                ? "bg-emerald-600 text-white"
                : "bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-950/40 dark:text-emerald-300"
            }`}
          >
            Complétées
          </button>
        </div>

        <select
          value={selectedSupplierId}
          onChange={(e) => setSelectedSupplierId(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-white"
        >
          <option value="">-- Tous les fournisseurs --</option>
          {suppliers.map((s) => (
            <option key={s.id} value={s.id}>
              {s.legal_name}
            </option>
          ))}
        </select>
      </div>

      {/* Requests List */}
      <div className="space-y-3">
        {requests.map((req) => (
          <div
            key={req.id}
            className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900 flex flex-col md:flex-row md:items-center justify-between gap-4"
          >
            <div className="space-y-1.5 flex-1">
              <div className="flex items-center gap-2.5 flex-wrap">
                {getStatusBadge(req.status)}
                <span className="font-semibold text-sm text-slate-900 dark:text-white">
                  {req.subject}
                </span>
              </div>

              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
                <span>
                  🏢 <strong>Fournisseur :</strong> {req.supplier_name || "Non spécifié"}
                </span>
                {req.product_name && (
                  <span>
                    📦 <strong>Produit :</strong> {req.product_name}
                  </span>
                )}
                {req.due_at && (
                  <span>
                    📅 <strong>Échéance :</strong> {new Date(req.due_at).toLocaleDateString("fr-FR")}
                  </span>
                )}
                {req.sent_at && (
                  <span>
                    📤 <strong>Envoyée le :</strong> {new Date(req.sent_at).toLocaleDateString("fr-FR")}
                  </span>
                )}
                {req.last_reminded_at && (
                  <span className="text-amber-600 dark:text-amber-400">
                    🔔 <strong>Dernière relance :</strong> {new Date(req.last_reminded_at).toLocaleDateString("fr-FR")}
                  </span>
                )}
              </div>

              {req.claim_text && (
                <p className="text-xs text-slate-600 dark:text-slate-300 italic bg-slate-50 p-2 rounded dark:bg-slate-800/60">
                  Allégation liée : &ldquo;{req.claim_text}&rdquo;
                </p>
              )}

              {/* Items checklist */}
              {req.requested_items.length > 0 && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {req.requested_items.map((item, idx) => (
                    <span
                      key={idx}
                      className="rounded bg-indigo-50 border border-indigo-100 px-2 py-0.5 text-[10px] font-medium text-indigo-700 dark:bg-indigo-950/40 dark:border-indigo-900 dark:text-indigo-300"
                    >
                      📄 {item.name}
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* Actions buttons */}
            <div className="flex items-center gap-2 self-end md:self-center shrink-0">
              {req.status === "draft" && (
                <button
                  type="button"
                  disabled={actionInProgress === req.id}
                  onClick={() => handleSend(req.id)}
                  className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
                >
                  📤 Envoyer
                </button>
              )}

              {req.status === "sent" && (
                <>
                  <button
                    type="button"
                    disabled={actionInProgress === req.id}
                    onClick={() => handleRemind(req.id)}
                    className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-800 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300 disabled:opacity-50"
                  >
                    🔔 Relancer
                  </button>
                  <button
                    type="button"
                    disabled={actionInProgress === req.id}
                    onClick={() => handleMarkFulfilled(req.id)}
                    className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    ✓ Reçue / Conforme
                  </button>
                </>
              )}

              {req.status === "draft" && (
                <button
                  type="button"
                  disabled={actionInProgress === req.id}
                  onClick={() => handleDelete(req.id)}
                  className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-500 hover:bg-rose-50 hover:text-rose-600 dark:border-slate-800 dark:hover:bg-rose-950/30"
                >
                  Supprimer
                </button>
              )}
            </div>
          </div>
        ))}

        {requests.length === 0 && !isLoading && (
          <div className="rounded-xl border border-dashed border-slate-300 p-8 text-center dark:border-slate-800">
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Aucune demande de preuve trouvée pour ces critères.
            </p>
            <button
              onClick={() => setIsModalOpen(true)}
              className="mt-3 text-xs font-semibold text-indigo-600 hover:underline dark:text-indigo-400"
            >
              + Créer une nouvelle demande
            </button>
          </div>
        )}
      </div>

      {/* New Request Modal */}
      {isModalOpen && (
        <SupplierEvidenceRequestModal
          isOpen={true}
          onClose={() => setIsModalOpen(false)}
          onCreated={() => loadData()}
        />
      )}
    </div>
  );
}
