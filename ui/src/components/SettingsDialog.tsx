import { FONT_SIZES, type Settings } from "../settings";
import { useApp } from "../store";
import { Dialog } from "./Dialog";

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
      <div className="dialog-actions">
        <button type="button" onClick={() => setDialog(null)}>
          Close
        </button>
      </div>
    </Dialog>
  );
}
