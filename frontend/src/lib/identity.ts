import { requestUrl } from "@/lib/api";
import { csrfHeaders } from "@/lib/csrf";

/**
 * Client for the C14 self-service identity endpoints.
 *
 * Two rules shape this module:
 *
 * 1. **The API is the only source of truth about the outcome.** A signup answers
 *    the same thing whether the address exists or not; these functions therefore
 *    return that message verbatim instead of inventing a friendlier one, and never
 *    turn it into "un e-mail vous a été envoyé" — nothing here can know that.
 * 2. **Delivery is never asserted by the interface.** The invitation screen shows
 *    the delivery status returned by the backend, including
 *    "not_configured" when the instance has no SMTP relay. A green tick that means
 *    "recorded in a database queue" would be a lie the operator cannot detect.
 */

export type SelfServiceMessage = {
  status: string;
  message: string;
  email_recorded?: boolean;
};

export type PasswordPolicy = {
  min_length: number;
  max_length: number;
  requires_digit: boolean;
  requires_letter: boolean;
  hashing: { scheme: string; note: string };
  note?: string;
};

export type PasswordSession = {
  user_id: string;
  email: string;
  display_name: string | null;
  active_organization_id: string | null;
};

export type SignupInput = {
  email: string;
  password: string;
  organizationName: string;
  displayName?: string;
};

/**
 * Turns an API error body into something a human can act on.
 *
 * The self-service API answers with `{"detail": {"code", "message", "errors"}}`.
 * Flattening that to "Erreur API (422)" would hide the one piece of information the
 * visitor needs — which rule their password broke.
 */
export function describeApiError(body: unknown, status: number): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (typeof detail === "object" && detail !== null) {
      const message = (detail as { message?: unknown }).message;
      const errors = (detail as { errors?: unknown }).errors;
      const lines: string[] = [];
      if (typeof message === "string") lines.push(message);
      if (Array.isArray(errors)) {
        errors.forEach((error) => {
          if (typeof error === "string" && !lines.includes(error)) lines.push(error);
        });
      }
      if (lines.length) return lines.join(" ");
    }
  }
  return `Erreur API (${status})`;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    Object.entries(csrfHeaders()).forEach(([name, value]) => headers.set(name, value));
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(requestUrl(path), {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) throw new Error(describeApiError(body, response.status));
  return body as T;
}

export function getPasswordPolicy(): Promise<PasswordPolicy> {
  return call<PasswordPolicy>("/api/v1/auth/password-policy");
}

export function signupWithPassword(input: SignupInput): Promise<SelfServiceMessage> {
  return call<SelfServiceMessage>("/api/v1/auth/signup", {
    method: "POST",
    body: JSON.stringify({
      email: input.email,
      password: input.password,
      organization_name: input.organizationName,
      display_name: input.displayName || undefined,
    }),
  });
}

export function verifyEmail(token: string): Promise<SelfServiceMessage> {
  return call<SelfServiceMessage>("/api/v1/auth/verify-email", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export function passwordLogin(email: string, password: string): Promise<PasswordSession> {
  return call<PasswordSession>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function requestPasswordReset(email: string): Promise<SelfServiceMessage> {
  return call<SelfServiceMessage>("/api/v1/auth/password-reset/request", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function confirmPasswordReset(token: string, newPassword: string): Promise<SelfServiceMessage> {
  return call<SelfServiceMessage>("/api/v1/auth/password-reset/confirm", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword }),
  });
}

export function changePassword(currentPassword: string, newPassword: string): Promise<SelfServiceMessage> {
  return call<SelfServiceMessage>("/api/v1/auth/password/change", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

export function acceptInvitation(
  token: string,
  password: string,
  displayName?: string,
): Promise<PasswordSession> {
  return call<PasswordSession>("/api/v1/auth/invitations/accept", {
    method: "POST",
    body: JSON.stringify({ token, password, display_name: displayName || undefined }),
  });
}
