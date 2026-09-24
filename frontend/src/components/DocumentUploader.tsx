"use client";

import { useRef, useState, type DragEvent, type ChangeEvent } from "react";
import type { EvidenceItem } from "@/lib/types";

type Props = {
  isLoading: boolean;
  error?: string | null;
  onAuditText: (text: string, hasLcaAttached: boolean, additionalEvidence?: EvidenceItem[]) => void | Promise<void>;
  onAuditFile: (file: File, hasLcaAttached: boolean) => void | Promise<void>;
  onAuditUrl?: (url: string, hasLcaAttached: boolean, renderJs?: boolean) => void | Promise<void>;
  onOpenEvidence: () => void;
};

type Tab = "document" | "text" | "url";

const MAX_FILE_BYTES = 15 * 1024 * 1024;

const DEMO_CRITICAL =
  "Packaging 100% biodégradable et neutre en carbone grâce à la compensation de nos forêts.";

const DEMO_DECEPTIVE_LEXICON =
  "Nettoyant ménager zéro déchet, formule 100% sans produits chimiques, emballage en plastique recyclé et barquette compostable.";

const DEMO_COMPLIANT =
  "Réduction de 28% des émissions CO2 (ACV ISO 14044, cabinet tiers 2023) - Certifié Ecolabel Européen (licence FR/012/345).";

// The sample contains only the facts written in its copy. The API registry still has to
// corroborate the label, while the missing ACV details intentionally remain unverified.
const DEMO_DECLARED_EVIDENCE: EvidenceItem[] = [
  {
    kind: "lca_report",
    reference: "ACV ISO 14044 — cabinet tiers 2023 (mention de démonstration)",
    standard: "ISO 14044",
  },
  {
    kind: "ecolabel_certificate",
    scheme: "EU_ECOLABEL",
    license_number: "FR/012/345",
    product_category: "packaging",
  },
];

