/** Browser API address. Git Bash can expand /pv/api into a Windows file path. */
export function getApiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_PIVOT_API_BASE?.trim();
  if (configured && (/^https?:\/\//i.test(configured) || /^\/(?!\/)/.test(configured))) {
    return configured.replace(/\/+$/, "");
  }
  return "/pv/api";
}
