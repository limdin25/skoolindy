import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { CheckCircle2, Loader2, Zap } from "lucide-react";

export default function ConnectPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [countdown, setCountdown] = useState(30);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [profileId, setProfileId] = useState<string | null>(null);

  useEffect(() => {
    if (!loading) return;
    setCountdown(30);
    const id = setInterval(() => {
      setCountdown((c) => (c > 0 ? c - 1 : 0));
    }, 1000);
    return () => clearInterval(id);
  }, [loading]);

  const handleConnect = async () => {
    setError("");
    if (!email.trim() || !password.trim()) {
      setError("Email and password are required");
      return;
    }
    setLoading(true);
    try {
      const res = await api.connectSkool({ email: email.trim(), password });
      if (res?.success && res?.profileId) {
        setProfileId(res.profileId);
        setConnected(true);
      } else {
        setError(res?.message || "Connection failed");
      }
    } catch (e: unknown) {
      const msg = e && typeof e === "object" && "message" in e ? String((e as { message: unknown }).message) : "Connection failed";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleDisconnect = () => {
    setConnected(false);
    setProfileId(null);
    setError("");
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-white">
      <div className="w-full max-w-sm mx-auto px-6">
        <div className="flex flex-col items-center mb-8">
          <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-primary mb-4">
            <Zap className="w-6 h-6 text-primary-foreground" />
          </div>
          <h1 className="text-xl font-semibold text-foreground">Connect Skool</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {connected ? "Your account is connected" : "Enter your Skool credentials"}
          </p>
        </div>

        {connected ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2 p-4 rounded-lg bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800">
              <CheckCircle2 className="w-5 h-5 text-green-600 shrink-0" />
              <span className="text-sm font-medium text-green-700 dark:text-green-400">Connected</span>
            </div>
            <Button variant="outline" onClick={handleDisconnect} className="w-full">
              Disconnect
            </Button>
          </div>
        ) : (
          <div className="space-y-4">
            <Input
              type="email"
              placeholder="Skool email"
              value={email}
              onChange={(e) => { setEmail(e.target.value); setError(""); }}
              onKeyDown={(e) => e.key === "Enter" && handleConnect()}
            />
            <Input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => { setPassword(e.target.value); setError(""); }}
              onKeyDown={(e) => e.key === "Enter" && handleConnect()}
            />
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button onClick={handleConnect} disabled={loading} className="w-full">
              {loading ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
              Connect
            </Button>
            {loading && (
              <div className="flex flex-col items-center gap-2 mt-4 text-center">
                <div className="flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span className="text-sm text-muted-foreground">Connecting</span>
                </div>
                <span className="text-2xl font-semibold tabular-nums text-muted-foreground">
                  {countdown}s
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
