import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { checkPassword, DEV_MODE, unlock } from "@/lib/lockscreen";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Lock, Zap } from "lucide-react";

export default function LockScreen() {
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const handleUnlock = () => {
    if (checkPassword(password)) {
      unlock(remember);
      navigate("/", { replace: true });
    } else {
      setError("Incorrect password");
    }
  };

  const handleDirectUnlock = () => {
    unlock(false);
    navigate("/", { replace: true });
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="w-full max-w-sm mx-auto px-6">
        <div className="flex flex-col items-center mb-8">
          <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-primary mb-4">
            <Zap className="w-6 h-6 text-primary-foreground" />
          </div>
          <h1 className="text-xl font-semibold text-foreground">EngageFlow</h1>
          <p className="text-sm text-muted-foreground mt-1">Enter password to continue</p>
        </div>

        <div className="space-y-4">
          <div className="relative">
            <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => { setPassword(e.target.value); setError(""); }}
              onKeyDown={(e) => e.key === "Enter" && handleUnlock()}
              className="pl-10"
            />
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <div className="flex items-center gap-2">
            <Checkbox
              id="remember"
              checked={remember}
              onCheckedChange={(v) => setRemember(v === true)}
            />
            <label htmlFor="remember" className="text-sm text-muted-foreground cursor-pointer">
              Remember me on this device
            </label>
          </div>

          <Button onClick={handleUnlock} className="w-full">
            Unlock
          </Button>

          {DEV_MODE && (
            <Button variant="outline" onClick={handleDirectUnlock} className="w-full text-muted-foreground">
              Direct Unlock (Dev)
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
