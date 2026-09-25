const CSRF_COOKIE_NAME = "vericlaim_csrf";

export function csrfHeaders(): Record<string, string> {
  if (typeof document === "undefined") return {};
  const token = document.cookie
    .split(";")
    .map((item) => item.trim())
    .find((item) => item.startsWith(`${CSRF_COOKIE_NAME}=`))
    ?.slice(CSRF_COOKIE_NAME.length + 1);
  return token ? { "X-CSRF-Token": decodeURIComponent(token) } : {};
}
