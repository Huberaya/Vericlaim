import claimsFile from "./public-claims.json";

/**
 * Typed access to the public claims file.
 *
 * The file is the single source of truth for what the site is allowed to
 * assert. Every claim carries an `evidence_key` that `backend/tests/
 * test_public_claims.py` knows how to verify against the running product; a new
 * claim with an unknown key fails that test rather than reaching a customer.
 * The published numbers come from the same file, so the page cannot advertise a
 * detection rate the tests do not measure.
 */

export type PublicClaim = {
  id: string;
  text: string;
  evidence_key: string;
  detail: string;
};

export type PublishedFacts = {
  lexicon_version: string;
  rulebook_version: string;
  rule_count: number;
  corpus_positive_cases: number;
  corpus_detection_rate: number;
  corpus_negative_cases: number;
  corpus_false_positive_rate: number;
  corpus_note: string;
};

type ClaimsFile = {
  schema_version: string;
  note: string;
  legal_review_status: string;
  claims: PublicClaim[];
  published_facts: PublishedFacts;
};

const data = claimsFile as ClaimsFile;

export const PUBLIC_CLAIMS_SCHEMA_VERSION = data.schema_version;
export const PUBLIC_CLAIMS_LEGAL_STATUS = data.legal_review_status;
export const publicClaims: PublicClaim[] = data.claims;
export const publishedFacts: PublishedFacts = data.published_facts;

export function percent(value: number): string {
  return `${Math.round(value * 100)} %`;
}
