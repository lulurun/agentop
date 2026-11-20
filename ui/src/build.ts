/** The entry script this page loaded, as the service names its app's (ServiceInfo.ui_build). None in development. */
export function ownBuild(doc: Document = document): string | null {
  const script = doc.querySelector<HTMLScriptElement>('script[type="module"][src*="assets/"]');
  return script?.getAttribute("src")?.split("/").pop() ?? null;
}

