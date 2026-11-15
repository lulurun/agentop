import { useState } from "react";
import type { PluginInfo } from "../api/client";
import { missingFromApp, pluginsFor, UI_PLUGINS } from "../plugins/registry";
import { PluginSlot, usePluginContext } from "../plugins/Slot";
import { useApp } from "../store";

const STATE_TEXT: Record<PluginInfo["state"], string> = {
  enabled: "On",
  disabled: "Off",
  failed: "Failed",
  incompatible: "Incompatible",
};

function PluginEntry({ info }: { info: PluginInfo }) {
  const setEnabled = useApp((s) => s.setPluginEnabled);
  const plugin = usePluginContext(info);
  // What the plugin says turning it off would do, waiting for the user to confirm.
  const [warning, setWarning] = useState<string | null>(null);
  const hasSettings = pluginsFor("settings-section", [info]).length > 0;
  const switchable = info.state === "enabled" || info.state === "disabled";

  async function switchTo(enabled: boolean) {
    const ask = UI_PLUGINS[info.id]?.beforeDisable;
    if (!enabled && info.state === "enabled" && ask) {
      const message = await ask(plugin).catch(() => null);
      if (message) {
        setWarning(message);
        return;
      }
    }
    setWarning(null);
    await setEnabled(info.id, enabled);
  }

  return (
    <li className="plugin-entry" data-state={info.state}>
      <div className="plugin-head">
        <label className="plugin-switch">
          <input
            type="checkbox"
            role="switch"
            checked={info.state === "enabled"}
            disabled={!switchable}
            aria-label={`${info.name} on`}
            onChange={(event) => void switchTo(event.target.checked)}
          />
          <strong>{info.name}</strong>
        </label>
        <span className="plugin-meta">
          {info.version && <span>{info.version}</span>}
          <span className="plugin-state">{STATE_TEXT[info.state]}</span>
        </span>
      </div>
      {info.description && <p className="plugin-description">{info.description}</p>}
      {warning && (
        <div className="plugin-warning" role="alert">
          <p>{warning}</p>
          <div className="plugin-warning-actions">
            <button type="button" onClick={() => setWarning(null)}>Keep it on</button>
            <button
              type="button"
              className="primary"
              onClick={() => {
                setWarning(null);
                void setEnabled(info.id, false);
              }}
            >
              Turn it off
            </button>
          </div>
        </div>
      )}
      {info.error && <p className="form-error">{info.error}</p>}
      {info.state === "failed" && (
        <p className="plugin-note">
          Restart the service to try loading it again.{" "}
          <button type="button" className="link" onClick={() => void setEnabled(info.id, false)}>
            Turn it off
          </button>
        </p>
      )}
      {missingFromApp(info) && <p className="plugin-note">Not available in this app. Update the app to use it.</p>}
      {hasSettings && (
        <div className="plugin-settings">
          <PluginSlot slot="settings-section" only={info.id} />
        </div>
      )}
    </li>
  );
}

/** Plugins belong to the service, so switching one affects every client of that service. */
export function PluginsSection() {
  const plugins = useApp((s) => s.plugins);
  const service = useApp((s) => (s.link.state === "connected" || s.link.state === "reconnecting" ? s.link.service.name : null));
  if (!service) return null;
  return (
    <section className="settings-plugins" aria-label="Plugins">
      <h3>Plugins on {service}</h3>
      {plugins.length ? (
        <ul className="plugin-list">
          {[...plugins].sort((a, b) => a.name.localeCompare(b.name)).map((info) => (
            <PluginEntry key={info.id} info={info} />
          ))}
        </ul>
      ) : (
        <p className="plugin-note">This service has no plugins.</p>
      )}
    </section>
  );
}

