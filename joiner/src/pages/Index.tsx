import { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AccountsTab } from "@/components/AccountsTab";
import { SurveyTab } from "@/components/SurveyTab";
import { QueueTab } from "@/components/QueueTab";
import { LogsTab } from "@/components/LogsTab";
import { Users, FileText, ListChecks, ScrollText } from "lucide-react";

const Index = () => {
  const [activeTab, setActiveTab] = useState("accounts");
  const [logFilterAccount, setLogFilterAccount] = useState<string | undefined>();

  const handleFilterLogs = (account: string) => {
    setLogFilterAccount(account);
    setActiveTab("logs");
  };

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-6xl px-4 py-6 space-y-6">
        <div className="rounded-lg border border-border bg-card p-4">
          <h1 className="text-xl font-semibold text-foreground">Skool Community Join Manager</h1>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
          <TabsList className="bg-card border border-border">
            <TabsTrigger value="accounts" className="gap-2 data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
              <Users className="h-4 w-4" /> Accounts
            </TabsTrigger>
            <TabsTrigger value="survey" className="gap-2 data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
              <FileText className="h-4 w-4" /> Survey Info
            </TabsTrigger>
            <TabsTrigger value="queue" className="gap-2 data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
              <ListChecks className="h-4 w-4" /> Communities & Queue
            </TabsTrigger>
            <TabsTrigger value="logs" className="gap-2 data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
              <ScrollText className="h-4 w-4" /> Live Logs
            </TabsTrigger>
          </TabsList>

          <TabsContent value="accounts" className="mt-4">
            <AccountsTab onFilterLogs={handleFilterLogs} />
          </TabsContent>
          <TabsContent value="survey" className="mt-4">
            <SurveyTab />
          </TabsContent>
          <TabsContent value="queue" className="mt-4">
            <QueueTab />
          </TabsContent>
          <TabsContent value="logs" className="mt-4">
            <LogsTab filterAccount={logFilterAccount} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
};

export default Index;
