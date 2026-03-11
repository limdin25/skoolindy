import { type AccountStatus, type QueueStatus, type AccountRunStatus } from "@/lib/mock-data";

interface StatusBadgeProps {
  status: AccountStatus | QueueStatus | AccountRunStatus | string;
  dot?: boolean;
}

const statusStyleMap: Record<string, string> = {
  Connected: "status-connected",
  Joined: "status-connected",
  "Survey Submitted": "status-info",
  Success: "status-connected",
  Submitted: "status-info",
  "Missing Cookies": "status-warning",
  Expired: "status-warning",
  "Skipped Paid": "status-warning",
  Skipped: "status-warning",
  Pending: "status-warning",
  Invalid: "status-error",
  Error: "status-error",
  Failed: "status-error",
  Rejected: "status-error",
  Connecting: "status-info",
  Processing: "status-processing",
  Queued: "status-idle",
  Idle: "status-idle",
  Running: "status-info",
  Paused: "status-warning",
  Completed: "status-connected",
  Blocked: "status-error",
};

export function StatusBadge({ status, dot = true }: StatusBadgeProps) {
  const style = statusStyleMap[status] || "status-idle";
  return (
    <span className={`status-badge ${style}`}>
      {dot && <span className="inline-block h-1.5 w-1.5 rounded-full bg-current" />}
      {status}
    </span>
  );
}
