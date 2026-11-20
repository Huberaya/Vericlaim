import factsFile from "./legal-facts.json";

/**
 * Typed access to the legal facts file.
 *
 * C12 asked for legal documents that are not fiction. Two failure modes are worse
 * than a missing page: a page that promises a right the product does not serve,
 * and a page that states a company detail nobody verified. Both are prevented
 * here:
 *
 * - the rights table is copied from `app/privacy/rights.py` and
 *   `backend/tests/test_public_claims.py` recomputes it — if the product stops
 *   serving a right, or starts serving a new one, the test fails until this file
 *   is regenerated;
 * - every company-specific value (publisher, host, data-protection contact) is
 *   `null` in the file and rendered as "à compléter", never guessed.
 */

export type LegalField = {
  label: string;
  value: string | null;
};

export type RightHandling = {
  request_type: string;
  article: string;
  automated: boolean;
  route: string | null;
  requires_permission: string;
  note: string;
};

export type LegalFacts = {
  review: {
    external_review: string;
    reviewer: string | null;
    label: string;
    statement: string;
    missing_fields_notice: string;
  };
  publisher: { title: string; fields: LegalField[]; note: string };
  hosting: { title: string; fields: LegalField[]; note: string };
  rights: {
    title: string;
    response_window_months: number;
    extension_months: number;
    contact_email: string | null;
    contact_note: string;
    procedure_path: string;
    handling: RightHandling[];
    manual_label: string;
    automated_label: string;
  };
  cookies: {
    title: string;
    items: Array<{
      name: string;
      purpose: string;
      duration: string;
      attributes: string;
      consent_required: boolean;
    }>;
    note: string;
  };
  retention: { title: string; rules: string[] };
  transfers: {
    title: string;
    no_ai_statement: string;
    verified_by: string[];
    outbound_flows: string[];
  };
  gaps: { title: string; items: string[] };
  documents: {
    title: string;
    items: Array<{ title: string; path: string; status: string }>;
    served_note: string;
  };
};

export const legalFacts = factsFile as LegalFacts;

export const RIGHTS_LABELS: Record<string, string> = {
  access: "Accès",
  rectification: "Rectification",
  erasure: "Effacement",
  restriction: "Limitation",
  portability: "Portabilité",
  objection: "Opposition",
};

export function rightLabel(requestType: string): string {
  return RIGHTS_LABELS[requestType] ?? requestType;
}

/** `null` is a fact, not a hole: the page shows the missing item instead of hiding it. */
export function displayValue(value: string | null): string {
  return value && value.trim().length > 0 ? value : "à compléter";
}

export function isMissing(value: string | null): boolean {
  return !value || value.trim().length === 0;
}
