import { NavLink as RouterNavLink, useLocation, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  Inbox,
  Users,
  Globe,
  Sparkles,
  Settings,
  ScrollText,
  BarChart3,
  LogOut,
  ChevronDown,
  Zap,
} from "lucide-react";
import { useState } from "react";
import { lock } from "@/lib/lockscreen";
import { useBackend } from "@/context/BackendContext";

const navItems = [
  { title: "Dashboard", path: "/", icon: LayoutDashboard },
  { title: "Inbox", path: "/inbox", icon: Inbox },
  { title: "Profiles", path: "/profiles", icon: Users },
  { title: "Communities", path: "/communities", icon: Globe },
  { title: "Keywords & AI Rules", path: "/keywords", icon: Sparkles },
  { title: "Automation", path: "/automation", icon: Zap },
  { title: "Logs", path: "/logs", icon: ScrollText },
  { title: "Analytics", path: "/analytics", icon: BarChart3 },
];

interface SidebarProps {
  onNavigate?: () => void;
}

export default function AppSidebar({ onNavigate }: SidebarProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const [profileOpen, setProfileOpen] = useState(false);

  const { profiles, communities, conversations } = useBackend();

  const activeProfiles = profiles.filter(p => p.status === "running" || p.status === "ready").length;
  const attentionProfiles = profiles.filter(p =>
    ["idle", "paused", "logged_out", "captcha", "blocked"].includes(String(p.status))
  ).length;
  const totalCommunities = communities.length;
  const activeConversations = conversations.filter(c => !c.isDeletedUi && !c.isArchived);
  const needsReplyConversations = activeConversations.filter((c) => {
    const lastVisible = [...c.messages].reverse().find((m) => !m.isDeletedUi);
    if (!lastVisible) return false;
    return String(lastVisible.sender) !== "outbound";
  }).length;
  const totalConversations = activeConversations.length;

  const getNavExtra = (title: string) => {
    if (title === "Profiles") {
      return (
          <span className="ml-auto flex items-center gap-1 text-[10px] text-sidebar-muted">
          <span className="text-success font-semibold">{activeProfiles}</span>
          <span>/</span>
          <span className="text-warning font-semibold">{attentionProfiles}</span>
        </span>
      );
    }
    if (title === "Communities") {
      return (
        <span className="ml-auto text-[10px] text-sidebar-muted font-medium">{totalCommunities}</span>
      );
    }
    if (title === "Inbox") {
      return (
        <span className="ml-auto flex items-center gap-1.5">
          {needsReplyConversations > 0 && (
            <span className="flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-primary text-[10px] font-semibold text-primary-foreground">
              {needsReplyConversations}
            </span>
          )}
          <span className="text-[10px] text-sidebar-muted font-medium">{totalConversations}</span>
        </span>
      );
    }
    return null;
  };

  return (
    <aside className="flex flex-col w-60 h-screen sticky top-0 bg-sidebar text-sidebar-foreground border-r border-sidebar-border shrink-0 overflow-hidden">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 py-5 border-b border-sidebar-border">
        <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-primary">
          <Zap className="w-4 h-4 text-primary-foreground" />
        </div>
        <span className="text-base font-semibold text-sidebar-accent-foreground tracking-tight">EngageFlow</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {navItems.map((item) => {
          const isActive = location.pathname === item.path || 
            (item.path !== "/" && location.pathname.startsWith(item.path));
          return (
            <RouterNavLink
              key={item.path}
              onClick={onNavigate}
              to={item.path}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150 ${
                isActive
                  ? "bg-sidebar-accent text-sidebar-primary"
                  : "text-sidebar-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
              }`}
            >
              <item.icon className="w-[18px] h-[18px] shrink-0" />
              <span>{item.title}</span>
              {getNavExtra(item.title)}
            </RouterNavLink>
          );
        })}
      </nav>

      {/* Profile */}
      <div className="relative px-3 pb-4">
        <button
          onClick={() => setProfileOpen(!profileOpen)}
          className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm hover:bg-sidebar-accent/60 transition-colors"
        >
          <div className="flex items-center justify-center w-8 h-8 rounded-full bg-sidebar-accent text-xs font-semibold text-sidebar-accent-foreground">
            AR
          </div>
          <div className="flex-1 text-left">
            <p className="text-sm font-medium text-sidebar-accent-foreground">Main User</p>
            <p className="text-xs text-sidebar-muted">Admin</p>
          </div>
          <ChevronDown className={`w-4 h-4 text-sidebar-muted transition-transform ${profileOpen ? 'rotate-180' : ''}`} />
        </button>

        {profileOpen && (
          <div className="absolute bottom-full left-3 right-3 mb-1 bg-sidebar-accent border border-sidebar-border rounded-lg shadow-lg overflow-hidden animate-fade-in">
            <button className="flex items-center gap-2 w-full px-3 py-2.5 text-sm text-sidebar-foreground hover:bg-sidebar-border transition-colors"
              onClick={() => { lock(); navigate("/lock", { replace: true }); }}>
              <LogOut className="w-4 h-4" /> Lock & Logout
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
