import { Fragment, useEffect, useState } from "react";
import { Search, Send, Sparkles, ExternalLink, MessageSquare, Archive, ArchiveRestore, Trash2, Tag, X, Plus, Check, ChevronDown, ArrowLeft } from "lucide-react";
import { useBackend } from "@/context/BackendContext";
import { api } from "@/lib/api";
import type { Conversation, Label } from "@/lib/types";
import { useIsMobile } from "@/hooks/use-mobile";

const tones = ["Friendly", "Authority", "Consultant", "Casual"];
const DAY_IN_MS = 24 * 60 * 60 * 1000;
const ORDINAL_SUFFIXES = ["th", "st", "nd", "rd"] as const;
const UK_TIMEZONE = "Europe/London";
const INBOX_SELECTED_CONVERSATION_KEY = "engageflow_inbox_selected_conversation_id";
const INBOX_SELECTED_CONVERSATION_META_KEY = "engageflow_inbox_selected_conversation_meta";
type PendingOutgoingMessage = {
  id: string;
  text: string;
  timestamp: string;
  status: "sending" | "failed";
};

const getDatePartsInTz = (date: Date, timeZone: string): Record<string, number> => {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const map: Record<string, number> = {};
  for (const p of parts) {
    if (p.type !== "literal") map[p.type] = Number(p.value);
  }
  return map;
};

const zonedToEpoch = (
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  second: number,
  timeZone: string,
): number => {
  const baseUtc = Date.UTC(year, month - 1, day, hour, minute, second);
  let guess = baseUtc;
  for (let i = 0; i < 2; i += 1) {
    const tzParts = getDatePartsInTz(new Date(guess), timeZone);
    const asUtc = Date.UTC(
      Number(tzParts.year || 0),
      Number(tzParts.month || 1) - 1,
      Number(tzParts.day || 1),
      Number(tzParts.hour || 0),
      Number(tzParts.minute || 0),
      Number(tzParts.second || 0),
    );
    const offset = asUtc - guess;
    const adjusted = baseUtc - offset;
    if (adjusted === guess) break;
    guess = adjusted;
  }
  return guess;
};

const parseTimestamp = (value: string | undefined | null): number => {
  if (!value) return 0;
  const trimmed = value.trim().replace(/^•\s*/, "");
  if (!trimmed) return 0;

  const normalized = trimmed
    .replace(/(\d)(am|pm)\b/gi, "$1 $2")
    .replace(/\b(\d{1,2})(st|nd|rd|th)\b/gi, "$1")
    .replace(",", "");
  const normalizedCore = normalized.split(" (")[0].trim().replace(/\s+/g, " ");

  const hasExplicitZone = /(z|[+-]\d{2}:?\d{2}|gmt|utc)$/i.test(normalizedCore);
  const direct = Date.parse(normalizedCore);
  if (!Number.isNaN(direct) && hasExplicitZone) return direct;

  const monthMap: Record<string, number> = {
    jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
    jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
  };
  const monthDateTime = normalizedCore.match(/^([A-Za-z]{3,9})\s+(\d{1,2})\s+(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)$/i);
  if (monthDateTime) {
    const mon = monthMap[monthDateTime[1].slice(0, 3).toLowerCase()] || 1;
    const day = Number(monthDateTime[2]);
    const year = Number(monthDateTime[3]);
    let hour = Number(monthDateTime[4]) % 12;
    const minute = Number(monthDateTime[5]);
    const second = Number(monthDateTime[6] ?? 0);
    if (String(monthDateTime[7]).toUpperCase() === "PM") hour += 12;
    return zonedToEpoch(year, mon, day, hour, minute, second, UK_TIMEZONE);
  }

  const time12hMatch = normalizedCore.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)$/i);
  if (time12hMatch) {
    const nowParts = getDatePartsInTz(new Date(), UK_TIMEZONE);
    let hour = Number(time12hMatch[1]) % 12;
    if (time12hMatch[4].toUpperCase() === "PM") hour += 12;
    return zonedToEpoch(
      Number(nowParts.year || 0),
      Number(nowParts.month || 1),
      Number(nowParts.day || 1),
      hour,
      Number(time12hMatch[2]),
      Number(time12hMatch[3] ?? 0),
      UK_TIMEZONE,
    );
  }

  const time24hMatch = normalizedCore.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/);
  if (!time24hMatch) return 0;

  const nowParts = getDatePartsInTz(new Date(), UK_TIMEZONE);
  return zonedToEpoch(
    Number(nowParts.year || 0),
    Number(nowParts.month || 1),
    Number(nowParts.day || 1),
    Number(time24hMatch[1]),
    Number(time24hMatch[2]),
    Number(time24hMatch[3] ?? 0),
    UK_TIMEZONE,
  );
};

