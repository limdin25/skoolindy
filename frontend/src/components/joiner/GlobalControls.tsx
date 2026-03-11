import { useState } from "react";
import { Play, Square, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { StatusBadge } from "./StatusBadge";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { GlobalStatus } from "@/lib/mock-data";

interface GlobalControlsProps {
  status: GlobalStatus;
  onStart: () => void;
  onStop: () => void;
  delayAccounts: number;
  delayJoins: number;
  onDelayAccountsChange: (v: number) => void;
  onDelayJoinsChange: (v: number) => void;
  progress?: { accounts: number; totalAccounts: number; communities: number; totalCommunities: number };
}

export function GlobalControls({
  status,
  onStart,
  onStop,
  delayAccounts,
  delayJoins,
  onDelayAccountsChange,
  onDelayJoinsChange,
  progress,
}: GlobalControlsProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-4">
        <h1 className="text-xl font-semibold text-foreground">Automation Control Center</h1>
        <StatusBadge status={status} />
      </div>

      <div className="flex items-center gap-3">
        {status === "Running" && progress && (
          <div className="flex items-center gap-4 text-sm text-muted-foreground mr-4">
            <span>Accounts: {progress.accounts}/{progress.totalAccounts}</span>
            <span>Communities: {progress.communities}/{progress.totalCommunities}</span>
          </div>
        )}

        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" className="gap-2">
              <Settings2 className="h-4 w-4" />
              Delays
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-64" align="end">
            <div className="space-y-3">
              <div>
                <Label className="text-xs text-muted-foreground">Delay between accounts (sec)</Label>
                <Input
                  type="number"
                  min={0}
                  value={delayAccounts}
                  onChange={(e) => onDelayAccountsChange(Number(e.target.value))}
                  className="mt-1"
                />
              </div>
              <div>
                <Label className="text-xs text-muted-foreground">Delay between joins (sec)</Label>
                <Input
                  type="number"
                  min={0}
                  value={delayJoins}
                  onChange={(e) => onDelayJoinsChange(Number(e.target.value))}
                  className="mt-1"
                />
              </div>
            </div>
          </PopoverContent>
        </Popover>

        {status === "Running" ? (
          <Button variant="destructive" size="sm" onClick={onStop} className="gap-2">
            <Square className="h-3.5 w-3.5" /> Stop
          </Button>
        ) : (
          <Button size="sm" onClick={onStart} className="gap-2">
            <Play className="h-3.5 w-3.5" /> Start
          </Button>
        )}
      </div>
    </div>
  );
}
