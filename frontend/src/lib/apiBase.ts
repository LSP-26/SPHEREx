// Where the live server is. In order:
//   1. ?api=<url> in the page address, for a temporary tunnel. Only tunnel hosts we use
//      (trycloudflare.com, localhost.run's lhr.life) and localhost are accepted, so a crafted link
//      cannot point the page at an arbitrary server.
//   2. VITE_API_BASE at build time (vite.config.ts fills it from remote-api.txt for a deploy).
//   3. "" (same origin): the dev server's proxy, or a server that hosts the page itself.
const ALLOWED =
  /^(https:\/\/[a-z0-9-]+\.(trycloudflare\.com|lhr\.life|app\.lhr\.life)|http:\/\/(localhost|127\.0\.0\.1)(:\d+)?)$/;

export function resolveApiBase(search: string, fallback = ""): string {
  const raw = new URLSearchParams(search).get("api")?.replace(/\/+$/, "") ?? "";
  return ALLOWED.test(raw) ? raw : fallback;
}

export const API_BASE = resolveApiBase(
  globalThis.location?.search ?? "",
  ((import.meta.env.VITE_API_BASE as string | undefined) ?? "").replace(/\/+$/, ""),
);
