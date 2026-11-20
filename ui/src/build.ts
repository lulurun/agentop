/** The entry script this page loaded, as the service names its app's (ServiceInfo.ui_build). None in development. */
export function ownBuild(doc: Document = document): string | null {
  const script = doc.querySelector<HTMLScriptElement>('script[type="module"][src*="assets/"]');
  return script?.getAttribute("src")?.split("/").pop() ?? null;
}

/** The service that served this page has another app now, so this page is out of date until reloaded. */
export function pageIsOutdated(serviceUrl: string, uiBuild: string | null | undefined, own = ownBuild(), here = location.href): boolean {
  if (!own || !uiBuild || uiBuild === own) return false;
  try {
    return new URL(serviceUrl).origin === new URL(here).origin;
  } catch {
    return false;
  }
}
