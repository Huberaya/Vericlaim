"use client";

import { useEffect, useState } from "react";
import {
  createPersistentEvidence,
  deletePersistentEvidence,
  listPersistentEvidence,
} from "@/lib/api";
import type {
  CatalogProduct,
  CatalogSupplier,
  PersistentEvidence,
  PersistentEvidenceCreateRequest,
  PersistentEvidenceStatus,
  PersistentEvidenceType,
} from "@/lib/types";

type Props = {
  suppliers?: CatalogSupplier[];
  products?: CatalogProduct[];
  canManage: boolean;
};

const EVIDENCE_TYPE_LABELS: Record<PersistentEvidenceType, string> = {
  certificate: "Certificat / Écolabel",
  lca_report: "Rapport ACV (ISO 14040/44)",
  environmental_declaration: "Déclaration environnementale / FDES",
  lab_report: "Rapport d'essais laboratoire",
  recycling_route: "Attestation de filière recyclage",
  ghg_inventory: "Bilan GES / Émissions (Scopes 1-3)",
  ghg_reduction_plan: "Plan de réduction carbone & Décarbonation",
  carbon_offset: "Certificat de compensation carbone",
  standard: "Norme technique / Standard",
  other: "Autre justificatif",
};

const STATUS_CONFIG: Record<
  PersistentEvidenceStatus,
  { label: string; badgeClass: string; desc: string }
> = {
  present: {
    label: "Présente & Active",
    badgeClass: "bg-emerald-900/40 text-emerald-300 border-emerald-700",
    desc: "Justificatif fourni et en cours de validité",
  },
  verified: {
    label: "Vérifiée par tiers",
    badgeClass: "bg-blue-900/40 text-blue-300 border-blue-700",
    desc: "Contrôlée dans un registre tiers officiel",
  },
  partial: {
    label: "Partielle / À compléter",
    badgeClass: "bg-amber-900/40 text-amber-300 border-amber-700",
    desc: "Périmètre ou données incomplètes",
  },
  expired: {
    label: "Expirée",
    badgeClass: "bg-rose-900/40 text-rose-300 border-rose-700",
    desc: "Date de validité dépassée",
  },
  out_of_scope: {
    label: "Hors périmètre",
    badgeClass: "bg-purple-900/40 text-purple-300 border-purple-700",
    desc: "Ne couvre pas la référence ou l'allégation",
  },
  missing: {
    label: "Manquante",
    badgeClass: "bg-red-900/40 text-red-300 border-red-700",
    desc: "Aucune pièce fournie",
  },
  pending: {
    label: "En attente de revue",
    badgeClass: "bg-slate-800 text-slate-300 border-slate-600",
    desc: "Déclarée sans validation",
  },
  rejected: {
    label: "Rejetée",
    badgeClass: "bg-rose-950 text-rose-400 border-rose-800",
    desc: "Non conforme ou frauduleuse",
  },
};

