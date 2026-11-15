import { useEffect, useMemo, useState } from "react";
import { fetchLoginMethods, publicPluginClient, type LoginMethod } from "../api/client";
import { clientName } from "../connections";
import { useApp } from "../store";
import { UI_PLUGINS } from "./registry";
import { PluginBoundary } from "./Slot";
import type { LoginContext } from "./types";

/**
 * The ways to sign in that a service lists, besides the service token: each one
 * a login plugin the service has enabled and this app has a component for.
 * Unknown methods, and a service that can't be asked, leave only the token form.
 */
export function useLoginMethods(url: string): LoginMethod[] {
  const [methods, setMethods] = useState<LoginMethod[]>([]);
  useEffect(() => {
    let live = true;
    setMethods([]);
    fetchLoginMethods(url)
      .then((found) => live && setMethods(found.filter((m) => m.id !== "token" && UI_PLUGINS[m.id]?.login)))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [url]);
  return methods;
}

export function LoginMethods({ url, methods }: { url: string; methods: LoginMethod[] }) {
  return (
    <>
      {methods.map((method) => (
        <PluginBoundary key={method.id} id={method.id}>
          <LoginPiece url={url} method={method} />
        </PluginBoundary>
      ))}
    </>
  );
}

function LoginPiece({ url, method }: { url: string; method: LoginMethod }) {
  const signIn = useApp((s) => s.signIn);
  const login = useMemo<LoginContext>(
    () => ({ id: method.id, name: method.name, url, clientName: clientName(), client: publicPluginClient(url, method.id), signIn }),
    [url, method, signIn],
  );
  const Component = UI_PLUGINS[method.id]?.login;
  return Component ? (
    <section className="login-method" aria-label={method.name}>
      <Component login={login} />
    </section>
  ) : null;
}
