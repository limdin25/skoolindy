import { X, ExternalLink, Clock } from "lucide-react";
import { useEffect } from "react";
import { useQueue } from "@/hooks/useEngageFlow";
import type { QueueItem } from "@/lib/types";

interface Props {
  open: boolean;
  onClose: () => void;
  filterProfileId?: string;
}

export default function NextActionsDrawer({ open, onClose, filterProfileId }: Props) {
  const { data: queue = [] } = useQueue();

  const interleaveQueueByProfile = (items: QueueItem[]) => {
    const byProfile = new Map<string, QueueItem[]>();
    const profileOrder: string[] = [];
    for (const item of items) {
      const key = String(item.profileId || item.profile || "");
      if (!byProfile.has(key)) {
        byProfile.set(key, []);
        profileOrder.push(key);
      }
      byProfile.get(key)!.push(item);
    }
    const out: QueueItem[] = [];
    while (true) {
      let tookAny = false;
      for (const key of profileOrder) {
        const bucket = byProfile.get(key);
        if (!bucket || bucket.length === 0) continue;
        const nextItem = bucket.shift();
        if (!nextItem) continue;
        out.push(nextItem);
        tookAny = true;
      }
      if (!tookAny) break;
    }
    return out;
  };

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  const items = filterProfileId
    ? queue.filter(q => q.profileId === filterProfileId)
    : interleaveQueueByProfile(queue);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-foreground/20 animate-fade-in" onClick={onClose}>
      <div className="w-full max-w-xl bg-card border-l border-border h-full overflow-y-auto animate-slide-in" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-5 border-b border-border sticky top-0 bg-card z-10">
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-muted-foreground" />
            <h2 className="text-lg font-semibold text-foreground">Next Scheduled Actions</h2>
            <span className="text-xs text-muted-foreground">({items.length})</span>
          </div>
          <button onClick={onClose} className="p-1 rounded-md hover:bg-muted transition-colors">
            <X className="w-5 h-5 text-muted-foreground" />
          </button>
        </div>

        <div className="divide-y divide-border">
          {items.map((item) => (
            <div key={item.id} className="flex items-center gap-4 px-6 py-3">
              <span className="text-sm font-mono font-semibold text-foreground w-20 shrink-0">{item.scheduledTime}</span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-foreground">{item.profile}</p>
                <p className="text-xs text-muted-foreground truncate">
                  {item.community} · <span className="text-primary">{item.keyword}</span>
                </p>
              </div>
              <a href={item.postId} target="_blank" rel="noreferrer" className="text-primary hover:text-primary/80 shrink-0">
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

