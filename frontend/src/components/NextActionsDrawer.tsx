import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { useQueue, useQueuePreview } from "@/hooks/useEngageFlow";
import type { QueueItem, QueuePreviewItem } from "@/lib/types";

interface Props {
  open: boolean;
  onClose: () => void;
  filterProfileId?: string;
}

type MergedItem = (QueueItem | QueuePreviewItem) & {
  _parsedTs: number;
};

function parseItemTs(item: QueueItem | QueuePreviewItem): number {
  if (item.scheduledFor) {
    const t = new Date(item.scheduledFor).getTime();
    if (Number.isFinite(t)) return t;
  }
  if (item.scheduledTime) {
    const today = new Date();
    const parts = item.scheduledTime.match(/(\d{1,2}):(\d{2})(?::(\d{2}))?/);
    if (parts) {
      today.setHours(Number(parts[1]), Number(parts[2]), Number(parts[3] || 0), 0);
      return today.getTime();
    }
  }
  return Infinity;
}

function formatCountdownStr(diffMs: number): string {
  if (diffMs <= 0) return "";
  const s = Math.floor(diffMs / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return `${m}m ${rs}s`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  if (h < 24) return `${h}h ${rm}m`;
  const d = Math.floor(h / 24);
  const rh = h % 24;
  return `${d}d ${rh}h`;
}

function formatDate(ts: number): string {
  const d = new Date(ts);
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const tomorrowStart = todayStart + 86400000;
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  if (ts >= todayStart && ts < tomorrowStart) return `Today ${time}`;
  if (ts >= tomorrowStart && ts < tomorrowStart + 86400000) return `Tomorrow ${time}`;
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) + " " + time;
}

function isFollowUpItem(item: QueueItem | QueuePreviewItem): boolean {
  if ((item as QueueItem).isFollowUp) return true;
  const label = ((item as QueuePreviewItem).actionLabel || "").toLowerCase();
  const comm = (item.community || "").toLowerCase();
  return label.includes("follow") || label.includes("dm") || comm.includes("follow") || comm.includes("dm");
}

export default function NextActionsDrawer({ open, onClose, filterProfileId }: Props) {
  const { data: liveQueue = [] } = useQueue();
  const { data: projected = [] } = useQueuePreview();
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (!open) return;
    const interval = setInterval(() => setNow(Date.now()), 1000);
    const onKeyDown = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKeyDown);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      clearInterval(interval);
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!open) return null;

  const filteredLive = filterProfileId
    ? liveQueue.filter(q => q.profileId === filterProfileId)
    : liveQueue;
  const filteredProjected = filterProfileId
    ? projected.filter(q => q.profileId === filterProfileId)
    : projected;

  // Merge and deduplicate (projected may overlap live)
  const liveIds = new Set(filteredLive.map(i => i.id));
  const dedupedProjected = filteredProjected.filter(i => !liveIds.has(i.id));

  const allItems: MergedItem[] = [
    ...filteredLive.map(i => ({ ...i, _parsedTs: parseItemTs(i) })),
    ...dedupedProjected.map(i => ({ ...i, _parsedTs: parseItemTs(i) })),
  ].sort((a, b) => a._parsedTs - b._parsedTs);

  const total = allItems.length;

  // Find next follow-up and next comment
  const nextFollowUp = allItems.find(i => isFollowUpItem(i));
  const nextComment = allItems.find(i => !isFollowUpItem(i));

  const renderCountdownBadge = (ts: number) => {
    const diff = ts - now;
    if (diff <= 0) return <span className="text-destructive font-medium">{"\uD83D\uDD34"} Overdue</span>;
    return <span className="text-muted-foreground">{"\uD83D\uDD04"} \u23f1 {formatCountdownStr(diff)}</span>;
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-foreground/20 animate-fade-in" onClick={onClose}>
      <div className="w-full max-w-xl bg-card border-l border-border h-full overflow-y-auto animate-slide-in" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="px-6 py-5 border-b border-border sticky top-0 bg-card z-10">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <span className="text-success text-lg">{"\uD83D\uDFE2"}</span>
              <h2 className="text-lg font-semibold text-foreground">System Running</h2>
              <span className="text-xs text-muted-foreground ml-1">Queue: {total} pending</span>
            </div>
            <button onClick={onClose} className="p-1 rounded-md hover:bg-muted transition-colors">
              <X className="w-5 h-5 text-muted-foreground" />
            </button>
          </div>

          <div className="grid grid-cols-2 gap-3">
            {/* Next follow-up */}
            <div className="bg-muted/30 rounded-lg p-3">
              <p className="text-[11px] text-muted-foreground mb-1">{"\uD83D\uDCE9"} Next follow-up DM</p>
              {nextFollowUp ? (
                <>
                  <p className="text-sm font-medium text-foreground truncate">
                    {(nextFollowUp as QueueItem).followUpLeadName || nextFollowUp.profile}
                  </p>
                  <p className="text-[11px] text-muted-foreground">
                    {formatDate(nextFollowUp._parsedTs)} &middot; {renderCountdownBadge(nextFollowUp._parsedTs)}
                  </p>
                </>
              ) : (
                <p className="text-xs text-muted-foreground italic">None scheduled</p>
              )}
            </div>
            {/* Next comment */}
            <div className="bg-muted/30 rounded-lg p-3">
              <p className="text-[11px] text-muted-foreground mb-1">{"\uD83D\uDCAC"} Next comment</p>
              {nextComment ? (
                <>
                  <p className="text-sm font-medium text-foreground truncate">{nextComment.profile}</p>
                  <p className="text-[11px] text-muted-foreground truncate">
                    {nextComment.community} &middot; {renderCountdownBadge(nextComment._parsedTs)}
                  </p>
                </>
              ) : (
                <p className="text-xs text-muted-foreground italic">None scheduled</p>
              )}
            </div>
          </div>
        </div>

        {/* Table */}
        <div className="px-6 py-3 border-b border-border bg-muted/20">
          <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Scheduled Actions ({total} scheduled)
          </span>
        </div>

        {total > 0 ? (
          <div className="divide-y divide-border">
            {/* Table header */}
            <div className="grid grid-cols-[2rem_1fr_1.5fr_5.5rem_6rem] gap-2 px-6 py-2 text-[10px] text-muted-foreground uppercase tracking-wide font-medium bg-muted/10">
              <span>#</span>
              <span>Account</span>
              <span>Type</span>
              <span>Date</span>
              <span>Status</span>
            </div>
            {allItems.map((item, idx) => {
              const isFu = isFollowUpItem(item);
              const typeLabel = isFu
                ? `\uD83D\uDCE9 Follow-up DM \u00b7 ${(item as QueueItem).followUpLeadName || "contact"}`
                : `\uD83D\uDCAC Comment \u00b7 ${item.community}`;
              return (
                <div
                  key={item.id}
                  className="grid grid-cols-[2rem_1fr_1.5fr_5.5rem_6rem] gap-2 px-6 py-2.5 items-center hover:bg-muted/10 transition-colors"
                >
                  <span className="text-[11px] text-muted-foreground">{idx + 1}</span>
                  <p className="text-xs font-medium text-foreground truncate">{item.profile}</p>
                  <p className="text-xs text-muted-foreground truncate">{typeLabel}</p>
                  <span className="text-[11px] text-muted-foreground">{formatDate(item._parsedTs)}</span>
                  <span className="text-[11px]">{renderCountdownBadge(item._parsedTs)}</span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="px-6 py-12 text-center">
            <p className="text-sm text-muted-foreground">No scheduled actions</p>
          </div>
        )}
      </div>
    </div>
  );
}
