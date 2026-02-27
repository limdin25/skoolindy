export interface Profile {
  id: string;
  name: string;
  password?: string;
  hasPassword?: boolean;
  email?: string;
  proxy?: string;
  proxyStatus?: "connected" | "disconnected" | string;
  avatar: string;
  status: "running" | "paused" | "idle" | "checking" | "ready" | "blocked" | "captcha" | "logged_out" | string;
  dailyUsage: number;
  groupsConnected: number;
}

export interface Community {
  id: string;
  profileId: string;
  name: string;
  url: string;
  dailyLimit: number;
  maxPostAgeDays: number;
  lastScanned: string;
  status: "active" | "paused";
  matchesToday: number;
  actionsToday: number;
  totalScannedPosts: number;
  totalKeywordMatches: number;
}

export interface Label {
  id: string;
  name: string;
  color: string;
}

export interface KeywordRule {
  id: string;
  keyword: string;
  persona: string;
  promptPreview: string;
  commentPrompt?: string;
  dmPrompt?: string;
  dmMaxReplies?: number;
  dmReplyDelay?: number;
  active: boolean;
  assignedProfileIds: string[];
}

export interface AutomationSettings {
  masterEnabled: boolean;
  globalDailyCapPerAccount: number;
  delayMin: number;
  delayMax: number;
  roundsBeforeConnectionRest: number;
  connectionRestMinutes: number;
  activeDays: string[];
  runFrom: string;
  runTo: string;
  postsPerCommunityScanLimit: number;
  preScanEnabled: boolean;
  commentFallbackEnabled: boolean;
  commentFallbackPrompt: string;
  dmFallbackPrompt: string;
  keywordScanningEnabled: boolean;
  scanIntervalMinutes: number;
  postsPerCommunityPerScan: number;
  scanConcurrency: number;
  executionConcurrency: number;
  queuePrefillMaxPerProfilePerPass: number;
  blacklistEnabled: boolean;
  blacklistTerms: string[];
}

export interface QueueItem {
  id: string;
  profile: string;
  profileId: string;
  community: string;
  communityId: string;
  postId: string;
  keyword: string;
  keywordId: string;
  scheduledTime: string;
  scheduledFor: string;
  priorityScore: number;
  countdown: number;
}

export interface LogEntry {
  id: string;
  timestamp: string;
  profile: string;
  status: "success" | "retry" | "error" | "info";
  module?: string;
  action?: string;
  message: string;
  fallbackLevelUsed?: string;
}

export interface ActivityEntry {
  id: string;
  profile: string;
  groupName: string;
  action: string;
  timestamp: string;
  postUrl: string;
}

export interface ContactInfo {
  firstInteraction: string;
}

export interface CommentAttribution {
  postUrl: string;
  originalComment: string;
  timestamp: string;
  postTitle: string;
}

export interface KeywordContext {
  persona: string;
  promptPreview: string;
}

export interface Message {
  id: string;
  text: string;
  sender: "outbound" | "inbound";
  timestamp: string;
  isDeletedUi: boolean;
}

export interface Conversation {
  id: string;
  contactName: string;
  profileId: string;
  profileName: string;
  keyword: string;
  originGroup: string;
  lastMessage: string;
  lastMessageTime: string;
  unread: boolean;
  labelId: string | null;
  isArchived: boolean;
  isDeletedUi: boolean;
  aiAutoEnabled: boolean;
  contactInfo: ContactInfo;
  commentAttribution: CommentAttribution;
  keywordContext: KeywordContext;
  messages: Message[];
}

export interface AnalyticsData {
  messagesPerDay: Array<{ day: string; messages: number }>;
  keywordDistribution: Array<{ keyword: string; count: number }>;
  profileActivity: Array<Record<string, number | string>>;
}
