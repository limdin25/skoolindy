import { useState } from "react";
import { useAnalytics, useProfiles } from "@/hooks/useEngageFlow";
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from "recharts";

const dateRanges = ["Last 7 days", "Last 14 days", "Last 30 days"];

export default function AnalyticsPage() {
  const [range, setRange] = useState("Last 7 days");
  const [selectedProfile, setSelectedProfile] = useState("");
  const analyticsQuery = useAnalytics();
  const profilesQuery = useProfiles();

  const analyticsData = analyticsQuery.data ?? { messagesPerDay: [], keywordDistribution: [], profileActivity: [] };
  const profiles = profilesQuery.data ?? [];

  const filteredProfileActivity = selectedProfile
    ? analyticsData.profileActivity.map(day => ({
        day: day.day,
        [selectedProfile]: (day as any)[selectedProfile] || 0,
      }))
    : analyticsData.profileActivity;

  const profileKeys = selectedProfile
    ? [selectedProfile]
    : Object.keys(analyticsData.profileActivity[0] || {}).filter(k => k !== 'day');

  const profileColors: Record<string, string> = {
  'Main User': 'hsl(217, 91%, 53%)',
    'Sarah Chen': 'hsl(217, 91%, 70%)',
    'Mike Johnson': 'hsl(217, 91%, 83%)',
  };

  return (
    <div className="p-4 md:p-6 lg:p-8 pt-16 md:pt-6 lg:pt-8 max-w-7xl">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Analytics</h1>
          <p className="text-sm text-muted-foreground mt-1">Performance metrics and engagement data</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select value={selectedProfile} onChange={e => setSelectedProfile(e.target.value)} className="text-sm px-3 py-2 rounded-lg border border-border bg-background text-foreground">
            <option value="">All Profiles</option>
                {profiles.map(p => <option key={p.id} value={p.name}>{p.name}</option>)}
          </select>
          <select value={range} onChange={e => setRange(e.target.value)} className="text-sm px-3 py-2 rounded-lg border border-border bg-background text-foreground">
            {dateRanges.map(r => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Messages per day */}
        <div className="bg-card border border-border rounded-xl p-5">
          <h3 className="text-sm font-semibold text-foreground mb-4">Messages Per Day</h3>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={analyticsData.messagesPerDay}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 13%, 91%)" />
              <XAxis dataKey="day" tick={{ fontSize: 12, fill: 'hsl(220, 9%, 46%)' }} />
              <YAxis tick={{ fontSize: 12, fill: 'hsl(220, 9%, 46%)' }} />
              <Tooltip contentStyle={{ borderRadius: '8px', border: '1px solid hsl(220, 13%, 91%)', fontSize: '12px' }} />
              <Line type="monotone" dataKey="messages" stroke="hsl(217, 91%, 53%)" strokeWidth={2} dot={{ fill: 'hsl(217, 91%, 53%)', r: 4 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Keyword distribution */}
        <div className="bg-card border border-border rounded-xl p-5">
          <h3 className="text-sm font-semibold text-foreground mb-4">Keyword Distribution</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={analyticsData.keywordDistribution}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 13%, 91%)" />
              <XAxis dataKey="keyword" tick={{ fontSize: 10, fill: 'hsl(220, 9%, 46%)' }} />
              <YAxis tick={{ fontSize: 12, fill: 'hsl(220, 9%, 46%)' }} />
              <Tooltip contentStyle={{ borderRadius: '8px', border: '1px solid hsl(220, 13%, 91%)', fontSize: '12px' }} />
              <Bar dataKey="count" fill="hsl(217, 91%, 53%)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Profile activity stacked */}
        <div className="bg-card border border-border rounded-xl p-5 lg:col-span-2">
          <h3 className="text-sm font-semibold text-foreground mb-4">
            Profile Activity {selectedProfile && <span className="text-muted-foreground font-normal">— {selectedProfile}</span>}
          </h3>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={filteredProfileActivity}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 13%, 91%)" />
              <XAxis dataKey="day" tick={{ fontSize: 12, fill: 'hsl(220, 9%, 46%)' }} />
              <YAxis tick={{ fontSize: 12, fill: 'hsl(220, 9%, 46%)' }} />
              <Tooltip contentStyle={{ borderRadius: '8px', border: '1px solid hsl(220, 13%, 91%)', fontSize: '12px' }} />
              <Legend />
              {profileKeys.map((key, i) => (
                <Bar key={key} dataKey={key} stackId="a" fill={profileColors[key] || `hsl(217, 91%, ${53 + i * 15}%)`} radius={i === profileKeys.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
