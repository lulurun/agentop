import { useEffect, useRef, type ReactNode } from "react";
import { useApp } from "../store";

export function Dialog({ title, children }: { title: string; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const setDialog = useApp((s) => s.setDialog);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  return (
    <dialog ref={ref} className="dialog" onClose={() => setDialog(null)} aria-labelledby="dialog-title">
      <h2 id="dialog-title">{title}</h2>
      {children}
    </dialog>
  );
}
