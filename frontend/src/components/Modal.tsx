import { ReactNode, useEffect } from "react";
import { useI18n } from "../i18n";

export default function Modal({ title, onClose, children, footer, narrow, wide }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; narrow?: boolean; wide?: boolean }) {
  const { t } = useI18n();
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${narrow ? "narrow" : ""} ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button className="ghost" onClick={onClose} aria-label={t("close")}>✕</button>
        </div>
        {children}
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}
