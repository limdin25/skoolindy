import { Outlet } from "react-router-dom";
import { useState } from "react";
import { Menu, X } from "lucide-react";
import AppSidebar from "./AppSidebar";
import { useIsMobile } from "@/hooks/use-mobile";

export default function AppLayout() {
  const isMobile = useIsMobile();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="flex min-h-screen w-full bg-background">
      {isMobile ? (
        <>
          {/* Mobile hamburger */}
          <button
            onClick={() => setSidebarOpen(true)}
            className="fixed top-4 left-4 z-40 p-2 rounded-lg bg-card border border-border shadow-md"
          >
            <Menu className="w-5 h-5 text-foreground" />
          </button>

          {/* Mobile overlay */}
          {sidebarOpen && (
            <div className="fixed inset-0 z-50 flex animate-fade-in">
              <div className="absolute inset-0 bg-foreground/20" onClick={() => setSidebarOpen(false)} />
              <div className="relative z-10 animate-slide-in">
                <AppSidebar onNavigate={() => setSidebarOpen(false)} />
              </div>
              <button
                onClick={() => setSidebarOpen(false)}
                className="absolute top-4 right-4 z-20 p-2 rounded-lg bg-card border border-border shadow-md"
              >
                <X className="w-5 h-5 text-foreground" />
              </button>
            </div>
          )}
        </>
      ) : (
        <AppSidebar />
      )}
      <main className="flex-1 min-w-0 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
