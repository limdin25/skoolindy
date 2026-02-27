import { useState } from "react";
import { APP_LOCK_PASSWORD } from "@/lib/lockscreen";
import { useNavigate } from "react-router-dom";
import { Lock, ArrowLeft, Check } from "lucide-react";

const PASSWORDS_KEY = "engageflow_passwords";

function getSavedPasswords(): string[] {
  try {
    const stored = localStorage.getItem(PASSWORDS_KEY);
    if (stored) return JSON.parse(stored);
  } catch {}
  return [APP_LOCK_PASSWORD, "", ""];
}

function savePasswords(passwords: string[]) {
  localStorage.setItem(PASSWORDS_KEY, JSON.stringify(passwords));
}

export function getValidPasswords(): string[] {
  return getSavedPasswords().filter(p => p.trim() !== "");
}

export default function OwnerPage() {
  const navigate = useNavigate();
  const [ownerAuth, setOwnerAuth] = useState(false);
  const [ownerPassword, setOwnerPassword] = useState("");
  const [ownerError, setOwnerError] = useState("");

  const saved = getSavedPasswords();
  const [pw1, setPw1] = useState(saved[0] || "");
  const [pw2, setPw2] = useState(saved[1] || "");
  const [pw3, setPw3] = useState(saved[2] || "");
  const [success, setSuccess] = useState(false);

  const handleOwnerLogin = () => {
    const valid = getValidPasswords();
    if (valid.includes(ownerPassword) || ownerPassword === APP_LOCK_PASSWORD) {
      setOwnerAuth(true);
      setOwnerError("");
    } else {
      setOwnerError("Invalid password");
    }
  };

  const handleSave = () => {
    if (!pw1.trim()) return;
    savePasswords([pw1.trim(), pw2.trim(), pw3.trim()]);
    setSuccess(true);
    setTimeout(() => setSuccess(false), 2000);
  };

  if (!ownerAuth) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-4">
        <div className="w-full max-w-sm space-y-6">
          <div className="text-center">
            <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-primary/10 mb-4">
              <Lock className="w-6 h-6 text-primary" />
            </div>
            <h1 className="text-xl font-bold text-foreground">Owner Access</h1>
            <p className="text-sm text-muted-foreground mt-1">Enter a valid password to manage access</p>
          </div>
          <div className="space-y-3">
            <input
              type="password"
              value={ownerPassword}
              onChange={e => { setOwnerPassword(e.target.value); setOwnerError(""); }}
              onKeyDown={e => e.key === "Enter" && handleOwnerLogin()}
              placeholder="Password"
              className="w-full px-4 py-3 rounded-xl border border-border bg-background text-foreground text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
            {ownerError && <p className="text-xs text-destructive">{ownerError}</p>}
            <button onClick={handleOwnerLogin} className="w-full py-3 rounded-xl bg-primary text-primary-foreground font-medium text-sm hover:bg-primary/90 transition-colors">
              Continue
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-foreground">Password Management</h1>
            <p className="text-sm text-muted-foreground mt-1">Set up to 3 passwords for app access</p>
          </div>
          <button onClick={() => navigate("/")} className="p-2 rounded-lg hover:bg-muted transition-colors">
            <ArrowLeft className="w-5 h-5 text-muted-foreground" />
          </button>
        </div>

        <div className="bg-card border border-border rounded-xl p-5 space-y-4">
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">Password 1 <span className="text-destructive">*</span></label>
            <input type="text" value={pw1} onChange={e => setPw1(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono" />
          </div>
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">Password 2 <span className="text-muted-foreground/50">(optional)</span></label>
            <input type="text" value={pw2} onChange={e => setPw2(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono" />
          </div>
          <div>
            <label className="text-xs text-muted-foreground mb-1 block">Password 3 <span className="text-muted-foreground/50">(optional)</span></label>
            <input type="text" value={pw3} onChange={e => setPw3(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring font-mono" />
          </div>
          <button onClick={handleSave} disabled={!pw1.trim()}
            className="w-full py-2.5 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50 inline-flex items-center justify-center gap-2">
            {success ? <><Check className="w-4 h-4" /> Saved!</> : "Save Passwords"}
          </button>
        </div>

        <div className="bg-muted/30 rounded-lg p-3">
          <p className="text-[11px] text-muted-foreground">Any of these passwords will unlock the app. Password 1 is required. Passwords are stored locally on this device.</p>
        </div>
      </div>
    </div>
  );
}
