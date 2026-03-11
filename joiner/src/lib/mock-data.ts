export type AccountStatus = "Connected" | "Missing Cookies" | "Invalid" | "Expired" | "Connecting" | "Error";

export type AccountRunStatus = "Idle" | "Running" | "Paused" | "Completed" | "Blocked";

export interface Account {
  id: string;
  label: string;
  password: string;
  proxy: string;
  authMethod: string;
  status: AccountStatus;
  lastAction: string;
  cookies: string;
  dailyCap: number;
  surveyCompletion: number;
  lastError: string;
  runStatus: AccountRunStatus;
  nextActionCountdown: number; // seconds, -1 = n/a
  delayAccounts: number;
  delayJoins: number;
  stats: { queued: number; joined: number; pending: number; failed: number; skippedPaid: number };
}

export interface SurveyInfo {
  fullName: string;
  email: string;
  phone: string;
  instagram: string;
  linkedin: string;
  website: string;
  bio: string;
  preferredOption: string;
  lastUpdated: string;
}

export type QueueStatus = "Queued" | "Processing" | "Joined" | "Pending" | "Survey Submitted" | "Skipped Paid" | "Rejected" | "Failed";

export interface QueueItem {
  id: string;
  community: string;
  status: QueueStatus;
  reason: string;
  queuedTime: string;
  finishedTime: string;
}

export interface LogEntry {
  id: string;
  timestamp: string;
  account: string;
  action: string;
  target: string;
  result: string;
  reason: string;
}

export type GlobalStatus = "Idle" | "Running" | "Paused" | "Error";

function getAuthMethod(cookies: string, password: string): string {
  if (cookies && password) return "Cookies + Password";
  if (cookies) return "Cookies";
  if (password) return "Password";
  return "None";
}

export const MOCK_ACCOUNTS: Account[] = [
  {
    id: "1",
    label: "john.doe@email.com",
    password: "s3cret!",
    proxy: "192.168.1.1:8080",
    authMethod: getAuthMethod('{"session":"abc123..."}', "s3cret!"),
    status: "Connected",
    lastAction: "Join attempt — 2 min ago",
    cookies: '{"session":"abc123..."}',
    dailyCap: 20,
    surveyCompletion: 85,
    lastError: "",
    runStatus: "Running",
    nextActionCountdown: 43,
    delayAccounts: 5,
    delayJoins: 3,
    stats: { queued: 2, joined: 12, pending: 5, failed: 1, skippedPaid: 1 },
  },
  {
    id: "2",
    label: "sarah.k@email.com",
    password: "",
    proxy: "10.0.0.1:3128",
    authMethod: getAuthMethod('{"session":"def456..."}', ""),
    status: "Connected",
    lastAction: "Auth test — 15 min ago",
    cookies: '{"session":"def456..."}',
    dailyCap: 15,
    surveyCompletion: 100,
    lastError: "",
    runStatus: "Idle",
    nextActionCountdown: -1,
    delayAccounts: 5,
    delayJoins: 3,
    stats: { queued: 0, joined: 8, pending: 3, failed: 0, skippedPaid: 1 },
  },
  {
    id: "3",
    label: "mike.wilson@email.com",
    password: "mike123",
    proxy: "",
    authMethod: getAuthMethod("", "mike123"),
    status: "Expired",
    lastAction: "Error — 2 days ago",
    cookies: "",
    dailyCap: 20,
    surveyCompletion: 40,
    lastError: "Session expired - cookies need refresh",
    runStatus: "Blocked",
    nextActionCountdown: -1,
    delayAccounts: 5,
    delayJoins: 3,
    stats: { queued: 0, joined: 0, pending: 0, failed: 3, skippedPaid: 0 },
  },
];

export const MOCK_QUEUE: QueueItem[] = [
  { id: "1", community: "growth-hackers-club", status: "Joined", reason: "", queuedTime: "10:15 AM", finishedTime: "10:16 AM" },
  { id: "2", community: "saas-founders-network", status: "Joined", reason: "", queuedTime: "10:16 AM", finishedTime: "10:18 AM" },
  { id: "3", community: "digital-marketing-hub", status: "Processing", reason: "", queuedTime: "10:18 AM", finishedTime: "" },
  { id: "4", community: "startup-accelerator", status: "Queued", reason: "", queuedTime: "10:20 AM", finishedTime: "" },
  { id: "5", community: "freelance-designers", status: "Queued", reason: "", queuedTime: "10:20 AM", finishedTime: "" },
  { id: "6", community: "premium-mastermind", status: "Skipped Paid", reason: "Paid community - $49/mo", queuedTime: "10:12 AM", finishedTime: "10:12 AM" },
  { id: "7", community: "ai-builders-collective", status: "Failed", reason: "Rate limit exceeded - try again later", queuedTime: "10:10 AM", finishedTime: "10:11 AM" },
  { id: "8", community: "copywriting-pros", status: "Survey Submitted", reason: "Awaiting admin approval", queuedTime: "10:08 AM", finishedTime: "" },
];

export const MOCK_LOGS: LogEntry[] = [
  { id: "1", timestamp: "10:18:32", account: "john.doe@email.com", action: "Join Community", target: "digital-marketing-hub", result: "Processing", reason: "" },
  { id: "2", timestamp: "10:18:01", account: "john.doe@email.com", action: "Join Community", target: "saas-founders-network", result: "Success", reason: "" },
  { id: "3", timestamp: "10:16:45", account: "john.doe@email.com", action: "Join Community", target: "growth-hackers-club", result: "Success", reason: "" },
  { id: "4", timestamp: "10:15:12", account: "john.doe@email.com", action: "Validate Cookies", target: "-", result: "Success", reason: "" },
  { id: "5", timestamp: "10:12:30", account: "sarah.k@email.com", action: "Join Community", target: "premium-mastermind", result: "Skipped", reason: "Paid community" },
  { id: "6", timestamp: "10:11:05", account: "sarah.k@email.com", action: "Join Community", target: "ai-builders-collective", result: "Failed", reason: "Rate limit exceeded" },
  { id: "7", timestamp: "10:08:22", account: "sarah.k@email.com", action: "Submit Survey", target: "copywriting-pros", result: "Submitted", reason: "Pending approval" },
  { id: "8", timestamp: "10:05:00", account: "mike.wilson@email.com", action: "Validate Cookies", target: "-", result: "Failed", reason: "Session expired" },
];

export const MOCK_SURVEY: Record<string, SurveyInfo> = {
  "1": { fullName: "John Doe", email: "john.doe@email.com", phone: "+1 555-0123", instagram: "@johndoe", linkedin: "linkedin.com/in/johndoe", website: "johndoe.com", bio: "Growth marketer with 5+ years in SaaS", preferredOption: "Networking", lastUpdated: "Today, 9:45 AM" },
  "2": { fullName: "Sarah Kim", email: "sarah.k@email.com", phone: "+1 555-0456", instagram: "@sarahk", linkedin: "linkedin.com/in/sarahkim", website: "sarahkim.io", bio: "Product designer & startup advisor", preferredOption: "Learning", lastUpdated: "Today, 8:30 AM" },
  "3": { fullName: "", email: "mike.wilson@email.com", phone: "", instagram: "", linkedin: "", website: "", bio: "", preferredOption: "", lastUpdated: "Never" },
};