export default function DocumentUploader({
  isLoading,
  error,
  onAuditText,
  onAuditFile,
  onAuditUrl,
  onOpenEvidence,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<Tab>("document");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [renderJs, setRenderJs] = useState(true);
  const [hasLcaAttached, setHasLcaAttached] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  function submitText(value = text, lcaDeclared = hasLcaAttached, evidence?: EvidenceItem[]) {
    const normalized = value.trim();
    if (!normalized) {
      setLocalError("Collez un texte ou choisissez un échantillon avant de lancer l’audit.");
      return;
    }
    setLocalError(null);
    setText(value);
    void onAuditText(normalized, lcaDeclared, evidence);
  }

  function submitUrl(targetUrl = url) {
    const trimmed = targetUrl.trim();
    if (!trimmed) {
      setLocalError("Saisissez l'URL d'une page produit e-commerce à auditer.");
      return;
    }
    if (!/^https?:\/\//i.test(trimmed)) {
      setLocalError("L'URL doit commencer par http:// ou https://.");
      return;
    }
    setLocalError(null);
    setUrl(trimmed);
    if (onAuditUrl) {
      void onAuditUrl(trimmed, hasLcaAttached, renderJs);
    }
  }

  function submitFile(file: File) {
    if (isLoading) return;
    setLocalError(null);
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (extension !== "pdf" && extension !== "txt") {
      setLocalError("Format non pris en charge. Déposez un fichier PDF ou TXT.");
      return;
    }
    if (file.size === 0) {
      setLocalError("Le fichier est vide.");
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      setLocalError("Le fichier dépasse la limite de 15 Mo.");
      return;
    }
    setFileName(file.name);
    void onAuditFile(file, hasLcaAttached);
  }

  function onInputChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) submitFile(file);
    event.target.value = "";
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file) submitFile(file);
  }

  function runDemo(kind: "critical" | "compliant" | "deceptive") {
    const isCompliantDemo = kind === "compliant";
    const demoText = kind === "compliant" ? DEMO_COMPLIANT : kind === "deceptive" ? DEMO_DECEPTIVE_LEXICON : DEMO_CRITICAL;
    const lcaDeclared = isCompliantDemo;
    setTab("text");
    setText(demoText);
    setFileName(null);
    setHasLcaAttached(lcaDeclared);
    setLocalError(null);
    submitText(demoText, lcaDeclared, isCompliantDemo ? DEMO_DECLARED_EVIDENCE : []);
  }

  return (
    <section className="surface-card uploader-card" aria-labelledby="uploader-title">
      <div className="card-heading">
        <div>
          <div className="section-eyebrow"><span className="step-chip">01</span> Entrée d’audit</div>
          <h2 id="uploader-title" className="card-title">Texte ou document produit</h2>
          <p className="card-description">Analysez une fiche, un emballage ou une communication commerciale.</p>
        </div>
        <span className="engine-tag"><span className="pulse-dot" /> API déterministe</span>
      </div>

      <div className="uploader-tabs" role="tablist" aria-label="Mode d’entrée">
        <button
          type="button"
          className={`uploader-tab${tab === "document" ? " is-active" : ""}`}
          role="tab"
          aria-selected={tab === "document"}
          onClick={() => setTab("document")}
        >
          <span aria-hidden="true">↥</span> Déposer un fichier
        </button>
        <button
          type="button"
          className={`uploader-tab${tab === "text" ? " is-active" : ""}`}
          role="tab"
          aria-selected={tab === "text"}
          onClick={() => setTab("text")}
        >
          <span aria-hidden="true">≡</span> Coller un texte
        </button>
        <button
          type="button"
          className={`uploader-tab${tab === "url" ? " is-active" : ""}`}
          role="tab"
          aria-selected={tab === "url"}
          onClick={() => setTab("url")}
        >
          <span aria-hidden="true">🌐</span> Scraper une URL
        </button>
      </div>

      {tab === "document" ? (
        <div
          className={`drop-zone${isDragging ? " is-dragging" : ""}${isLoading ? " is-disabled" : ""}`}
          onDragOver={(event) => { event.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={onDrop}
          aria-label="Zone de dépôt PDF ou TXT"
        >
          <input
            ref={inputRef}
            className="visually-hidden-file"
            type="file"
            accept=".pdf,.txt,application/pdf,text/plain"
            onChange={onInputChange}
            disabled={isLoading}
            aria-label="Choisir un fichier PDF ou TXT"
          />
          <div className="drop-icon" aria-hidden="true"><span>↑</span></div>
          <div className="drop-title">Glissez votre fichier ici</div>
          <div className="drop-caption">PDF ou TXT · 15 Mo maximum · OCR disponible pour les PDF numérisés</div>
          {fileName && <div className="selected-file"><span aria-hidden="true">▤</span>{fileName}</div>}
          <button type="button" className="button button-secondary choose-file" onClick={() => inputRef.current?.click()} disabled={isLoading}>
            Parcourir les fichiers
          </button>
        </div>
      ) : tab === "text" ? (
        <div className="paste-panel">
          <label htmlFor="source-text" className="field-caption">Texte marketing à auditer</label>
          <textarea
            id="source-text"
            className="source-textarea"
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Collez ici une allégation, une fiche produit ou un extrait d’emballage…"
            maxLength={100000}
            disabled={isLoading}
          />
          <div className="textarea-footer"><span>{text.length.toLocaleString("fr-FR")} caractères</span><span>Langue détectée : français (déclarative)</span></div>
          <button type="button" className="button button-primary analyze-button" onClick={() => submitText()} disabled={isLoading}>
            {isLoading ? <span className="button-spinner" aria-hidden="true" /> : <span aria-hidden="true">✦</span>}
            {isLoading ? "Analyse en cours…" : "Lancer l’audit"}
          </button>
        </div>
      ) : (
        <div className="paste-panel url-scraper-panel">
          <label htmlFor="source-url" className="field-caption">URL de la fiche produit e-commerce (Shopify, WooCommerce, Amazon...)</label>
          <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.75rem" }}>
            <input
              id="source-url"
              type="url"
              className="source-textarea"
              style={{ minHeight: "44px", height: "44px", padding: "0.5rem 0.75rem" }}
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://votre-boutique.com/produits/shampoing-naturel"
              disabled={isLoading}
            />
          </div>

          <div style={{ marginBottom: "1rem" }}>
            <span style={{ fontSize: "0.75rem", color: "var(--color-text-muted, #64748b)", display: "block", marginBottom: "0.35rem" }}>
              Boutiques de démonstration à tester sans réseau :
            </span>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
              <button
                type="button"
                className="button button-quiet"
                style={{ fontSize: "0.75rem", padding: "0.25rem 0.5rem" }}
                onClick={() => {
                  const demo = "https://demo-shop.vericlaim.ai/produit/gourde-verte";
                  setUrl(demo);
                  submitUrl(demo);
                }}
                disabled={isLoading}
              >
                Gourde Nomade (Greenwashing)
              </button>
              <button
                type="button"
                className="button button-quiet"
                style={{ fontSize: "0.75rem", padding: "0.25rem 0.5rem" }}
                onClick={() => {
                  const demo = "https://demo-shop.vericlaim.ai/produit/shampoing-solide-naturel";
                  setUrl(demo);
                  submitUrl(demo);
                }}
                disabled={isLoading}
              >
                Shampoing Solide (Sans chimie)
              </button>
              <button
                type="button"
                className="button button-quiet"
                style={{ fontSize: "0.75rem", padding: "0.25rem 0.5rem" }}
                onClick={() => {
                  const demo = "https://demo-shop.vericlaim.ai/produit/spa-react-eco-creme";
                  setUrl(demo);
                  submitUrl(demo);
                }}
                disabled={isLoading}
              >
                Crème SPA Next.js (Headless)
              </button>
            </div>
          </div>

          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
              fontSize: "0.8rem",
              color: "var(--color-text-default, #1e293b)",
              marginBottom: "1rem",
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              checked={renderJs}
              onChange={(e) => setRenderJs(e.target.checked)}
              disabled={isLoading}
            />
            <span>
              <strong>Rendu JavaScript dynamique actif</strong> (Next.js, Shopify Hydrogen, JSON-LD, SPAs)
            </span>
          </label>

          <div className="textarea-footer">
            <span>Bouclier anti-SSRF actif</span>
            <span>Extraction hybride : DOM statique + JSON-LD + State Hydraté</span>
          </div>

          <button
            type="button"
            className="button button-primary analyze-button"
            onClick={() => submitUrl()}
            disabled={isLoading}
          >
            {isLoading ? <span className="button-spinner" aria-hidden="true" /> : <span aria-hidden="true">✦</span>}
            {isLoading ? "Extraction et audit en cours…" : "Scanner & Auditer la page"}
          </button>
        </div>
      )}

      <label className="evidence-toggle">
        <input
          type="checkbox"
          checked={hasLcaAttached}
          onChange={(event) => setHasLcaAttached(event.target.checked)}
          disabled={isLoading}
        />
        <span className="checkbox-mark" aria-hidden="true" />
        <span className="toggle-copy">
          <strong>Rapport ACV (ISO 14044) fourni par le fournisseur</strong>
          <small>Déclaration uniquement : les métadonnées et le contenu restent à vérifier.</small>
        </span>
      </label>

      <div className="uploader-bottom-row">
        <button type="button" className="button button-quiet" onClick={onOpenEvidence} disabled={isLoading}>
          <span aria-hidden="true">＋</span> Ajouter une preuve structurée
        </button>
        <div className="accepted-types"><span>PDF</span><span>TXT</span><span>OCR</span></div>
      </div>

      {localError && <p className="inline-error" role="alert">{localError}</p>}
      {error && <p className="inline-error" role="alert">{error}</p>}

      <div className="demo-section">
        <div className="demo-heading"><span>TESTER SANS DOCUMENT</span><span className="demo-divider" /></div>
        <div className="demo-buttons">
          <button type="button" className="demo-button demo-danger" onClick={() => runDemo("critical")} disabled={isLoading}>
            <span className="demo-icon" aria-hidden="true">!</span>
            <span><strong>Exemple Greenwashing critique</strong><small>Allégation AGEC + compensation carbone</small></span>
            <span className="demo-arrow" aria-hidden="true">↗</span>
          </button>
          <button type="button" className="demo-button demo-danger" onClick={() => runDemo("deceptive")} disabled={isLoading}>
            <span className="demo-icon" aria-hidden="true">⌕</span>
            <span><strong>Allégations trompeuses</strong><small>Sans chimie · Zéro déchet · Plastique recyclé</small></span>
            <span className="demo-arrow" aria-hidden="true">↗</span>
          </button>
          <button type="button" className="demo-button demo-safe" onClick={() => runDemo("compliant")} disabled={isLoading}>
            <span className="demo-icon" aria-hidden="true">✓</span>
            <span><strong>Exemple Conforme</strong><small>Dossier déclaré · vérification API requise</small></span>
            <span className="demo-arrow" aria-hidden="true">↗</span>
          </button>
        </div>
        <p className="demo-disclaimer">La deuxième démo ne préjuge pas du verdict : un numéro de licence fourni seul n’est pas vérifié par le registre serveur et l’ACV citée peut rester incomplète.</p>
      </div>
    </section>
  );
}