const formatTimeFromTimestamp = (value: string | undefined | null): string => {
  const ts = parseTimestamp(value);
  if (ts) {
    return new Date(ts).toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
      timeZone: UK_TIMEZONE,
    }).replace(/\b(am|pm)\b/i, (m) => m.toUpperCase());
  }
  return String(value || "")
    .replace(/(\d)(am|pm)\b/gi, (_, prefix, meridiem) => `${prefix} ${String(meridiem).toUpperCase()}`)
    .replace(/\b(am|pm)\b/gi, (m) => m.toUpperCase());
};

const formatListAbsoluteTimestamp = (value: string | undefined | null): string => {
  const ts = parseTimestamp(value);
  if (!ts) return formatTimeFromTimestamp(value);
  const date = new Date(ts);
  const monthDay = date.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: UK_TIMEZONE });
  const time = date
    .toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true, timeZone: UK_TIMEZONE })
    .replace(/\b(am|pm)\b/i, (m) => m.toUpperCase());
  return `${monthDay}, ${time}`;
};

const getOrdinalDay = (day: number): string => {
  const mod100 = day % 100;
  const suffix = mod100 >= 11 && mod100 <= 13 ? ORDINAL_SUFFIXES[0] : (ORDINAL_SUFFIXES[day % 10] || ORDINAL_SUFFIXES[0]);
  return `${day}${suffix}`;
};

const formatDayDivider = (ts: number): string => {
  const date = new Date(ts);
  const weekday = date.toLocaleDateString("en-US", { weekday: "short", timeZone: UK_TIMEZONE });
  const month = date.toLocaleDateString("en-US", { month: "short", timeZone: UK_TIMEZONE });
  const day = Number(date.toLocaleDateString("en-US", { day: "numeric", timeZone: UK_TIMEZONE }));
  const year = Number(date.toLocaleDateString("en-US", { year: "numeric", timeZone: UK_TIMEZONE }));
  return `${weekday}, ${month} ${getOrdinalDay(day)} ${year}`;
};

const getDayKey = (ts: number): string => {
  const date = new Date(ts);
  const parts = date.toLocaleDateString("en-CA", { timeZone: UK_TIMEZONE });
  return parts;
};

const formatLastMessageTime = (conv: Conversation): string => {
  const lastVisibleMessage = [...conv.messages].reverse().find((m) => !m.isDeletedUi);
  const sourceTimestamp = lastVisibleMessage?.timestamp || conv.lastMessageTime;
  const ts = parseTimestamp(sourceTimestamp);
  if (!ts) return formatListAbsoluteTimestamp(conv.lastMessageTime);

  const now = Date.now();
  const diffMs = now - ts;
  if (diffMs < 0 || diffMs >= DAY_IN_MS) return formatListAbsoluteTimestamp(sourceTimestamp);

  const diffMinutes = Math.max(1, Math.floor(diffMs / 60000));
  if (diffMinutes < 60) {
    return `${diffMinutes} min ago`;
  }

  const diffHours = Math.floor(diffMinutes / 60);
  return `${diffHours} h ago`;
};

const getConversationLastTimestamp = (conv: Conversation): number => {
  const lastVisibleMessage = [...conv.messages].reverse().find((m) => !m.isDeletedUi);
  const sourceTimestamp = lastVisibleMessage?.timestamp || conv.lastMessageTime;
  return parseTimestamp(sourceTimestamp);
};

