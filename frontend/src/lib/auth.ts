import { requestUrl } from "@/lib/api";
import { csrfHeaders } from "@/lib/csrf";
import type { AuthSession, AuthStatus, OrganizationMembership } from "@/lib/types";

async function errorMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "detail" in body) {
      const detail = (body as { detail?: unknown }).detail;
      if (typeof detail === "string") return detail;
    }
  } catch {
    // Preserve a stable, non-sensitive fallback below.
  }
  return `Erreur d’authentification (${response.status})`;
}

async function authenticatedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method || "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    Object.entries(csrfHeaders()).forEach(([name, value]) => headers.set(name, value));
  }
  return fetch(requestUrl(path), {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });
}

export async function getAuthStatus(): Promise<AuthStatus> {
  const response = await authenticatedFetch("/api/v1/auth/status");
  if (!response.ok) throw new Error(await errorMessage(response));
  return (await response.json()) as AuthStatus;
}

export async function getCurrentSession(): Promise<AuthSession | null> {
  const response = await authenticatedFetch("/api/v1/auth/me");
  if (response.status === 401) return null;
  if (!response.ok) throw new Error(await errorMessage(response));
  return (await response.json()) as AuthSession;
}

export function beginSsoLogin(): void {
  const returnTo = `${window.location.pathname}${window.location.search}`;
  window.location.assign(requestUrl(`/api/v1/auth/login?return_to=${encodeURIComponent(returnTo)}`));
}

export async function logout(): Promise<void> {
  const response = await authenticatedFetch("/api/v1/auth/logout", { method: "POST" });
  if (!response.ok && response.status !== 204) throw new Error(await errorMessage(response));
}

export async function createOrganization(name: string, slug?: string): Promise<OrganizationMembership> {
  const response = await authenticatedFetch("/api/v1/organizations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, slug: slug || undefined }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return (await response.json()) as OrganizationMembership;
}

export async function switchActiveOrganization(organizationId: string): Promise<OrganizationMembership> {
  const response = await authenticatedFetch("/api/v1/auth/active-organization", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ organization_id: organizationId }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return (await response.json()) as OrganizationMembership;
}
