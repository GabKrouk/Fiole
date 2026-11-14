import { useState } from "react";
import { api, User } from "../api";
import { useAuth } from "../App";
import { useI18n } from "../i18n";
import Modal from "./Modal";

/** "My account": display name. */
export function AccountDialog({ onClose }: { onClose: () => void }) {
  const { lang } = useI18n();
  const { user, setUser } = useAuth();
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);
  const [name, setName] = useState(user?.display_name ?? "");
  if (!user) return null;
  return (
    <Modal title={T("Mon compte", "My account")} onClose={onClose} narrow
      footer={<button className="primary" onClick={async () => { if (name.trim() && name !== user.display_name) setUser(await api.patch<User>("/api/auth/me", { display_name: name })); onClose(); }}>{T("OK", "Done")}</button>}>
      <label className="field"><span>{T("Nom affiché", "Display name")}</span><input value={name} onChange={(e) => setName(e.target.value)} /></label>
      <div className="field"><span>Email</span><div>{user.email}</div></div>
    </Modal>
  );
}