const normalizeSearchText = (value: string): string =>
  String(value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();

const matchesSearch = (query: string, fields: Array<string | undefined | null>): boolean => {
  const q = normalizeSearchText(query);
  if (!q) return true;
  const haystack = normalizeSearchText(fields.map((v) => String(v || "")).join(" "));
  if (!haystack) return false;
  if (haystack.includes(q)) return true;
  const tokens = q.split(" ").filter(Boolean);
  return tokens.length > 0 && tokens.every((token) => haystack.includes(token));
};

export default function InboxPage() {
  const isMobile = useIsMobile();
  const { profiles: backendProfiles, conversations: backendConversations, labels: backendLabels, refresh } = useBackend();
  const [conversations, setConversations] = useState<Conversation[]>(backendConversations);
  const [labels, setLabels] = useState<Label[]>(backendLabels);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [filterProfile, setFilterProfile] = useState("");
  const [filterKeyword, setFilterKeyword] = useState("");
  const [filterLabel, setFilterLabel] = useState("");
  const [search, setSearch] = useState("");
  const [reply, setReply] = useState("");
  const [tone, setTone] = useState("Friendly");
  const [aiSuggestPending, setAiSuggestPending] = useState(false);
  const [messages, setMessages] = useState<Conversation["messages"]>(conversations[0]?.messages ?? []);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [viewMode, setViewMode] = useState<'active' | 'archived'>('active');
  const [showLabelManager, setShowLabelManager] = useState(false);
  const [newLabelName, setNewLabelName] = useState("");
  const [showBulkLabel, setShowBulkLabel] = useState(false);
  const [mobileView, setMobileView] = useState<'list' | 'chat'>('list');
  const [pendingOutgoingByConversation, setPendingOutgoingByConversation] = useState<Record<string, PendingOutgoingMessage[]>>({});

  const persistSelectedConversation = (conv: Conversation | null) => {
    if (!conv) {
      sessionStorage.removeItem(INBOX_SELECTED_CONVERSATION_KEY);
      localStorage.removeItem(INBOX_SELECTED_CONVERSATION_KEY);
      localStorage.removeItem(INBOX_SELECTED_CONVERSATION_META_KEY);
      return;
    }
    const id = String(conv.id || "");
    if (id) {
      sessionStorage.setItem(INBOX_SELECTED_CONVERSATION_KEY, id);
      localStorage.setItem(INBOX_SELECTED_CONVERSATION_KEY, id);
    }
    localStorage.setItem(
      INBOX_SELECTED_CONVERSATION_META_KEY,
      JSON.stringify({
        id: conv.id,
        profileId: conv.profileId,
        contactName: conv.contactName,
      }),
    );
  };

  useEffect(() => {
    setConversations(backendConversations);
    setLabels(backendLabels);
    const selectedId = selected?.id
      || sessionStorage.getItem(INBOX_SELECTED_CONVERSATION_KEY)
      || localStorage.getItem(INBOX_SELECTED_CONVERSATION_KEY)
      || "";
    let updatedSelected = selectedId
      ? (backendConversations.find((item) => item.id === selectedId) ?? null)
      : null;
    if (!updatedSelected) {
      try {
        const raw = localStorage.getItem(INBOX_SELECTED_CONVERSATION_META_KEY) || "";
        const meta = raw ? JSON.parse(raw) : null;
        const metaProfileId = String(meta?.profileId || "");
        const metaContact = String(meta?.contactName || "");
        if (metaProfileId && metaContact) {
          updatedSelected = backendConversations.find(
            (item) => item.profileId === metaProfileId && item.contactName === metaContact,
          ) ?? null;
        }
      } catch {
        updatedSelected = null;
      }
    }
    const fallbackSelected = !isMobile ? (backendConversations[0] ?? null) : null;
    const resolved = updatedSelected ?? fallbackSelected;
    setSelected(resolved);
    if (resolved) {
      persistSelectedConversation(resolved);
      setMessages(resolved.messages.filter((m) => !m.isDeletedUi));
    } else {
      persistSelectedConversation(null);
    }
  }, [backendConversations, backendLabels, isMobile]);

  useEffect(() => {
    let active = true;
    api.getConversations(false)
      .then(() => {
        if (!active) return;
        return refresh();
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [refresh]);

  const getAiAuto = (conv?: Conversation | null) => Boolean(conv?.aiAutoEnabled);

  const handleSelect = (conv: Conversation) => {
    setSelected(conv);
    persistSelectedConversation(conv);
    setMessages(conv.messages.filter(m => !m.isDeletedUi));
    setReply("");
    if (isMobile) setMobileView('chat');
  };

  const handleBack = () => {
    setMobileView('list');
  };

  const sendOutgoingMessage = async (conversationId: string, text: string, pendingId?: string) => {
    const normalizedText = text.trim();
    if (!normalizedText) return;
    const id = pendingId || `pending-${conversationId}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    setPendingOutgoingByConversation((prev) => {
      const current = prev[conversationId] || [];
      if (pendingId) {
        return {
          ...prev,
          [conversationId]: current.map((item) => item.id === pendingId ? { ...item, status: "sending" } : item),
        };
      }
      return {
        ...prev,
        [conversationId]: [...current, { id, text: normalizedText, timestamp: new Date().toISOString(), status: "sending" }],
      };
    });

    try {
      const updated = await api.addMessage(conversationId, { text: normalizedText, sender: "outbound" });
      setPendingOutgoingByConversation((prev) => ({
        ...prev,
        [conversationId]: (prev[conversationId] || []).filter((item) => item.id !== id),
      }));
      setSelected(updated);
      setMessages(updated.messages.filter((m) => !m.isDeletedUi));
      await refresh();
    } catch {
      setPendingOutgoingByConversation((prev) => ({
        ...prev,
        [conversationId]: (prev[conversationId] || []).map((item) => item.id === id ? { ...item, status: "failed" } : item),
      }));
    }
  };

  const handleSend = async () => {
    if (!selected) return;
    const text = reply.trim();
    if (!text) return;
    setReply("");
    await sendOutgoingMessage(selected.id, text);
  };

  const handleAISuggest = async () => {
    if (!selected) return;
    setAiSuggestPending(true);
    try {
      const result = await api.aiSuggestMessage(selected.id, {
        tone: (["Friendly", "Authority", "Consultant", "Casual"].includes(tone) ? tone : "Friendly") as "Friendly" | "Authority" | "Consultant" | "Casual",
      });
      if (result?.text?.trim()) {
        setReply(result.text.trim());
      }
    } finally {
      setAiSuggestPending(false);
    }
  };

  const handleToggleAiAuto = async () => {
    if (!selected) return;
    const nextValue = !getAiAuto(selected);
    setSelected((prev) => (prev ? { ...prev, aiAutoEnabled: nextValue } : prev));
    setConversations((prev) => prev.map((item) => (
      item.id === selected.id ? { ...item, aiAutoEnabled: nextValue } : item
    )));
    try {
      await api.patchConversation(selected.id, { aiAutoEnabled: nextValue });
      await refresh();
    } catch {
      setSelected((prev) => (prev ? { ...prev, aiAutoEnabled: !nextValue } : prev));
      setConversations((prev) => prev.map((item) => (
        item.id === selected.id ? { ...item, aiAutoEnabled: !nextValue } : item
      )));
    }
  };

  const handleArchive = async (id: string) => {
    await api.patchConversation(id, { isArchived: true });
    await refresh();
  };

  const handleUnarchive = async (id: string) => {
    await api.patchConversation(id, { isArchived: false });
    await refresh();
  };

  const handleDelete = async (id: string) => {
    await api.deleteConversation(id);
    if (selected?.id === id) {
      persistSelectedConversation(null);
      setSelected(null);
    }
    await refresh();
  };

  const handleSetLabel = async (convId: string, labelId: string | null) => {
    await api.patchConversation(convId, { labelId });
    await refresh();
  };

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]);
  };

  const handleBulkArchive = async () => {
    await Promise.all(selectedIds.map((id) => api.patchConversation(id, { isArchived: true })));
    await refresh();
    setSelectedIds([]);
  };

  const handleBulkDelete = async () => {
    await Promise.all(selectedIds.map((id) => api.deleteConversation(id)));
    await refresh();
    setSelectedIds([]);
  };

  const handleBulkLabel = async (labelId: string) => {
    await Promise.all(selectedIds.map((id) => api.patchConversation(id, { labelId })));
    await refresh();
    setSelectedIds([]);
    setShowBulkLabel(false);
  };

  const handleAddLabel = async () => {
    if (!newLabelName.trim()) return;
    await api.createLabel({ name: newLabelName, color: "bg-primary" });
    await refresh();
    setNewLabelName("");
  };

  const handleDeleteLabel = async (id: string) => {
    await api.deleteLabel(id);
    await refresh();
  };

  const filtered = conversations.filter(c => {
    if (c.isDeletedUi) return false;
    if (viewMode === 'active' && c.isArchived) return false;
    if (viewMode === 'archived' && !c.isArchived) return false;
    if (filterProfile && c.profileId !== filterProfile) return false;
    if (filterKeyword && c.keyword !== filterKeyword) return false;
    if (filterLabel && c.labelId !== filterLabel) return false;
    const s = search.trim();
    if (s) {
      const matchesMeta = matchesSearch(s, [
        c.contactName,
        c.lastMessage,
        c.originGroup,
        c.keyword,
        c.profileName,
      ]);
      const matchesMessages = c.messages.some(m => matchesSearch(s, [m.text]));
      if (!matchesMeta && !matchesMessages) return false;
    }
    return true;
  });
  const sortedFiltered = filtered
    .map((conv, index) => ({ conv, index, ts: getConversationLastTimestamp(conv) }))
    .sort((a, b) => (b.ts - a.ts) || (a.index - b.index))
    .map((item) => item.conv);

  const profiles = backendProfiles.map((p) => ({ id: p.id, name: p.name }));
  const keywords = [...new Set(conversations.map(c => c.keyword))];
  const getLabel = (id: string | null) => labels.find(l => l.id === id);
  const selectedPendingOutgoing = selected ? (pendingOutgoingByConversation[selected.id] || []) : [];

  const showList = !isMobile || mobileView === 'list';
  const showChat = !isMobile || mobileView === 'chat';

  return (
    <div className="flex h-screen overflow-hidden pt-14 md:pt-0">
      {/* LEFT - Conversations List */}
      {showList && (
        <div className={`${isMobile ? 'w-full' : 'w-[380px]'} flex-shrink-0 border-r border-border flex flex-col bg-card overflow-hidden`}>
          <div className="p-4 border-b border-border space-y-3 flex-shrink-0">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold text-foreground">Inbox</h2>
              <button onClick={() => setShowLabelManager(true)} className="p-1.5 rounded-md hover:bg-muted transition-colors" title="Manage Labels">
                <Tag className="w-4 h-4 text-muted-foreground" />
              </button>
            </div>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <input type="text" placeholder="Search messages..." value={search} onChange={e => setSearch(e.target.value)}
                className="w-full pl-9 pr-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
            </div>
            <div className="flex gap-2">
              <select value={filterProfile} onChange={e => setFilterProfile(e.target.value)} className="flex-1 min-w-0 text-xs px-2 py-1.5 rounded-md border border-border bg-background text-foreground">
                <option value="">All Profiles</option>
                {profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <select value={filterKeyword} onChange={e => setFilterKeyword(e.target.value)} className="flex-1 min-w-0 text-xs px-2 py-1.5 rounded-md border border-border bg-background text-foreground">
                <option value="">All Keywords</option>
                {keywords.map(k => <option key={k} value={k}>{k}</option>)}
              </select>
            </div>
            <select value={filterLabel} onChange={e => setFilterLabel(e.target.value)} className="w-full text-xs px-2 py-1.5 rounded-md border border-border bg-background text-foreground">
              <option value="">All Labels</option>
              {labels.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
            <div className="flex rounded-md border border-border overflow-hidden">
              <button onClick={() => setViewMode('active')} className={`flex-1 text-xs px-3 py-1.5 font-medium transition-colors ${viewMode === 'active' ? 'bg-primary text-primary-foreground' : 'bg-background text-muted-foreground hover:bg-muted'}`}>Active</button>
              <button onClick={() => setViewMode('archived')} className={`flex-1 text-xs px-3 py-1.5 font-medium transition-colors ${viewMode === 'archived' ? 'bg-primary text-primary-foreground' : 'bg-background text-muted-foreground hover:bg-muted'}`}>Archived</button>
            </div>
          </div>

          {/* Bulk actions bar */}
          {selectedIds.length > 0 && (
            <div className="px-4 py-2 border-b border-border bg-muted/50 flex items-center gap-2 flex-shrink-0">
              <span className="text-xs text-muted-foreground">{selectedIds.length} selected</span>
              <div className="flex-1" />
              <div className="relative">
                <button onClick={() => setShowBulkLabel(!showBulkLabel)} className="p-1.5 rounded-md hover:bg-muted" title="Set Label"><Tag className="w-3.5 h-3.5 text-muted-foreground" /></button>
                {showBulkLabel && (
                  <div className="absolute top-full right-0 mt-1 w-40 bg-card border border-border rounded-lg shadow-lg z-20 py-1">
                    {labels.map(l => (
                      <button key={l.id} onClick={() => handleBulkLabel(l.id)} className="w-full text-left px-3 py-1.5 text-xs text-foreground hover:bg-muted flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full ${l.color}`} /> {l.name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <button onClick={handleBulkArchive} className="p-1.5 rounded-md hover:bg-muted" title="Archive"><Archive className="w-3.5 h-3.5 text-muted-foreground" /></button>
              <button onClick={handleBulkDelete} className="p-1.5 rounded-md hover:bg-muted" title="Delete"><Trash2 className="w-3.5 h-3.5 text-destructive" /></button>
            </div>
          )}

          <div className="flex-1 overflow-y-auto divide-y divide-border min-h-0">
            {sortedFiltered.map(conv => {
              const label = getLabel(conv.labelId);
              return (
                <div key={conv.id} className="flex items-start w-full overflow-hidden">
                  <div className="flex items-center pt-4 pl-3 flex-shrink-0">
                    <input
                      type="checkbox"
                      checked={selectedIds.includes(conv.id)}
                      onChange={() => toggleSelect(conv.id)}
                      className="w-3.5 h-3.5 rounded border-border accent-primary"
                    />
                  </div>
                  <button
                    onClick={() => handleSelect(conv)}
                    className={`flex-1 min-w-0 text-left px-3 py-3.5 transition-colors overflow-hidden ${
                      selected?.id === conv.id ? "bg-primary/5 relative" : "hover:bg-muted/50"
                    }`}
                  >
                    {selected?.id === conv.id && (
                      <span className="absolute left-2 top-2 bottom-2 w-0.5 rounded-full bg-primary" aria-hidden="true" />
                    )}
                    <div className="flex items-start gap-3 min-w-0">
                      <div className="flex items-center justify-center w-9 h-9 rounded-full bg-primary/10 text-primary text-xs font-semibold shrink-0">
                        {conv.contactName.split(' ').map(n => n[0]).join('')}
                      </div>
                      <div className="flex-1 min-w-0 overflow-hidden">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[15px] font-semibold text-foreground truncate">{conv.contactName}</span>
                          <span className="text-[11px] text-muted-foreground whitespace-nowrap flex-shrink-0">{formatLastMessageTime(conv)}</span>
                        </div>
                        <div className="flex items-center gap-1.5 mt-0.5 min-w-0 overflow-hidden">
                          <span className="inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium bg-primary/10 text-primary whitespace-nowrap flex-shrink-0">{conv.keyword}</span>
                          {label && <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium text-white whitespace-nowrap flex-shrink-0 ${label.color}`}>{label.name}</span>}
                        </div>
                        <p className="text-xs text-muted-foreground mt-1 truncate max-w-full">{conv.lastMessage}</p>
                      </div>
                      {conv.unread && <span className="w-2 h-2 rounded-full bg-primary shrink-0 mt-2 animate-pulse-dot" />}
                    </div>
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* MIDDLE - Chat Window */}
      {showChat && selected && (
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          <div className="px-4 md:px-6 py-4 border-b border-border bg-card flex-shrink-0">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3 min-w-0">
                {isMobile && (
                  <button onClick={handleBack} className="p-1.5 rounded-md hover:bg-muted transition-colors flex-shrink-0">
                    <ArrowLeft className="w-5 h-5 text-foreground" />
                  </button>
                )}
                <div className="min-w-0">
                  <h3 className="text-base font-semibold text-foreground truncate">{selected.contactName}</h3>
                  <p className="text-xs text-muted-foreground mt-0.5 truncate">
                    {selected.profileName} В· {selected.originGroup} В· <span className="text-primary">{selected.keyword}</span>
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-1 flex-shrink-0">
                <LabelDropdown labels={labels} currentLabel={selected.labelId} onSelect={(id) => handleSetLabel(selected.id, id)} />
                {selected.isArchived ? (
                  <button onClick={() => handleUnarchive(selected.id)} className="p-2 rounded-md hover:bg-muted transition-colors" title="Unarchive">
                    <ArchiveRestore className="w-4 h-4 text-primary" />
                  </button>
                ) : (
                  <button onClick={() => handleArchive(selected.id)} className="p-2 rounded-md hover:bg-muted transition-colors" title="Archive">
                    <Archive className="w-4 h-4 text-muted-foreground" />
                  </button>
                )}
                <button onClick={() => handleDelete(selected.id)} className="p-2 rounded-md hover:bg-muted transition-colors" title="Delete">
                  <Trash2 className="w-4 h-4 text-destructive" />
                </button>
              </div>
            </div>
            <div className="flex items-center gap-1.5 mt-2 text-xs text-muted-foreground">
              <MessageSquare className="w-3 h-3 flex-shrink-0" />
              <span>Triggered by comment on: </span>
              <a
                href={selected.commentAttribution.postUrl || "#"}
                target="_blank"
                rel="noreferrer"
                className="text-primary hover:underline inline-flex items-center gap-0.5 truncate"
              >
                {selected.commentAttribution.postTitle} <ExternalLink className="w-3 h-3 flex-shrink-0" />
              </a>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-4 md:px-6 py-4 bg-muted/30 min-h-0">
            <div className="flex flex-col gap-3">
              {[...messages, ...selectedPendingOutgoing.map((item) => ({
                id: item.id,
                text: item.text,
                sender: "outbound" as const,
                timestamp: item.timestamp,
                isDeletedUi: false,
                pendingState: item.status,
              }))].map((msg, index) => {
                const currentTs = parseTimestamp(msg.timestamp);
                const combined = [...messages, ...selectedPendingOutgoing.map((item) => ({
                  id: item.id,
                  text: item.text,
                  sender: "outbound" as const,
                  timestamp: item.timestamp,
                  isDeletedUi: false,
                  pendingState: item.status,
                }))];
                const previousTs = index > 0 ? parseTimestamp(combined[index - 1]?.timestamp) : 0;
                const showDayDivider = Boolean(currentTs) && (!previousTs || getDayKey(currentTs) !== getDayKey(previousTs));

                return (
                  <Fragment key={msg.id}>
                    {showDayDivider && (
                      <div className="flex items-center w-full gap-3 py-1">
                        <div className="h-px bg-border flex-1" />
                        <span className="text-[11px] text-muted-foreground whitespace-nowrap">{formatDayDivider(currentTs)}</span>
                        <div className="h-px bg-border flex-1" />
                      </div>
                    )}

                    <div className={`flex w-full group ${msg.sender === 'outbound' ? 'justify-end' : 'justify-start'}`}>
                      <div className="relative max-w-[85%] md:max-w-[70%]">
                        <div className={`px-4 py-2.5 rounded-2xl text-sm ${
                          msg.sender === 'outbound'
                            ? 'bg-primary text-primary-foreground rounded-br-md'
                            : 'bg-card border border-border text-foreground rounded-bl-md'
                        }`}>
                          <p>{msg.text}</p>
                          <p className={`text-[10px] mt-1 ${msg.sender === 'outbound' ? 'text-primary-foreground/70' : 'text-muted-foreground'}`}>{formatTimeFromTimestamp(msg.timestamp)}</p>
                          {"pendingState" in msg && msg.pendingState === "sending" && (
                            <p className="text-[10px] mt-0.5 text-primary-foreground/70">Sending...</p>
                          )}
                          {"pendingState" in msg && msg.pendingState === "failed" && (
                            <div className="mt-1.5 flex items-center gap-2">
                              <p className="text-[10px] text-destructive">Failed to send</p>
                              {selected && (
                                <button
                                  onClick={() => sendOutgoingMessage(selected.id, msg.text, msg.id)}
                                  className="text-[10px] underline underline-offset-2 text-primary-foreground/90 hover:text-primary-foreground"
                                >
                                  Retry
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  </Fragment>
                );
              })}
            </div>
          </div>

          <div className="px-4 md:px-6 py-4 border-t border-border bg-card flex-shrink-0">
            <div className="flex flex-wrap items-center gap-2 mb-3">
              <select value={tone} onChange={e => setTone(e.target.value)} className="text-xs px-2.5 py-1.5 rounded-md border border-border bg-background text-foreground">
                {tones.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
              <button disabled={aiSuggestPending} onClick={handleAISuggest} className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors disabled:opacity-60">
                <Sparkles className="w-3.5 h-3.5" /> {aiSuggestPending ? "Generating..." : "AI Suggest"}
              </button>
              <button
                onClick={handleToggleAiAuto}
                className="inline-flex items-center gap-1.5 text-xs font-medium px-2 py-1.5 rounded-md border border-border hover:bg-muted transition-colors"
                title="AI Auto: auto-send replies when allowed"
              >
                <span className="text-muted-foreground">AI Auto</span>
                <span className={`relative w-7 h-4 rounded-full transition-colors inline-flex items-center ${getAiAuto(selected) ? 'bg-primary' : 'bg-muted'}`}>
                  <span className={`absolute top-0.5 left-0.5 w-3 h-3 bg-card rounded-full shadow transition-transform ${getAiAuto(selected) ? 'translate-x-3' : ''}`} />
                </span>
              </button>
            </div>
            <div className="flex gap-2">
              <textarea value={reply} onChange={e => setReply(e.target.value)} placeholder="Type your reply..." rows={2}
                className="flex-1 px-4 py-2.5 rounded-xl border border-border bg-background text-sm text-foreground placeholder:text-muted-foreground resize-none focus:outline-none focus:ring-2 focus:ring-ring min-w-0"
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }}} />
              <button onClick={handleSend} className="self-end p-2.5 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 transition-colors flex-shrink-0">
                <Send className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* No selection placeholder for desktop */}
      {showChat && !selected && !isMobile && (
        <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
          Select a conversation to start
        </div>
      )}

      {/* RIGHT - Context Panel (hidden on mobile) */}
      {!isMobile && selected && (
        <div className="w-[340px] flex-shrink-0 border-l border-border bg-card overflow-y-auto hidden lg:block">
          <div className="p-5 space-y-7">
            <div>
              <h4 className="text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.12em] mb-4">Contact Info</h4>
              <div className="space-y-3">
                <InfoRow label="Name" value={selected.contactName} />
                <InfoRow label="Origin Group" value={selected.originGroup} />
                <InfoRow label="Matched Keyword" value={selected.keyword} highlight />
                <InfoRow label="First Interaction" value={selected.contactInfo.firstInteraction} />
                <InfoRow label="Label" value={getLabel(selected.labelId)?.name ?? "No label"} highlight={Boolean(getLabel(selected.labelId))} />
              </div>
            </div>
            <div>
              <h4 className="text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.12em] mb-4">Keyword Context</h4>
              <div className="space-y-3">
                <InfoRow label="Keyword" value={selected.keyword} highlight />
                <div>
                  <p className="text-[11px] text-muted-foreground mb-1">Prompt Preview</p>
                  <p className="text-xs text-foreground bg-muted/35 border border-border/60 rounded-lg p-2.5 leading-relaxed">{selected.keywordContext.promptPreview}</p>
                </div>
              </div>
            </div>
            <div>
              <h4 className="text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.12em] mb-4">Comment Attribution</h4>
              <div className="space-y-3">
                <div>
                  <p className="text-[11px] text-muted-foreground mb-1">Original Comment</p>
                  <p className="text-xs text-foreground bg-muted/35 border border-border/60 rounded-lg p-2.5 leading-relaxed">{selected.commentAttribution.originalComment}</p>
                </div>
                <InfoRow label="Timestamp" value={formatTimeFromTimestamp(selected.commentAttribution.timestamp)} />
                {/^https?:\/\//i.test(selected.commentAttribution.postUrl || "") ? (
                  <a
                    href={selected.commentAttribution.postUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs text-primary hover:underline inline-flex items-center gap-1"
                  >
                    View Post <ExternalLink className="w-3 h-3" />
                  </a>
                ) : (
                  <InfoRow label="View Post" value="—" />
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Label Manager Modal */}
      {showLabelManager && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/20 animate-fade-in" onClick={() => setShowLabelManager(false)}>
          <div className="bg-card border border-border rounded-2xl w-full max-w-sm mx-4 p-6 shadow-xl animate-count-up" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-foreground">Manage Labels</h3>
              <button onClick={() => setShowLabelManager(false)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="space-y-2 mb-4 max-h-60 overflow-y-auto">
              {labels.map(l => (
                <div key={l.id} className="flex items-center justify-between py-1.5 px-2 rounded-lg hover:bg-muted/50">
                  <div className="flex items-center gap-2">
                    <span className={`w-3 h-3 rounded-full ${l.color}`} />
                    <span className="text-sm text-foreground">{l.name}</span>
                  </div>
                  <button onClick={() => handleDeleteLabel(l.id)} className="p-1 rounded hover:bg-muted"><Trash2 className="w-3 h-3 text-destructive" /></button>
                </div>
              ))}
            </div>
            <div className="flex gap-2">
              <input value={newLabelName} onChange={e => setNewLabelName(e.target.value)} placeholder="New label name..." className="flex-1 px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
              <button onClick={handleAddLabel} className="px-3 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90"><Plus className="w-4 h-4" /></button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function InfoRow({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div>
      <p className="text-[11px] text-muted-foreground mb-0.5">{label}</p>
      <p className={`text-sm leading-snug ${highlight ? 'text-primary font-medium' : 'text-foreground'}`}>{value}</p>
    </div>
  );
}

function LabelDropdown({ labels, currentLabel, onSelect }: { labels: Label[]; currentLabel: string | null; onSelect: (id: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const current = labels.find(l => l.id === currentLabel);

  return (
    <div className="relative">
      <button onClick={() => setOpen(!open)} className="p-2 rounded-md hover:bg-muted transition-colors flex items-center gap-1" title="Set label">
        {current ? <span className={`w-2.5 h-2.5 rounded-full ${current.color}`} /> : <Tag className="w-4 h-4 text-muted-foreground" />}
        <ChevronDown className="w-3 h-3 text-muted-foreground" />
      </button>
      {open && (
        <div className="absolute top-full right-0 mt-1 w-40 bg-card border border-border rounded-lg shadow-lg z-20 py-1">
          <button onClick={() => { onSelect(null); setOpen(false); }} className="w-full text-left px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted">No label</button>
          {labels.map(l => (
            <button key={l.id} onClick={() => { onSelect(l.id); setOpen(false); }} className="w-full text-left px-3 py-1.5 text-xs text-foreground hover:bg-muted flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${l.color}`} /> {l.name}
              {currentLabel === l.id && <Check className="w-3 h-3 ml-auto text-primary" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}



