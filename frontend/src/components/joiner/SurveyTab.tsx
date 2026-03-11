import { useState, useEffect } from "react";
import { Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api } from "./joiner-api";

export function SurveyTab() {
  const [profiles, setProfiles] = useState<any[]>([]);
  const [selectedId, setSelectedId] = useState<string>('');
  const [form, setForm] = useState({ full_name: '', email: '', phone: '', instagram: '', linkedin: '', website: '', bio: '' });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.getProfiles().then(data => {
      setProfiles(data);
      if (data.length > 0) setSelectedId(data[0].id);
    }).catch(console.error);
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    api.getDiscoveryInfo(selectedId).then(data => {
      setForm({
        full_name: data.full_name || '',
        email: data.email || '',
        phone: data.phone || '',
        instagram: data.instagram || '',
        linkedin: data.linkedin || '',
        website: data.website || '',
        bio: data.bio || '',
      });
    }).catch(() => setForm({ full_name: '', email: '', phone: '', instagram: '', linkedin: '', website: '', bio: '' }));
  }, [selectedId]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateDiscoveryInfo(selectedId, form);
      alert('Saved!');
    } catch (err: any) { alert(err.message); }
    setSaving(false);
  };

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-foreground">Survey Info</h2>
        <p className="text-sm text-muted-foreground">Profile info used for auto-filling community surveys</p>
      </div>

      <div className="rounded-lg border border-border bg-card p-6">
        <div className="mb-4 max-w-xs">
          <Label>Select Account</Label>
          <Select value={selectedId} onValueChange={setSelectedId}>
            <SelectTrigger><SelectValue placeholder="Select account" /></SelectTrigger>
            <SelectContent>
              {profiles.map(p => <SelectItem key={p.id} value={p.id}>{p.email}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div><Label>Full Name</Label><Input value={form.full_name} onChange={e => setForm({...form, full_name: e.target.value})} /></div>
          <div><Label>Email</Label><Input value={form.email} onChange={e => setForm({...form, email: e.target.value})} /></div>
          <div><Label>Phone</Label><Input value={form.phone} onChange={e => setForm({...form, phone: e.target.value})} /></div>
          <div><Label>Instagram</Label><Input value={form.instagram} onChange={e => setForm({...form, instagram: e.target.value})} /></div>
          <div><Label>LinkedIn</Label><Input value={form.linkedin} onChange={e => setForm({...form, linkedin: e.target.value})} /></div>
          <div><Label>Website</Label><Input value={form.website} onChange={e => setForm({...form, website: e.target.value})} /></div>
          <div className="md:col-span-2"><Label>Bio</Label><Textarea rows={3} value={form.bio} onChange={e => setForm({...form, bio: e.target.value})} /></div>
        </div>

        <Button className="mt-4 gap-2" onClick={handleSave} disabled={saving || !selectedId}>
          <Save className="h-4 w-4" /> {saving ? 'Saving...' : 'Save'}
        </Button>
      </div>
    </div>
  );
}
