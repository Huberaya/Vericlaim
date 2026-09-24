"use client";

import { useEffect, useState, type FormEvent } from "react";
import { verifyEcolabelLicense } from "@/lib/api";
import type { EvidenceItem } from "@/lib/types";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  onAdd: (item: EvidenceItem) => void;
};

type Kind = EvidenceItem["kind"];

const KIND_LABELS: Record<Kind, string> = {
  lca_report: "Rapport d’ACV",
  ecolabel_certificate: "Certificat / label officiel",
  recycling_route: "Filière de recyclage",
  ghg_inventory: "Bilan GES produit",
  ghg_reduction_plan: "Trajectoire de réduction",
  carbon_offset: "Crédits carbone / compensation",
  other: "Autre pièce",
};

export default function ProofUploadModal({ isOpen, onClose, onAdd }: Props) {
  const [kind, setKind] = useState<Kind>("lca_report");
  const [reference, setReference] = useState("");
  const [fileName, setFileName] = useState("");
  const [productScope, setProductScope] = useState("");
  const [lcaStandard, setLcaStandard] = useState("");
  const [inventoryStandard, setInventoryStandard] = useState("");
  const [functionalUnit, setFunctionalUnit] = useState("");
  const [systemBoundary, setSystemBoundary] = useState("");
  const [impactCategories, setImpactCategories] = useState("");
  const [comparative, setComparative] = useState(false);
  const [comparisonProduct, setComparisonProduct] = useState("");
  const [sameFunctionalUnit, setSameFunctionalUnit] = useState(false);
  const [sameSystemBoundary, setSameSystemBoundary] = useState(false);
  const [scheme, setScheme] = useState<"EU_ECOLABEL" | "EN_ISO_14024_TYPE_I" | "OTHER">("EU_ECOLABEL");
  const [licenseNumber, setLicenseNumber] = useState("");
  const [productIdentifier, setProductIdentifier] = useState("");
  const [productCategory, setProductCategory] = useState("");
  const [territories, setTerritories] = useState("");
  const [material, setMaterial] = useState("");
  const [collection, setCollection] = useState(false);
  const [sorting, setSorting] = useState(false);
  const [consumerAccess, setConsumerAccess] = useState(false);
  const [industrialProcessing, setIndustrialProcessing] = useState(false);
  const [includesDirect, setIncludesDirect] = useState(false);
  const [includesIndirect, setIncludesIndirect] = useState(false);
  const [lifecycleScope, setLifecycleScope] = useState("");
  const [publicUrl, setPublicUrl] = useState("");
  const [avoidance, setAvoidance] = useState(false);
  const [reduction, setReduction] = useState(false);
  const [annualTargets, setAnnualTargets] = useState(false);
  const [offsetStandard, setOffsetStandard] = useState("");
  const [retirementReference, setRetirementReference] = useState("");
  const [quantity, setQuantity] = useState("");
  const [residualReference, setResidualReference] = useState("");
  const [description, setDescription] = useState("");
  const [liveCheckResult, setLiveCheckResult] = useState<{
    verified: boolean;
    status?: string;
    message?: string;
    issuer?: string;
    registry_name?: string;
  } | null>(null);
  const [isCheckingLicense, setIsCheckingLicense] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setReference("");
    setFileName("");
    setLicenseNumber("");
    setDescription("");
  }, [isOpen]);

  if (!isOpen) return null;

  function addEvidence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const base = {
      evidence_id: `EV-${Date.now().toString(36).toUpperCase()}`,
      reference: reference.trim() || undefined,
      file_name: fileName.trim() || undefined,
      product_scope: productScope.trim() || undefined,
    };
    let item: EvidenceItem;

    if (kind === "lca_report") {
      item = {
        ...base,
        kind,
        standard: lcaStandard.trim() || undefined,
        functional_unit: functionalUnit.trim() || undefined,
        system_boundary: systemBoundary.trim() || undefined,
        impact_categories: impactCategories.split(",").map((part) => part.trim()).filter(Boolean),
        comparative,
        comparison_product: comparisonProduct.trim() || undefined,
        same_functional_unit: sameFunctionalUnit,
        same_system_boundary: sameSystemBoundary,
      };
    } else if (kind === "ecolabel_certificate") {
      if (!licenseNumber.trim()) return;
      item = {
        ...base,
        kind,
        scheme,
        license_number: licenseNumber.trim(),
        product_identifier: productIdentifier.trim() || undefined,
        product_category: productCategory.trim() || undefined,
      };
    } else if (kind === "recycling_route") {
      item = {
        ...base,
        kind,
        material_or_component: material.trim() || undefined,
        territories: territories.split(",").map((part) => part.trim()).filter(Boolean),
        collection_available: collection,
        sorting_available: sorting,
        consumer_access: consumerAccess,
        industrial_processing_available: industrialProcessing,
      };
    } else if (kind === "ghg_inventory") {
      item = {
        ...base,
        kind,
        standard: inventoryStandard.trim() || undefined,
        includes_direct_emissions: includesDirect,
        includes_indirect_emissions: includesIndirect,
        product_lifecycle_scope: lifecycleScope.trim() || undefined,
        public_disclosure_url: publicUrl.trim() || undefined,
      };
    } else if (kind === "ghg_reduction_plan") {
      item = {
        ...base,
        kind,
        avoidance_prioritised: avoidance,
        reduction_before_compensation: reduction,
        annual_quantified_targets: annualTargets,
        public_disclosure_url: publicUrl.trim() || undefined,
      };
    } else if (kind === "carbon_offset") {
      item = {
        ...base,
        kind,
        standard_or_registry: offsetStandard.trim() || undefined,
        retirement_reference: retirementReference.trim() || undefined,
        quantity_tco2e: quantity ? Number(quantity) : undefined,
        residual_emissions_reference: residualReference.trim() || undefined,
      };
    } else {
      item = { ...base, kind: "other", description: description.trim() || undefined };
    }

    onAdd(item);
    onClose();
  }

  const textField = (label: string, value: string, set: (next: string) => void, placeholder = "", full = false) => (
    <label className={`modal-field${full ? " full" : ""}`}>
      <span className="field-label">{label}</span>
      <input value={value} onChange={(event) => set(event.target.value)} placeholder={placeholder} />
    </label>
  );
  const checkbox = (label: string, value: boolean, set: (next: boolean) => void) => (
    <label className="modal-check"><input type="checkbox" checked={value} onChange={(event) => set(event.target.checked)} />{label}</label>
  );

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="modal" role="dialog" aria-modal="true" aria-labelledby="proof-modal-title">
        <div className="modal-head">
          <div>
            <h2 className="modal-title" id="proof-modal-title">Ajouter une preuve au dossier</h2>
            <p className="modal-subtitle">Déclarez les champs utiles à l’arbre d’inférence.</p>
          </div>
          <button className="modal-close" type="button" onClick={onClose} aria-label="Fermer">×</button>
        </div>
        <form onSubmit={addEvidence}>
          <div className="modal-body">
            <div className="modal-fields">
              <label className="modal-field full">
                <span className="field-label">Type de preuve</span>
                <select value={kind} onChange={(event) => setKind(event.target.value as Kind)}>
                  {(Object.keys(KIND_LABELS) as Kind[]).map((item) => <option key={item} value={item}>{KIND_LABELS[item]}</option>)}
                </select>
              </label>
              {textField("Référence / n° de rapport", reference, setReference, "EX. ACV-2025-014")}
              {textField("Nom de fichier (référence seulement)", fileName, setFileName, "rapport-acv.pdf")}
              {textField("Périmètre produit", productScope, setProductScope, "SKU, composant, gamme", true)}

              {kind === "lca_report" && <>
                {textField("Norme", lcaStandard, setLcaStandard, "ISO 14040/14044")}
                {textField("Unité fonctionnelle", functionalUnit, setFunctionalUnit, "1 unité emballée")}
                {textField("Frontières du système", systemBoundary, setSystemBoundary, "du berceau à la sortie usine", true)}
                {textField("Catégories d’impact (séparées par virgules)", impactCategories, setImpactCategories, "climat, eau, ressources", true)}
                <div className="modal-field full">{checkbox("ACV comparative", comparative, setComparative)}</div>
                {comparative && <>
                  {textField("Produit comparateur", comparisonProduct, setComparisonProduct, "Référence exacte")}
                  <div className="modal-field">
                    {checkbox("Même unité fonctionnelle", sameFunctionalUnit, setSameFunctionalUnit)}
                    {checkbox("Mêmes frontières du système", sameSystemBoundary, setSameSystemBoundary)}
                  </div>
                </>}
              </>}

              {kind === "ecolabel_certificate" && <>
                <label className="modal-field">
                  <span className="field-label">Schéma</span>
                  <select value={scheme} onChange={(event) => setScheme(event.target.value as typeof scheme)}>
                    <option value="EU_ECOLABEL">Écolabel européen</option>
                    <option value="EN_ISO_14024_TYPE_I">EN ISO 14024 Type I</option>
                    <option value="OTHER">Autre</option>
                  </select>
                </label>
                {textField("Numéro de licence *", licenseNumber, setLicenseNumber, "Ex. FR/012/345 ou NFE/75/001")}

                <div className="modal-field full" style={{ marginTop: "-2px", marginBottom: "8px" }}>
                  <button
                    type="button"
                    className="button button-secondary"
                    style={{ fontSize: "10px", padding: "4px 10px", width: "100%" }}
                    onClick={async () => {
                      if (!licenseNumber.trim()) return;
                      setIsCheckingLicense(true);
                      try {
                        const res = await verifyEcolabelLicense(
                          licenseNumber.trim(),
                          scheme,
                          productIdentifier.trim() || undefined,
                        );
                        setLiveCheckResult(res);
                      } catch {
                        setLiveCheckResult({
                          verified: false,
                          message: "Erreur de connexion au registre officiel.",
                        });
                      } finally {
                        setIsCheckingLicense(false);
                      }
                    }}
                    disabled={isCheckingLicense || !licenseNumber.trim()}
                  >
                    <span>{isCheckingLicense ? "⏳" : "🔍"}</span>
                    <span>{isCheckingLicense ? "Interrogation des registres officiels…" : "Vérifier en direct (Live ECAT / AFNOR / RAL)"}</span>
                  </button>

                  {liveCheckResult && (
                    <div
                      style={{
                        padding: "8px 12px",
                        borderRadius: "8px",
                        fontSize: "11px",
                        marginTop: "8px",
                        background: liveCheckResult.verified ? "#edf6e8" : "#fdf5e7",
                        border: `1px solid ${liveCheckResult.verified ? "#c9e4bf" : "#f6deb6"}`,
                        color: liveCheckResult.verified ? "#284c20" : "#845517",
                      }}
                    >
                      <strong>
                        {liveCheckResult.verified ? "✓ Licence officiellement corroborée" : "⚠ Licence non corroborée"}
                      </strong>
                      <div style={{ marginTop: "3px", fontSize: "10px", lineHeight: 1.4 }}>
                        {liveCheckResult.message}
                      </div>
                      {liveCheckResult.issuer && (
                        <div style={{ fontSize: "9px", marginTop: "4px", opacity: 0.9 }}>
                          Émetteur : {liveCheckResult.issuer}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {textField("Identifiant produit", productIdentifier, setProductIdentifier, "SKU-001")}
                {textField("Catégorie produit", productCategory, setProductCategory, "emballage")}
              </>}

              {kind === "recycling_route" && <>
                {textField("Composant / matériau", material, setMaterial, "Corps en PET")}
                {textField("Territoires (séparés par virgules)", territories, setTerritories, "France, Belgique", true)}
                <div className="modal-field full">
                  {checkbox("Collecte disponible", collection, setCollection)}
                  {checkbox("Accès consommateur démontré", consumerAccess, setConsumerAccess)}
                  {checkbox("Tri vers une filière identifiée", sorting, setSorting)}
                  {checkbox("Traitement industriel opérationnel", industrialProcessing, setIndustrialProcessing)}
                </div>
              </>}

              {kind === "ghg_inventory" && <>
                {textField("Norme / méthode", inventoryStandard, setInventoryStandard, "NF EN ISO 14067 ou méthode équivalente")}
                {textField("Périmètre de cycle de vie", lifecycleScope, setLifecycleScope, "fabrication et transport")}
                {textField("Lien public", publicUrl, setPublicUrl, "https://…", true)}
                <div className="modal-field full">
                  {checkbox("Émissions directes incluses", includesDirect, setIncludesDirect)}
                  {checkbox("Émissions indirectes incluses", includesIndirect, setIncludesIndirect)}
                </div>
              </>}

              {kind === "ghg_reduction_plan" && <>
                {textField("Lien public ou référence", publicUrl, setPublicUrl, "URL du plan")}
                <div className="modal-field full">
                  {checkbox("Évitement prioritaire documenté", avoidance, setAvoidance)}
                  {checkbox("Réductions avant compensation", reduction, setReduction)}
                  {checkbox("Objectifs annuels quantifiés", annualTargets, setAnnualTargets)}
                </div>
              </>}

              {kind === "carbon_offset" && <>
                {textField("Standard / registre", offsetStandard, setOffsetStandard, "VCS / Gold Standard")}
                {textField("Référence de retrait", retirementReference, setRetirementReference, "Retirement ID")}
                {textField("Quantité (tCO₂e)", quantity, setQuantity, "0")}
                {textField("Émissions résiduelles couvertes", residualReference, setResidualReference, "Lien vers le bilan")}
              </>}

              {kind === "other" && <label className="modal-field full">
                <span className="field-label">Description</span>
                <textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Décrire brièvement la pièce" />
              </label>}
            </div>
            <div className="modal-note">
              Les champs saisis ici sont des métadonnées déclaratives. Les pièces physiques ne sont pas téléversées par cette fenêtre; un numéro de licence ne devient un Safe Harbor que si le serveur le confirme dans un registre de confiance. Le moteur ne certifie pas le contenu d’une ACV ni la réalité d’une filière.
            </div>
            <div className="modal-foot">
              <button type="button" className="text-button" onClick={onClose}>Annuler</button>
              <button type="submit" className="primary-button">Ajouter au dossier <span aria-hidden="true">↗</span></button>
            </div>
          </div>
        </form>
      </section>
    </div>
  );
}
