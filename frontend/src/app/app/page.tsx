import AuthGate from "@/components/AuthGate";

/**
 * The product itself. Kept at `/app` so the marketing site can own `/` without
 * an authenticated-first experience, and so the entry point is a shareable URL.
 */
export default function WorkspacePage() {
  return <AuthGate />;
}
