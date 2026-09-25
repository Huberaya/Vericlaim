"use client";

import { useEffect, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import PersistentAnalysisPanel from "@/components/PersistentAnalysisPanel";
import {
  createSecureDocumentDownloadUrl,
  getSecureDocument,
  listCatalogProducts,
  listCatalogSuppliers,
  retrySecureDocumentExtraction,
  storeSecureDocument,
} from "@/lib/api";
import type {
  CatalogProduct,
  CatalogSupplier,
  DocumentDownload,
  DocumentType,
  DocumentUploadCompletion,
  ExtractionStatus,
  StoredDocumentVersion,
} from "@/lib/types";

const MAX_FILE_BYTES = 15 * 1024 * 1024;

const DOCUMENT_TYPES: Array<{ value: DocumentType; label: string }> = [
  { value: "supplier_declaration", label: "Déclaration fournisseur" },
  { value: "product_sheet", label: "Fiche produit" },
  { value: "marketing_asset", label: "Support marketing" },
  { value: "certificate", label: "Certificat" },
  { value: "lca_report", label: "Rapport ACV" },
  { value: "environmental_declaration", label: "Déclaration environnementale" },
  { value: "lab_report", label: "Rapport de laboratoire" },
  { value: "other", label: "Autre pièce" },
];

const ACCEPTED_EXTENSIONS = new Set(["pdf", "txt", "png", "jpg", "jpeg", "tif", "tiff", "webp"]);

const EXTRACTION_COPY: Record<ExtractionStatus, { label: string; detail: string; tone: string }> = {
  pending: {
    label: "Extraction mise en file",
    detail: "Le worker isolé préparera le texte et les passages citables. La pièce propre reste téléchargeable.",
    tone: "pending",
  },
  running: {
    label: "Extraction en cours",
    detail: "Le worker traite la version propre ; ne fermez pas nécessairement cette page, le statut est suivi automatiquement.",
    tone: "running",
  },
  completed: {
    label: "Texte et passages extraits",
    detail: "L’extraction est disponible pour les prochains parcours d’analyse. Elle reste une aide à relire sur l’original.",
    tone: "completed",
  },
  review_required: {
    label: "Revue humaine de l’OCR requise",
    detail: "Une ou plusieurs pages ont été transcrites par OCR. Vérifiez visuellement les négations, chiffres, unités et symboles sur le document original.",
    tone: "review",
  },
  failed: {
    label: "Extraction à relancer",
    detail: "La version propre est conservée et reste téléchargeable. Un gestionnaire de documents peut demander une nouvelle tentative.",
    tone: "failed",
  },
};

type Props = {
  canManage: boolean;
  canRunAnalysis: boolean;
  canReadCatalog: boolean;
  catalogRevision: number;
};

function initialTitle(file: File): string {
  const title = file.name.replace(/\.[^.]+$/, "").trim();
  return title.length >= 2 ? title : "Document importé";
}

function validateFile(file: File): string | null {
  const extension = file.name.split(".").pop()?.toLowerCase() || "";
  if (!ACCEPTED_EXTENSIONS.has(extension)) return "Formats acceptés : PDF, TXT UTF-8, PNG, JPG, TIFF ou WEBP.";
  if (file.name.length > 500 || /["/\\\u0000]/.test(file.name)) return "Le nom du fichier contient un caractère non autorisé.";
  if (file.size === 0) return "Le fichier est vide.";
  if (file.size > MAX_FILE_BYTES) return "Le fichier dépasse la limite locale de 15 Mo.";
  return null;
}

function replaceTrackedVersion(
  previous: DocumentUploadCompletion | null,
  version: StoredDocumentVersion,
  document?: DocumentUploadCompletion["document"],
): DocumentUploadCompletion | null {
  if (!previous) return previous;
  return { ...previous, document: document ?? previous.document, version };
}

export default function SecureDocumentVault({ canManage, canRunAnalysis, canReadCatalog, catalogRevision }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [documentType, setDocumentType] = useState<DocumentType>("supplier_declaration");
  const [isDragging, setIsDragging] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [completed, setCompleted] = useState<DocumentUploadCompletion | null>(null);
  const [download, setDownload] = useState<DocumentDownload | null>(null);
  const [isRequestingDownload, setIsRequestingDownload] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [isRetryingExtraction, setIsRetryingExtraction] = useState(false);
  const [extractionRefreshError, setExtractionRefreshError] = useState<string | null>(null);
  const [suppliers, setSuppliers] = useState<CatalogSupplier[]>([]);
  const [products, setProducts] = useState<CatalogProduct[]>([]);
  const [supplierId, setSupplierId] = useState("");
  const [productId, setProductId] = useState("");
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [isCatalogLoading, setIsCatalogLoading] = useState(false);

  const trackedVersion = completed?.version ?? null;

  useEffect(() => {
    if (!canReadCatalog) {
      setSuppliers([]);
      setProducts([]);
      setSupplierId("");
      setProductId("");
      return undefined;
    }
    let cancelled = false;
    setIsCatalogLoading(true);
    setCatalogError(null);
    void Promise.all([listCatalogSuppliers(), listCatalogProducts()])
      .then(([nextSuppliers, nextProducts]) => {
        if (cancelled) return;
        setSuppliers(nextSuppliers);
        setProducts(nextProducts);
        setSupplierId((current) => current && nextSuppliers.some((supplier) => supplier.id === current) ? current : "");
        setProductId((current) => current && nextProducts.some((product) => product.id === current) ? current : "");
      })
      .catch((cause) => {
        if (cancelled) return;
        setCatalogError(cause instanceof Error ? cause.message : "Le catalogue n’a pas pu être chargé.");
      })
      .finally(() => {
        if (!cancelled) setIsCatalogLoading(false);
      });
    return () => { cancelled = true; };
  }, [canReadCatalog, catalogRevision]);

  useEffect(() => {
    if (!completed?.document.id || !trackedVersion || !["pending", "running"].includes(trackedVersion.extraction_status)) {
      return undefined;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const refresh = async () => {
      try {
        const detail = await getSecureDocument(completed.document.id);
        const freshVersion = detail.versions.find((candidate) => candidate.id === trackedVersion.id);
        if (cancelled || !freshVersion) return;
        setCompleted((previous) => replaceTrackedVersion(previous, freshVersion, detail.document));
        setExtractionRefreshError(null);
        if (["pending", "running"].includes(freshVersion.extraction_status)) {
          timer = setTimeout(() => void refresh(), 2_500);
        }
      } catch {
        if (cancelled) return;
        setExtractionRefreshError("Le statut d’extraction n’a pas pu être actualisé. Réessayez dans un instant.");
        timer = setTimeout(() => void refresh(), 5_000);
      }
    };

    timer = setTimeout(() => void refresh(), 1_000);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [completed?.document.id, trackedVersion?.id, trackedVersion?.extraction_status]);

  function chooseFile(nextFile: File) {
    const invalidReason = validateFile(nextFile);
    setCompleted(null);
    setDownload(null);
    setDownloadError(null);
    setExtractionRefreshError(null);
    setError(invalidReason);
    if (invalidReason) {
      setFile(null);
      return;
    }
    setFile(nextFile);
    setTitle(initialTitle(nextFile));
  }

  function onInputChange(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    if (selected) chooseFile(selected);
    event.target.value = "";
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);
    const selected = event.dataTransfer.files?.[0];
    if (selected) chooseFile(selected);
  }

  async function submit() {
    if (!file || isSubmitting || !canManage) return;
    if (title.trim().length < 2) {
      setError("Donnez un titre de deux caractères minimum à la pièce.");
      return;
    }
    setIsSubmitting(true);
    setError(null);
    setCompleted(null);
    setDownload(null);
    setDownloadError(null);
    setExtractionRefreshError(null);
    try {
      const result = await storeSecureDocument(file, {
        title,
        documentType,
        supplierId: supplierId || null,
        productId: productId || null,
        tags: ["import", "preuve"],
      });
      if (!result.version || result.upload.status !== "clean") {
        throw new Error("La pièce n’a pas reçu de verdict propre et n’est pas disponible.");
      }
      setCompleted(result);
      setFile(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "L’import sécurisé n’a pas pu être finalisé.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function requestDownload() {
    if (!trackedVersion || isRequestingDownload) return;
    setIsRequestingDownload(true);
    setDownloadError(null);
    try {
      setDownload(await createSecureDocumentDownloadUrl(trackedVersion.id));
    } catch (cause) {
      setDownloadError(cause instanceof Error ? cause.message : "Le lien de téléchargement n’a pas pu être créé.");
    } finally {
      setIsRequestingDownload(false);
    }
  }

  async function retryExtraction() {
    if (!trackedVersion || !canManage || isRetryingExtraction) return;
    setIsRetryingExtraction(true);
    setExtractionRefreshError(null);
    try {
      const retried = await retrySecureDocumentExtraction(trackedVersion.id);
      setCompleted((previous) => replaceTrackedVersion(previous, retried.version));
    } catch (cause) {
      setExtractionRefreshError(cause instanceof Error ? cause.message : "La relance de l’extraction a échoué.");
    } finally {
      setIsRetryingExtraction(false);
    }
  }

  const extraction = trackedVersion ? EXTRACTION_COPY[trackedVersion.extraction_status] : null;
  const availableProducts = supplierId ? products.filter((product) => product.supplier_id === supplierId) : [];

  return (
    <section className="surface-card secure-vault" id="documents" aria-labelledby="secure-vault-title">
      <div className="secure-vault-header">
        <div>
          <div className="section-eyebrow"><span className="step-chip">02</span> Pièce documentaire</div>
          <h2 id="secure-vault-title" className="card-title">Conserver une preuve fournisseur</h2>
          <p className="card-description">Quarantaine privée, contrôle MIME/signature, SHA-256 puis antivirus avant conservation.</p>
        </div>
        <span className="vault-security-label"><span aria-hidden="true">▣</span> QUARANTAINE + CLAMAV</span>
      </div>

      {!canManage ? (
        <div className="secure-vault-readonly">
          <strong>Accès en lecture</strong>
          <p>Votre rôle peut consulter les pièces autorisées, mais ne peut pas créer ou importer une nouvelle version.</p>
        </div>
      ) : (
        <div className="secure-vault-body">
          <div className="secure-vault-fields">
            <label className="vault-field vault-title-field">
              <span>TITRE DE LA PIÈCE</span>
              <input
                value={title}
                maxLength={500}
                disabled={isSubmitting}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Ex. Déclaration environnementale fournisseur"
              />
            </label>
            <label className="vault-field">
              <span>TYPE</span>
              <select value={documentType} disabled={isSubmitting} onChange={(event) => setDocumentType(event.target.value as DocumentType)}>
                {DOCUMENT_TYPES.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}
              </select>
            </label>
            {canReadCatalog && (
              <>
                <label className="vault-field">
                  <span>FOURNISSEUR <em>facultatif</em></span>
                  <select
                    value={supplierId}
                    disabled={isSubmitting || isCatalogLoading}
                    onChange={(event) => {
                      setSupplierId(event.target.value);
                      setProductId("");
                    }}
                  >
                    <option value="">Aucun rattachement</option>
                    {suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.trading_name || supplier.legal_name}</option>)}
                  </select>
                </label>
                <label className="vault-field">
                  <span>PRODUIT <em>facultatif</em></span>
                  <select
                    value={productId}
                    disabled={!supplierId || isSubmitting || isCatalogLoading}
                    onChange={(event) => setProductId(event.target.value)}
                  >
                    <option value="">Aucun rattachement</option>
                    {availableProducts.map((product) => <option key={product.id} value={product.id}>{product.reference} · {product.name}</option>)}
                  </select>
                </label>
              </>
            )}
          </div>
          {catalogError && <p className="inline-error vault-catalog-error" role="alert">Le rattachement catalogue est indisponible : {catalogError}</p>}
          {canReadCatalog && !catalogError && !isCatalogLoading && suppliers.length === 0 && (
            <p className="vault-catalog-note">Ajoutez un fournisseur dans le catalogue pour contextualiser les prochaines pièces et analyses.</p>
          )}

          <div
            className={`secure-drop-zone${isDragging ? " is-dragging" : ""}${isSubmitting ? " is-disabled" : ""}`}
            onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={onDrop}
          >
            <input
              ref={inputRef}
              className="visually-hidden-file"
              type="file"
              accept=".pdf,.txt,.png,.jpg,.jpeg,.tif,.tiff,.webp,application/pdf,text/plain,image/png,image/jpeg,image/tiff,image/webp"
              disabled={isSubmitting}
              onChange={onInputChange}
              aria-label="Choisir une pièce fournisseur à conserver"
            />
            <div className="vault-file-icon" aria-hidden="true">⌑</div>
            <div>
              <strong>{file ? file.name : "Déposez une pièce à conserver"}</strong>
              <p>{file ? `${(file.size / 1024).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} Ko · contrôle avant promotion` : "PDF, TXT UTF-8, PNG, JPG, TIFF ou WEBP · 15 Mo maximum"}</p>
            </div>
            <button type="button" className="button button-secondary choose-file" disabled={isSubmitting} onClick={() => inputRef.current?.click()}>
              Choisir un fichier
            </button>
          </div>

          <div className="vault-submit-row">
            <p>La pièce n’est pas une preuve juridique vérifiée : le scan antivirus ne certifie ni son origine ni son contenu.</p>
            <button type="button" className="button button-primary" disabled={!file || isSubmitting} onClick={() => void submit()}>
              {isSubmitting ? <><span className="button-spinner" aria-hidden="true" /> Contrôle en cours…</> : <><span aria-hidden="true">⌁</span> Mettre en quarantaine</>}
            </button>
          </div>
          {error && <p className="inline-error" role="alert">{error}</p>}
        </div>
      )}

      {trackedVersion && completed && extraction && (
        <div className="vault-complete" role="status">
          <span className="vault-complete-icon" aria-hidden="true">✓</span>
          <div>
            <strong>Version {trackedVersion.version_number} promue après contrôle propre</strong>
            <p>{completed.document.document_key} · SHA-256 {trackedVersion.sha256.slice(0, 16)}… · moteur {completed.upload.scan_engine || "antivirus"}</p>
            <div className={`vault-extraction-status vault-extraction-${extraction.tone}`}>
              <strong>{extraction.label}</strong>
              <p>{extraction.detail}</p>
              {trackedVersion.extraction_job && (
                <small>
                  Job {trackedVersion.extraction_job.status} · tentative {trackedVersion.extraction_job.attempt_count}/{trackedVersion.extraction_job.max_attempts}
                </small>
              )}
              {trackedVersion.extraction_status === "failed" && canManage && (
                <button type="button" className="button button-secondary" disabled={isRetryingExtraction} onClick={() => void retryExtraction()}>
                  {isRetryingExtraction ? "Relance en cours…" : "Relancer l’extraction"}
                </button>
              )}
              {extractionRefreshError && <span className="vault-status-error" role="alert">{extractionRefreshError}</span>}
            </div>
            <div className="vault-download-action">
              {download ? (
                <a className="button button-secondary" href={download.url} target="_blank" rel="noopener noreferrer">
                  Télécharger avant {new Intl.DateTimeFormat("fr-FR", { timeStyle: "short", timeZone: "Europe/Paris" }).format(new Date(download.expires_at))} (Paris)
                </a>
              ) : (
                <button type="button" className="button button-secondary" disabled={isRequestingDownload} onClick={() => void requestDownload()}>
                  {isRequestingDownload ? "Création du lien…" : "Obtenir le lien de téléchargement"}
                </button>
              )}
              {downloadError && <span role="alert">{downloadError}</span>}
            </div>
            {["completed", "review_required"].includes(trackedVersion.extraction_status) && (
              <PersistentAnalysisPanel
                version={trackedVersion}
                canRun={canRunAnalysis}
                supplierId={completed.document.supplier_id}
                productId={completed.document.product_id}
              />
            )}
          </div>
          <span className="vault-clean-badge">CLEAN</span>
        </div>
      )}
    </section>
  );
}
