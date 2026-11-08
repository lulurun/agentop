import type { PluginInfo } from "../api/client";
import { missingFromApp, pluginsFor } from "../plugins/registry";
import { PluginSlot } from "../plugins/Slot";
import { FONT_SIZES, type Settings } from "../settings";
import { useApp } from "../store";
import { Dialog } from "./Dialog";

const STATE_TEXT: Record<PluginInfo["state"], string> = {
  enabled: "On",
  disabled: "Off",
  failed: "Failed",
  incompatible: "Incompatible",
};

function PluginEntry({ info }: { info: PluginInfo }) {
  const setEnabled = useApp((s) => s.setPluginEnabled);
  const hasSettings = pluginsFor("settings-section", [info]).length > 0;
  const switchable = info.state === "enabled" || info.state === "disabled";
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
            onChange={(event) => void setEnabled(info.id, event.target.checked)}
          />
          <strong>{info.name}</strong>
        </label>
        <span className="plugin-meta">
          {info.version && <span>{info.version}</span>}
          <span className="plugin-state">{STATE_TEXT[info.state]}</span>
        </span>
      </div>
      {info.description && <p className="plugin-description">{info.description}</p>}
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
function PluginsSection() {
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

export function SettingsDialog() {
  const settings = useApp((s) => s.settings);
  const update = useApp((s) => s.updateSettings);
  const setDialog = useApp((s) => s.setDialog);

  return (
    <Dialog title="Settings">
      <label className="settings-field">
        Theme
        <select value={settings.theme} onChange={(event) => update({ theme: event.target.value as Settings["theme"] })}>
          <option value="system">Same as this device</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </select>
      </label>
      <label className="settings-field">
        Terminal font size
        <input
          type="number"
          min={FONT_SIZES.min}
          max={FONT_SIZES.max}
          value={settings.fontSize}
          onChange={(event) => {
            const fontSize = Number(event.target.value);
            if (fontSize >= FONT_SIZES.min && fontSize <= FONT_SIZES.max) update({ fontSize });
          }}
        />
      </label>
      <label className="settings-field">
        Terminal drawing
        <select value={settings.renderer} onChange={(event) => update({ renderer: event.target.value as Settings["renderer"] })}>
          <option value="auto">Fast, using the graphics card</option>
          <option value="dom">Compatible</option>
        </select>
      </label>
      <PluginsSection />
      <div className="dialog-actions">
        <button type="button" onClick={() => setDialog(null)}>
          Close
        </button>
      </div>
    </Dialog>
  );
}