export default function EvidenceRegistryPanel({
  suppliers = [],
  products = [],
  canManage,
}: Props) {
  const [items, setItems] = useState<PersistentEvidence[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<PersistentEvidenceType | "all">("all");
  const [statusFilter, setStatusFilter] = useState<PersistentEvidenceStatus | "all">("all");
  const [selectedSupplierId, setSelectedSupplierId] = useState<string>("all");
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Form State
  const [formData, setFormData] = useState<PersistentEvidenceCreateRequest>({
    evidence_type: "certificate",
    reference: "",
    issuer: "",
    issued_on: "",
    expires_on: "",
    product_scope: "",
    supplier_id: null,
    product_id: null,
    status: "present",
    evidence_metadata: {},
  });

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listPersistentEvidence({
        query: searchQuery || undefined,
        evidenceType: typeFilter !== "all" ? typeFilter : undefined,
        status: statusFilter !== "all" ? statusFilter : undefined,
        supplierId: selectedSupplierId !== "all" ? selectedSupplierId : undefined,
        limit: 50,
      });
      setItems(res.items);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Erreur de chargement des preuves");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [typeFilter, statusFilter, selectedSupplierId]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    loadData();
  };

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setError(null);
    try {
      await createPersistentEvidence({
        ...formData,
        supplier_id: formData.supplier_id || null,
        product_id: formData.product_id || null,
        reference: formData.reference?.trim() || null,
        issuer: formData.issuer?.trim() || null,
        product_scope: formData.product_scope?.trim() || null,
        issued_on: formData.issued_on || null,
        expires_on: formData.expires_on || null,
      });
      setIsCreateModalOpen(false);
      setFormData({
        evidence_type: "certificate",
        reference: "",
        issuer: "",
        issued_on: "",
        expires_on: "",
        product_scope: "",
        supplier_id: null,
        product_id: null,
        status: "present",
        evidence_metadata: {},
      });
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Échec de création de la preuve");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDelete = async (evidenceId: string) => {
    if (!confirm("Êtes-vous sûr de vouloir archiver cette preuve ?")) return;
    try {
      await deletePersistentEvidence(evidenceId);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Échec de suppression");
    }
  };

  return (
    <div className="space-y-6">
      {/* Header bar */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-slate-900 border border-slate-800 p-6 rounded-xl shadow-lg">
        <div>
          <div className="flex items-center gap-3">
            <span className="text-2xl">🛡️</span>
            <div>
              <h2 className="text-xl font-semibold text-white">Registre Probatoire Réglementaire</h2>
              <p className="text-sm text-slate-400">
                Conservation, traçabilité et vérification des certificats, ACV et pièces justificatives fournisseurs.
              </p>
            </div>
          </div>
        </div>
        {canManage && (
          <button
            onClick={() => setIsCreateModalOpen(true)}
            className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg font-medium transition flex items-center gap-2 shadow"
          >
            <span>+</span> Enregistrer une preuve
          </button>
        )}
      </div>

      {/* Filter and Search Bar */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3 bg-slate-900/60 p-4 border border-slate-800 rounded-lg">
        <div>
          <label className="block text-xs font-semibold text-slate-400 uppercase mb-1">Type de preuve</label>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value as PersistentEvidenceType | "all")}
            className="w-full bg-slate-800 border border-slate-700 text-slate-200 text-sm rounded-lg p-2 focus:ring-emerald-500 focus:border-emerald-500"
          >
            <option value="all">Tous les types</option>
            {Object.entries(EVIDENCE_TYPE_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-semibold text-slate-400 uppercase mb-1">Statut probatoire</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as PersistentEvidenceStatus | "all")}
            className="w-full bg-slate-800 border border-slate-700 text-slate-200 text-sm rounded-lg p-2 focus:ring-emerald-500 focus:border-emerald-500"
          >
            <option value="all">Tous les statuts</option>
            {Object.entries(STATUS_CONFIG).map(([k, v]) => (
              <option key={k} value={k}>{v.label}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-semibold text-slate-400 uppercase mb-1">Fournisseur</label>
          <select
            value={selectedSupplierId}
            onChange={(e) => setSelectedSupplierId(e.target.value)}
            className="w-full bg-slate-800 border border-slate-700 text-slate-200 text-sm rounded-lg p-2 focus:ring-emerald-500 focus:border-emerald-500"
          >
            <option value="all">Tous les fournisseurs</option>
            {suppliers.map((s) => (
              <option key={s.id} value={s.id}>{s.legal_name}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-semibold text-slate-400 uppercase mb-1">Recherche</label>
          <form onSubmit={handleSearch} className="flex gap-2">
            <input
              type="text"
              placeholder="Réf, organisme, périmètre..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 text-slate-200 text-sm rounded-lg p-2 focus:ring-emerald-500 focus:border-emerald-500"
            />
            <button
              type="submit"
              className="px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded-lg text-sm"
            >
              🔍
            </button>
          </form>
        </div>
      </div>

      {error && (
        <div className="p-4 bg-rose-950/60 border border-rose-800 text-rose-300 rounded-lg text-sm flex items-center justify-between">
          <span>⚠️ {error}</span>
          <button onClick={() => setError(null)} className="text-rose-400 hover:text-white">✕</button>
        </div>
      )}

      {/* Evidence Table / Grid */}
      {loading ? (
        <div className="p-12 text-center text-slate-400 bg-slate-900 border border-slate-800 rounded-xl">
          <div className="animate-spin w-8 h-8 border-4 border-emerald-500 border-t-transparent rounded-full mx-auto mb-3"></div>
          Chargement du registre probatoire...
        </div>
      ) : items.length === 0 ? (
        <div className="p-12 text-center bg-slate-900/60 border border-slate-800 rounded-xl text-slate-400">
          <p className="text-lg font-medium text-slate-300 mb-1">Aucune preuve trouvée</p>
          <p className="text-sm">Enregistrez un certificat ou un rapport pour justifier les allégations de vos produits.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {items.map((ev) => {
            const statusInfo = STATUS_CONFIG[ev.status] || STATUS_CONFIG.pending;
            const supplier = suppliers.find((s) => s.id === ev.supplier_id);
            const product = products.find((p) => p.id === ev.product_id);

            return (
              <div
                key={ev.id}
                className="bg-slate-900 border border-slate-800 p-5 rounded-xl hover:border-slate-700 transition flex flex-col justify-between shadow-md"
              >
                <div>
                  <div className="flex justify-between items-start gap-2 mb-2">
                    <span className="text-xs font-semibold px-2 py-1 rounded bg-slate-800 text-emerald-400 border border-slate-700">
                      {EVIDENCE_TYPE_LABELS[ev.evidence_type] || ev.evidence_type}
                    </span>
                    <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${statusInfo.badgeClass}`}>
                      {statusInfo.label}
                    </span>
                  </div>

                  <h3 className="text-base font-semibold text-white mb-1">
                    {ev.reference || "Référence non spécifiée"}
                  </h3>

                  {ev.issuer && (
                    <p className="text-xs text-slate-300 mb-2">
                      <span className="text-slate-500">Émetteur / Organisme :</span> {ev.issuer}
                    </p>
                  )}

                  {ev.product_scope && (
                    <div className="mb-3 p-2.5 bg-slate-950/70 rounded border border-slate-800 text-xs text-slate-300">
                      <span className="text-slate-500 block mb-0.5 font-medium">Périmètre couvert :</span>
                      {ev.product_scope}
                    </div>
                  )}

                  <div className="grid grid-cols-2 gap-2 text-xs text-slate-400 mb-3 pt-2 border-t border-slate-800/80">
                    <div>
                      <span className="text-slate-500">Émission :</span>{" "}
                      {ev.issued_on || "N/A"}
                    </div>
                    <div>
                      <span className="text-slate-500">Expiration :</span>{" "}
                      <span className={ev.status === "expired" ? "text-rose-400 font-semibold" : ""}>
                        {ev.expires_on || "Permanente"}
                      </span>
                    </div>
                    {supplier && (
                      <div className="col-span-2 truncate">
                        <span className="text-slate-500">Fournisseur :</span> {supplier.legal_name}
                      </div>
                    )}
                    {product && (
                      <div className="col-span-2 truncate">
                        <span className="text-slate-500">Produit lié :</span> {product.name} ({product.reference})
                      </div>
                    )}
                  </div>
                </div>

                <div className="flex justify-between items-center pt-3 border-t border-slate-800 text-xs text-slate-500">
                  <span>Enregistré le {new Date(ev.created_at).toLocaleDateString()}</span>
                  {canManage && (
                    <button
                      onClick={() => handleDelete(ev.id)}
                      className="text-rose-400 hover:text-rose-300 transition"
                    >
                      Archiver
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Creation Modal */}
      {isCreateModalOpen && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 p-4 overflow-y-auto">
          <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-lg w-full p-6 shadow-2xl my-8">
            <div className="flex justify-between items-center mb-4 pb-3 border-b border-slate-800">
              <h3 className="text-lg font-semibold text-white">Enregistrer une pièce justificative</h3>
              <button
                onClick={() => setIsCreateModalOpen(false)}
                className="text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleCreateSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1">
                  Type de justificatif *
                </label>
                <select
                  value={formData.evidence_type}
                  onChange={(e) =>
                    setFormData({ ...formData, evidence_type: e.target.value as PersistentEvidenceType })
                  }
                  className="w-full bg-slate-800 border border-slate-700 text-slate-200 text-sm rounded-lg p-2.5"
                  required
                >
                  {Object.entries(EVIDENCE_TYPE_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1">
                  Référence / Numéro de certificat *
                </label>
                <input
                  type="text"
                  placeholder="Ex : CERT-2026-EU-9988 ou ACV-ISO14044-V2"
                  value={formData.reference || ""}
                  onChange={(e) => setFormData({ ...formData, reference: e.target.value })}
                  className="w-full bg-slate-800 border border-slate-700 text-slate-200 text-sm rounded-lg p-2.5"
                  required
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1">
                  Organisme émetteur / Laboratoire
                </label>
                <input
                  type="text"
                  placeholder="Ex : AFNOR, TÜV, Bureau Veritas, Citeo, SGS..."
                  value={formData.issuer || ""}
                  onChange={(e) => setFormData({ ...formData, issuer: e.target.value })}
                  className="w-full bg-slate-800 border border-slate-700 text-slate-200 text-sm rounded-lg p-2.5"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-semibold text-slate-300 mb-1">Date d'émission</label>
                  <input
                    type="date"
                    value={formData.issued_on || ""}
                    onChange={(e) => setFormData({ ...formData, issued_on: e.target.value })}
                    className="w-full bg-slate-800 border border-slate-700 text-slate-200 text-sm rounded-lg p-2"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-slate-300 mb-1">Date d'expiration</label>
                  <input
                    type="date"
                    value={formData.expires_on || ""}
                    onChange={(e) => setFormData({ ...formData, expires_on: e.target.value })}
                    className="w-full bg-slate-800 border border-slate-700 text-slate-200 text-sm rounded-lg p-2"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-300 mb-1">
                  Périmètre couvert (produits, matières, emballages)
                </label>
                <textarea
                  placeholder="Ex : Concerne les flacons PET 500ml et bouchons PEHD recyclables..."
                  value={formData.product_scope || ""}
                  onChange={(e) => setFormData({ ...formData, product_scope: e.target.value })}
                  className="w-full bg-slate-800 border border-slate-700 text-slate-200 text-sm rounded-lg p-2.5 h-20"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-semibold text-slate-300 mb-1">Fournisseur lié</label>
                  <select
                    value={formData.supplier_id || ""}
                    onChange={(e) =>
                      setFormData({ ...formData, supplier_id: e.target.value || null, product_id: null })
                    }
                    className="w-full bg-slate-800 border border-slate-700 text-slate-200 text-sm rounded-lg p-2"
                  >
                    <option value="">Aucun (global)</option>
                    {suppliers.map((s) => (
                      <option key={s.id} value={s.id}>{s.legal_name}</option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-slate-300 mb-1">Produit lié</label>
                  <select
                    value={formData.product_id || ""}
                    onChange={(e) => setFormData({ ...formData, product_id: e.target.value || null })}
                    className="w-full bg-slate-800 border border-slate-700 text-slate-200 text-sm rounded-lg p-2"
                  >
                    <option value="">Aucun (toute la gamme)</option>
                    {products
                      .filter((p) => !formData.supplier_id || p.supplier_id === formData.supplier_id)
                      .map((p) => (
                        <option key={p.id} value={p.id}>{p.name} ({p.reference})</option>
                      ))}
                  </select>
                </div>
              </div>

              <div className="flex justify-end gap-3 pt-4 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setIsCreateModalOpen(false)}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-sm"
                >
                  Annuler
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white font-medium rounded-lg text-sm transition disabled:opacity-50"
                >
                  {isSubmitting ? "Enregistrement..." : "Enregistrer la preuve"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
