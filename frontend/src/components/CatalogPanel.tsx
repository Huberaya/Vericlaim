"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  archiveCatalogProduct,
  archiveCatalogSupplier,
  createCatalogProduct,
  createCatalogSupplier,
  listCatalogProducts,
  listCatalogSuppliers,
  updateCatalogProduct,
  updateCatalogSupplier,
} from "@/lib/api";
import type { CatalogProduct, CatalogProductLifecycle, CatalogSupplier } from "@/lib/types";

type Props = {
  canRead: boolean;
  canManage: boolean;
  onCatalogChanged: () => void;
};

type SupplierEdit = {
  id: string;
  legalName: string;
  tradingName: string;
  externalReference: string;
  countryCode: string;
};

type ProductEdit = {
  id: string;
  name: string;
  category: string;
  countryOfSale: string;
  lifecycleStatus: CatalogProductLifecycle;
};

function asOptional(value: FormDataEntryValue | null): string | undefined {
  const normalised = String(value ?? "").trim();
  return normalised || undefined;
}

export default function CatalogPanel({ canRead, canManage, onCatalogChanged }: Props) {
  const [suppliers, setSuppliers] = useState<CatalogSupplier[]>([]);
  const [products, setProducts] = useState<CatalogProduct[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isMutating, setIsMutating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [productSupplierId, setProductSupplierId] = useState("");
  const [editingSupplier, setEditingSupplier] = useState<SupplierEdit | null>(null);
  const [editingProduct, setEditingProduct] = useState<ProductEdit | null>(null);

  const refresh = useCallback(async () => {
    if (!canRead) {
      setSuppliers([]);
      setProducts([]);
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const [nextSuppliers, nextProducts] = await Promise.all([listCatalogSuppliers(), listCatalogProducts()]);
      setSuppliers(nextSuppliers);
      setProducts(nextProducts);
      setProductSupplierId((current) => current && nextSuppliers.some((supplier) => supplier.id === current) ? current : "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Le catalogue n’a pas pu être chargé.");
    } finally {
      setIsLoading(false);
    }
  }, [canRead]);

  useEffect(() => { void refresh(); }, [refresh]);

  async function afterMutation(message: string) {
    await refresh();
    setNotice(message);
    onCatalogChanged();
  }

  async function submitSupplier(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canManage || isMutating) return;
    const data = new FormData(event.currentTarget);
    setIsMutating(true);
    setError(null);
    setNotice(null);
    try {
      const result = await createCatalogSupplier({
        legal_name: String(data.get("legal_name") ?? ""),
        trading_name: asOptional(data.get("trading_name")),
        external_reference: asOptional(data.get("external_reference")),
        country_code: asOptional(data.get("country_code")),
        contact_email: asOptional(data.get("contact_email")),
        metadata: {},
      });
      event.currentTarget.reset();
      await afterMutation(result.idempotent_replay ? "Le fournisseur existant a été retrouvé sans doublon." : "Fournisseur ajouté au catalogue.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Le fournisseur n’a pas pu être ajouté.");
    } finally {
      setIsMutating(false);
    }
  }

  async function submitProduct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canManage || isMutating || !productSupplierId) return;
    const data = new FormData(event.currentTarget);
    setIsMutating(true);
    setError(null);
    setNotice(null);
    try {
      const result = await createCatalogProduct({
        supplier_id: productSupplierId,
        reference: String(data.get("reference") ?? ""),
        name: String(data.get("name") ?? ""),
        category: asOptional(data.get("category")),
        country_of_sale: asOptional(data.get("country_of_sale")),
        lifecycle_status: "active",
        metadata: {},
      });
      event.currentTarget.reset();
      await afterMutation(result.idempotent_replay ? "Le produit existant a été retrouvé sans doublon." : "Produit ajouté au catalogue.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Le produit n’a pas pu être ajouté.");
    } finally {
      setIsMutating(false);
    }
  }

  async function saveSupplierEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingSupplier || isMutating) return;
    setIsMutating(true);
    setError(null);
    try {
      await updateCatalogSupplier(editingSupplier.id, {
        legal_name: editingSupplier.legalName,
        trading_name: editingSupplier.tradingName || null,
        external_reference: editingSupplier.externalReference || null,
        country_code: editingSupplier.countryCode || null,
      });
      setEditingSupplier(null);
      await afterMutation("Fournisseur mis à jour.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Le fournisseur n’a pas pu être mis à jour.");
    } finally {
      setIsMutating(false);
    }
  }

  async function saveProductEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingProduct || isMutating) return;
    setIsMutating(true);
    setError(null);
    try {
      await updateCatalogProduct(editingProduct.id, {
        name: editingProduct.name,
        category: editingProduct.category || null,
        country_of_sale: editingProduct.countryOfSale || null,
        lifecycle_status: editingProduct.lifecycleStatus,
      });
      setEditingProduct(null);
      await afterMutation("Produit mis à jour.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Le produit n’a pas pu être mis à jour.");
    } finally {
      setIsMutating(false);
    }
  }

  async function archiveSupplier(supplier: CatalogSupplier) {
    if (isMutating || !window.confirm(`Archiver ${supplier.trading_name || supplier.legal_name} ? Les produits actifs doivent être archivés au préalable.`)) return;
    setIsMutating(true);
    setError(null);
    try {
      await archiveCatalogSupplier(supplier.id);
      await afterMutation("Fournisseur archivé. Les documents et analyses historiques restent inchangés.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Le fournisseur n’a pas pu être archivé.");
    } finally {
      setIsMutating(false);
    }
  }

  async function archiveProduct(product: CatalogProduct) {
    if (isMutating || !window.confirm(`Archiver ${product.reference} ? Les documents et analyses historiques resteront rattachés à cet identifiant.`)) return;
    setIsMutating(true);
    setError(null);
    try {
      await archiveCatalogProduct(product.id);
      await afterMutation("Produit archivé. Les rattachements historiques sont conservés.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Le produit n’a pas pu être archivé.");
    } finally {
      setIsMutating(false);
    }
  }

  if (!canRead) return null;

  return (
    <section className="surface-card catalog-panel" id="catalog" aria-labelledby="catalog-title">
      <div className="catalog-header">
        <div>
          <div className="section-eyebrow"><span className="step-chip">01</span> Catalogue achats</div>
          <h2 className="card-title" id="catalog-title">Fournisseurs et produits</h2>
          <p className="card-description">Des identifiants tenant-scoped pour contextualiser les pièces et analyses. Aucun score fournisseur ni enrichissement externe n’est produit.</p>
        </div>
        <span className="catalog-security-label">TENANT-SCOPED · AUDITÉ</span>
      </div>

      {error && <p className="inline-error catalog-message" role="alert">{error}</p>}
      {notice && <p className="catalog-notice" role="status">{notice}</p>}

      {canManage ? (
        <div className="catalog-create-grid">
          <form className="catalog-form" onSubmit={submitSupplier}>
            <h3>Ajouter un fournisseur</h3>
            <label><span>Raison sociale *</span><input name="legal_name" required minLength={2} maxLength={255} disabled={isMutating} placeholder="Ex. Fournisseur Europe SAS" /></label>
            <div className="catalog-form-two-columns">
              <label><span>Nom commercial</span><input name="trading_name" maxLength={255} disabled={isMutating} placeholder="Marque / enseigne" /></label>
              <label><span>Référence interne</span><input name="external_reference" maxLength={128} disabled={isMutating} placeholder="SUP-001" /></label>
            </div>
            <div className="catalog-form-two-columns">
              <label><span>Pays (ISO)</span><input name="country_code" maxLength={2} disabled={isMutating} placeholder="FR" /></label>
              <label><span>E-mail de contact</span><input name="contact_email" type="email" maxLength={320} disabled={isMutating} placeholder="contact@fournisseur.example" /></label>
            </div>
            <button className="button button-primary" type="submit" disabled={isMutating}>{isMutating ? "Enregistrement…" : "Ajouter le fournisseur"}</button>
          </form>

          <form className="catalog-form" onSubmit={submitProduct}>
            <h3>Ajouter un produit</h3>
            <label><span>Fournisseur *</span><select value={productSupplierId} required disabled={isMutating || suppliers.length === 0} onChange={(event) => setProductSupplierId(event.target.value)}><option value="">Choisir un fournisseur</option>{suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.trading_name || supplier.legal_name}</option>)}</select></label>
            <div className="catalog-form-two-columns">
              <label><span>Référence *</span><input name="reference" required maxLength={128} disabled={isMutating || !productSupplierId} placeholder="SKU-001" /></label>
              <label><span>Nom *</span><input name="name" required minLength={2} maxLength={255} disabled={isMutating || !productSupplierId} placeholder="Nom du produit" /></label>
            </div>
            <div className="catalog-form-two-columns">
              <label><span>Catégorie</span><input name="category" maxLength={128} disabled={isMutating || !productSupplierId} placeholder="Emballage" /></label>
              <label><span>Pays de vente (ISO)</span><input name="country_of_sale" maxLength={2} disabled={isMutating || !productSupplierId} placeholder="FR" /></label>
            </div>
            <button className="button button-primary" type="submit" disabled={isMutating || !productSupplierId}>{isMutating ? "Enregistrement…" : "Ajouter le produit"}</button>
          </form>
        </div>
      ) : (
        <div className="catalog-readonly"><strong>Accès en lecture</strong><span>Votre rôle peut consulter le contexte catalogue sans le modifier.</span></div>
      )}

      <div className="catalog-lists" aria-busy={isLoading}>
        <section className="catalog-list" aria-labelledby="supplier-list-title">
          <div className="catalog-list-head"><h3 id="supplier-list-title">Fournisseurs</h3><span>{suppliers.length}</span></div>
          {isLoading ? <p className="catalog-empty">Chargement du catalogue…</p> : suppliers.length === 0 ? <p className="catalog-empty">Aucun fournisseur déclaré.</p> : (
            <ul>
              {suppliers.map((supplier) => (
                <li key={supplier.id}>
                  {editingSupplier?.id === supplier.id ? (
                    <form className="catalog-inline-edit" onSubmit={saveSupplierEdit}>
                      <input aria-label="Raison sociale" value={editingSupplier.legalName} onChange={(event) => setEditingSupplier({ ...editingSupplier, legalName: event.target.value })} disabled={isMutating} />
                      <input aria-label="Nom commercial" value={editingSupplier.tradingName} onChange={(event) => setEditingSupplier({ ...editingSupplier, tradingName: event.target.value })} disabled={isMutating} placeholder="Nom commercial" />
                      <input aria-label="Référence interne" value={editingSupplier.externalReference} onChange={(event) => setEditingSupplier({ ...editingSupplier, externalReference: event.target.value })} disabled={isMutating} placeholder="Référence interne" />
                      <input aria-label="Pays" value={editingSupplier.countryCode} onChange={(event) => setEditingSupplier({ ...editingSupplier, countryCode: event.target.value })} disabled={isMutating} placeholder="FR" maxLength={2} />
                      <span className="catalog-inline-actions"><button type="submit" className="text-button" disabled={isMutating}>Enregistrer</button><button type="button" className="text-button" disabled={isMutating} onClick={() => setEditingSupplier(null)}>Annuler</button></span>
                    </form>
                  ) : (
                    <>
                      <span className="catalog-row-main"><strong>{supplier.trading_name || supplier.legal_name}</strong><small>{supplier.trading_name ? supplier.legal_name : supplier.external_reference || "Référence non renseignée"}{supplier.country_code ? ` · ${supplier.country_code}` : ""}</small></span>
                      {canManage && <span className="catalog-row-actions"><button type="button" className="text-button" onClick={() => setEditingSupplier({ id: supplier.id, legalName: supplier.legal_name, tradingName: supplier.trading_name || "", externalReference: supplier.external_reference || "", countryCode: supplier.country_code || "" })}>Modifier</button><button type="button" className="text-button catalog-archive-button" disabled={isMutating} onClick={() => void archiveSupplier(supplier)}>Archiver</button></span>}
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="catalog-list" aria-labelledby="product-list-title">
          <div className="catalog-list-head"><h3 id="product-list-title">Produits</h3><span>{products.length}</span></div>
          {isLoading ? <p className="catalog-empty">Chargement du catalogue…</p> : products.length === 0 ? <p className="catalog-empty">Aucun produit déclaré.</p> : (
            <ul>
              {products.map((product) => {
                const supplier = suppliers.find((item) => item.id === product.supplier_id);
                return (
                  <li key={product.id}>
                    {editingProduct?.id === product.id ? (
                      <form className="catalog-inline-edit" onSubmit={saveProductEdit}>
                        <input aria-label="Nom du produit" value={editingProduct.name} onChange={(event) => setEditingProduct({ ...editingProduct, name: event.target.value })} disabled={isMutating} />
                        <input aria-label="Catégorie" value={editingProduct.category} onChange={(event) => setEditingProduct({ ...editingProduct, category: event.target.value })} disabled={isMutating} placeholder="Catégorie" />
                        <input aria-label="Pays de vente" value={editingProduct.countryOfSale} onChange={(event) => setEditingProduct({ ...editingProduct, countryOfSale: event.target.value })} disabled={isMutating} placeholder="FR" maxLength={2} />
                        <select aria-label="Cycle de vie" value={editingProduct.lifecycleStatus} onChange={(event) => setEditingProduct({ ...editingProduct, lifecycleStatus: event.target.value as CatalogProductLifecycle })} disabled={isMutating}><option value="active">Actif</option><option value="inactive">Inactif</option><option value="discontinued">Arrêté</option></select>
                        <span className="catalog-inline-actions"><button type="submit" className="text-button" disabled={isMutating}>Enregistrer</button><button type="button" className="text-button" disabled={isMutating} onClick={() => setEditingProduct(null)}>Annuler</button></span>
                      </form>
                    ) : (
                      <>
                        <span className="catalog-row-main"><strong>{product.reference} · {product.name}</strong><small>{supplier?.trading_name || supplier?.legal_name || "Fournisseur historique"}{product.category ? ` · ${product.category}` : ""} · {product.lifecycle_status}</small></span>
                        {canManage && <span className="catalog-row-actions"><button type="button" className="text-button" onClick={() => setEditingProduct({ id: product.id, name: product.name, category: product.category || "", countryOfSale: product.country_of_sale || "", lifecycleStatus: product.lifecycle_status })}>Modifier</button><button type="button" className="text-button catalog-archive-button" disabled={isMutating} onClick={() => void archiveProduct(product)}>Archiver</button></span>}
                      </>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
      <p className="catalog-disclaimer">Un rattachement catalogue facilite le pré-audit ; il ne vérifie ni l’identité, ni la conformité, ni la qualité d’une preuve fournisseur.</p>
    </section>
  );
}
