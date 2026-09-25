"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import AuditDashboard from "@/components/AuditDashboard";
import {
  beginSsoLogin,
  createOrganization,
  getAuthStatus,
  getCurrentSession,
  logout,
  switchActiveOrganization,
} from "@/lib/auth";
import type { AuthSession, AuthStatus } from "@/lib/types";

type State =
  | { phase: "loading" }
  | { phase: "anonymous"; status: AuthStatus }
  | { phase: "authenticated"; session: AuthSession }
  | { phase: "error"; message: string };

function Initials({ label }: { label: string }) {
  const initials = label
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase() || "VC";
  return <span className="auth-brand-symbol" aria-hidden="true">{initials}</span>;
}

export default function AuthGate() {
  const [state, setState] = useState<State>({ phase: "loading" });
  const [actionError, setActionError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const refresh = useCallback(async () => {
    setState({ phase: "loading" });
    try {
      const [status, session] = await Promise.all([getAuthStatus(), getCurrentSession()]);
      setState(session ? { phase: "authenticated", session } : { phase: "anonymous", status });
    } catch (cause) {
      setState({ phase: "error", message: cause instanceof Error ? cause.message : "La session n’a pas pu être vérifiée." });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const activeMembership = useMemo(() => {
    if (state.phase !== "authenticated") return null;
    return state.session.memberships.find((membership) => membership.organization.id === state.session.active_organization_id) ?? null;
  }, [state]);

  async function handleLogout() {
    setActionError(null);
    setIsSubmitting(true);
    try {
      await logout();
      await refresh();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "La déconnexion a échoué.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleOrganizationSwitch(organizationId: string) {
    setActionError(null);
    setIsSubmitting(true);
    try {
      await switchActiveOrganization(organizationId);
      await refresh();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "Le changement d’organisation a échoué.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleCreateOrganization(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const name = String(data.get("organization-name") ?? "").trim();
    const slug = String(data.get("organization-slug") ?? "").trim();
    if (!name) {
      setActionError("Indiquez le nom de votre organisation.");
      return;
    }
    setActionError(null);
    setIsSubmitting(true);
    try {
      await createOrganization(name, slug || undefined);
      await refresh();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "La création de l’organisation a échoué.");
    } finally {
      setIsSubmitting(false);
    }
  }

  if (state.phase === "loading") {
    return (
      <main className="auth-page" aria-busy="true">
        <section className="auth-card auth-loading-card"><span className="button-spinner" aria-hidden="true" /> Vérification de la session sécurisée…</section>
      </main>
    );
  }

  if (state.phase === "error") {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <Initials label="VeriClaim" />
          <p className="auth-eyebrow">SESSION SÉCURISÉE</p>
          <h1>Connexion indisponible</h1>
          <p>{state.message}</p>
          <button type="button" className="button button-primary" onClick={() => void refresh()}>Réessayer</button>
        </section>
      </main>
    );
  }

  if (state.phase === "anonymous") {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <Initials label="VeriClaim" />
          <p className="auth-eyebrow">VERICLAIM AI · ACCÈS ENTREPRISE</p>
          <h1>La preuve, dans un espace protégé.</h1>
          <p>Connectez-vous via le SSO de votre organisation. Les analyses et les traces d’audit sont isolées par organisation active.</p>
          {state.status.oidc_configured ? (
            <button type="button" className="button button-primary auth-login-button" onClick={beginSsoLogin}>
              Se connecter avec le SSO <span aria-hidden="true">↗</span>
            </button>
          ) : (
            <div className="auth-warning" role="status">
              Le SSO OIDC n’est pas encore configuré pour cet environnement. Renseignez les variables OIDC côté serveur avant de connecter un compte.
            </div>
          )}
          <small>VeriClaim AI est un outil de pré-audit et de gestion du risque, pas un avis juridique ni une certification.</small>
        </section>
      </main>
    );
  }

  if (!activeMembership && state.session.memberships.length > 0) {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <Initials label={state.session.user.display_name || state.session.user.email} />
          <p className="auth-eyebrow">SÉLECTION DE L’ESPACE</p>
          <h1>Choisissez une organisation</h1>
          <p>Votre compte est rattaché à plusieurs espaces. L’organisation sélectionnée déterminera le périmètre de chaque requête.</p>
          <div className="auth-organization-list">
            {state.session.memberships.map((membership) => (
              <button
                key={membership.id}
                type="button"
                className="auth-organization-choice"
                disabled={isSubmitting}
                onClick={() => void handleOrganizationSwitch(membership.organization.id)}
              >
                <span><strong>{membership.organization.name}</strong><small>{membership.role.name} · {membership.organization.data_region.toUpperCase()}</small></span>
                <span aria-hidden="true">↗</span>
              </button>
            ))}
          </div>
          {actionError && <p className="auth-inline-error" role="alert">{actionError}</p>}
          <button type="button" className="text-button" disabled={isSubmitting} onClick={() => void handleLogout()}>Se déconnecter</button>
        </section>
      </main>
    );
  }

  if (!activeMembership) {
    return (
      <main className="auth-page">
        <section className="auth-card auth-onboarding-card">
          <Initials label={state.session.user.display_name || state.session.user.email} />
          <p className="auth-eyebrow">CONFIGURATION DE L’ESPACE</p>
          <h1>Créez votre première organisation</h1>
          <p>Votre compte SSO est vérifié. Créez l’organisation qui détiendra les dossiers, preuves et rapports de votre équipe.</p>
          <form className="auth-form" onSubmit={handleCreateOrganization}>
            <label>
              <span>Nom de l’organisation</span>
              <input name="organization-name" autoComplete="organization" maxLength={255} placeholder="Ex. Atelier RSE Europe" disabled={isSubmitting} />
            </label>
            <label>
              <span>Slug (facultatif)</span>
              <input name="organization-slug" maxLength={100} pattern="[a-z0-9-]+" placeholder="atelier-rse-europe" disabled={isSubmitting} />
            </label>
            {actionError && <p className="auth-inline-error" role="alert">{actionError}</p>}
            <button type="submit" className="button button-primary" disabled={isSubmitting}>
              {isSubmitting ? "Création…" : "Créer l’organisation"}
            </button>
          </form>
          <button type="button" className="text-button" disabled={isSubmitting} onClick={() => void handleLogout()}>Se déconnecter</button>
        </section>
      </main>
    );
  }

  return (
    <>
      {actionError && <div className="auth-toast" role="alert">{actionError}</div>}
      <AuditDashboard
        auth={state.session}
        activeMembership={activeMembership}
        isSessionActionLoading={isSubmitting}
        onLogout={() => void handleLogout()}
        onSwitchOrganization={(organizationId) => void handleOrganizationSwitch(organizationId)}
      />
    </>
  );
}
