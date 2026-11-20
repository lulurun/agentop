import { useEffect, useState } from "react";
import type { ServiceInfo } from "../api/client";
import { FONT_SIZES, type Settings } from "../settings";
import { activeConnection, useActiveClient, useApp } from "../store";
import { connectionName } from "../connections";
import { Dialog } from "./Dialog";

/** How this app looks: the same for every service. A service's plugins are in its Status tab. */
export function SettingsDialog() {
  const settings = useApp((s) => s.settings);
  const update = useApp((s) => s.updateSettings);
  const setDialog = useApp((s) => s.setDialog);
  const connection = useApp(activeConnection);
  const client = useActiveClient();
  const [service, setService] = useState<ServiceInfo | null>(null);

  useEffect(() => {
    let live = true;
    client.service().then((info) => live && setService(info)).catch(() => undefined);
    return () => {
      live = false;
    };
  }, [client]);

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
      <dl className="settings-version">
        <div><dt>This app</dt><dd>{__UI_VERSION__}</dd></div>
        {service && (
          <div>
            <dt>{connectionName(connection, service.name)}</dt>
            <dd>
              {service.commit ? `${service.version} (${service.commit})` : service.version}
              {service.checkout_commit && service.commit && service.checkout_commit !== service.commit && `, ${service.checkout_commit} after a restart`}
            </dd>
          </div>
        )}
      </dl>
      <div className="dialog-actions">
        <button type="button" onClick={() => setDialog(null)}>
          Close
        </button>
      </div>
    </Dialog>
  );
}
