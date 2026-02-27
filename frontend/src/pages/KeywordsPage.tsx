import { useEffect, useState } from "react";
import { useKeywordRules, useProfiles } from "@/hooks/useEngageFlow";
import { api } from "@/lib/api";
import type { KeywordRule } from "@/lib/types";
import { Plus, Pencil, X, Sparkles, Users } from "lucide-react";

const defaultPersonas = ["Growth Consultant", "Outreach Specialist", "Product Expert", "Marketing Strategist", "Tech Advisor"];

export default function KeywordsPage() {
  const keywordRulesQuery = useKeywordRules();
  const profilesQuery = useProfiles();

  const profiles = profilesQuery.data ?? [];
  const keywordRules = keywordRulesQuery.data ?? [];

  const [personas, setPersonas] = useState(defaultPersonas);
  const [showPersonaManager, setShowPersonaManager] = useState(false);
  const [newPersonaName, setNewPersonaName] = useState("");
  const [editingPersonaIdx, setEditingPersonaIdx] = useState<number | null>(null);
  const [editingPersonaName, setEditingPersonaName] = useState("");
  const [rules, setRules] = useState<KeywordRule[]>([]);
  const [editing, setEditing] = useState<KeywordRule | null>(null);
  const [showEditor, setShowEditor] = useState(false);

  useEffect(() => {
    setRules(keywordRules);
  }, [keywordRules]);

  const [keywords, setKeywords] = useState<string[]>([]);
  const [keywordInput, setKeywordInput] = useState("");
  const [persona, setPersona] = useState(personas[0]);
  const [commentPrompt, setCommentPrompt] = useState("");
  const [dmPrompt, setDmPrompt] = useState("");
  const [dmMaxReplies, setDmMaxReplies] = useState(3);
  const [dmReplyDelay, setDmReplyDelay] = useState(1);
  const [assignedProfiles, setAssignedProfiles] = useState<string[]>([]);

  const openNew = () => {
    setEditing(null);
    setKeywords([]);
    setKeywordInput("");
    setPersona(personas[0]);
    setCommentPrompt("");
    setDmPrompt("");
    setDmMaxReplies(3);
    setDmReplyDelay(1);
    setAssignedProfiles([]);
    setShowEditor(true);
  };

  const openEdit = (rule: KeywordRule) => {
    setEditing(rule);
    setKeywords(rule.keyword.split(',').map(k => k.trim()).filter(Boolean));
    setKeywordInput("");
    setPersona(rule.persona);
    setCommentPrompt(rule.commentPrompt || rule.promptPreview || "");
    setDmPrompt(rule.dmPrompt || "");
    setDmMaxReplies(rule.dmMaxReplies ?? 3);
    setDmReplyDelay(rule.dmReplyDelay ?? 1);
    setAssignedProfiles(rule.assignedProfileIds);
    setShowEditor(true);
  };

  const handleSave = async () => {
    if (keywords.length === 0) return;
    const keyword = keywords.join(', ');
    const ruleData = {
      keyword,
      persona,
      promptPreview: commentPrompt,
      commentPrompt,
      dmPrompt,
      dmMaxReplies,
      dmReplyDelay,
      assignedProfileIds: assignedProfiles,
    };
    if (editing) {
      await api.updateKeywordRule(editing.id, ruleData);
    } else {
      await api.createKeywordRule({ active: true, ...ruleData });
    }
    await keywordRulesQuery.refetch();
    setShowEditor(false);
  };

  const toggleActive = async (id: string) => {
    const rule = rules.find((r) => r.id === id);
    if (!rule) return;
    await api.updateKeywordRule(id, { active: !rule.active });
    await keywordRulesQuery.refetch();
  };

  const toggleProfileAssign = (profileId: string) => {
    setAssignedProfiles(prev => prev.includes(profileId) ? prev.filter(p => p !== profileId) : [...prev, profileId]);
  };

  return (
    <div className="p-4 md:p-6 lg:p-8 pt-16 md:pt-6 lg:pt-8 max-w-7xl">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Keywords & AI Rules</h1>
          <p className="text-sm text-muted-foreground mt-1">Global keyword triggers and AI persona responses</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => setShowPersonaManager(true)} className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg border border-border text-sm font-medium text-foreground hover:bg-muted transition-colors">
            <Users className="w-4 h-4" /> Manage Personas
          </button>
          <button onClick={openNew} className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors">
            <Plus className="w-4 h-4" /> Add Keyword Rule
          </button>
        </div>
      </div>

      {/* Keywords Table */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border bg-muted/30">
              <th className="text-left px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Keyword</th>
              <th className="text-left px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Persona</th>
              <th className="text-left px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider hidden lg:table-cell">Prompt Preview</th>
              <th className="text-left px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider hidden md:table-cell">Profiles</th>
              <th className="text-center px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Active</th>
              <th className="px-5 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rules.map(rule => (
              <tr key={rule.id} className="hover:bg-muted/20 transition-colors">
                <td className="px-5 py-3.5">
                  <span className="inline-flex items-center gap-1.5 text-sm font-medium text-foreground">
                    <Sparkles className="w-3.5 h-3.5 text-primary" /> {rule.keyword}
                  </span>
                </td>
                <td className="px-5 py-3.5 text-sm text-muted-foreground">{rule.persona}</td>
                <td className="px-5 py-3.5 text-xs text-muted-foreground max-w-xs truncate hidden lg:table-cell">{rule.promptPreview}</td>
                <td className="px-5 py-3.5 hidden md:table-cell">
                  {rule.assignedProfileIds.length === 0 ? (
                    <span className="text-xs text-muted-foreground">All profiles</span>
                  ) : (
                    <div className="flex items-center gap-1">
                      <Users className="w-3 h-3 text-muted-foreground" />
                      <span className="text-xs text-foreground">{rule.assignedProfileIds.length} profile{rule.assignedProfileIds.length > 1 ? 's' : ''}</span>
                    </div>
                  )}
                </td>
                <td className="px-5 py-3.5 text-center">
                  <button
                    onClick={() => toggleActive(rule.id)}
                    className={`relative w-9 h-5 rounded-full transition-colors ${rule.active ? 'bg-primary' : 'bg-muted'}`}
                  >
                    <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-card rounded-full shadow transition-transform ${rule.active ? 'translate-x-4' : ''}`} />
                  </button>
                </td>
                <td className="px-5 py-3.5">
                  <button onClick={() => openEdit(rule)} className="p-1.5 rounded-md hover:bg-muted transition-colors">
                    <Pencil className="w-3.5 h-3.5 text-muted-foreground" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Editor Modal */}
      {showEditor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/20 animate-fade-in" onClick={() => setShowEditor(false)}>
          <div className="bg-card border border-border rounded-2xl w-full max-w-lg p-6 shadow-xl animate-count-up" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-5">
              <h3 className="text-lg font-semibold text-foreground">{editing ? "Edit" : "Add"} Keyword Rule</h3>
              <button onClick={() => setShowEditor(false)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">Keywords</label>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {keywords.map((kw, i) => (
                    <span key={i} className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-primary/10 text-primary text-xs font-medium">
                      {kw}
                      <button onClick={() => setKeywords(prev => prev.filter((_, idx) => idx !== i))} className="hover:text-destructive">
                        <X className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                </div>
                <div className="flex gap-2">
                  <input
                    value={keywordInput}
                    onChange={e => setKeywordInput(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' && keywordInput.trim()) {
                        e.preventDefault();
                        if (!keywords.includes(keywordInput.trim())) {
                          setKeywords(prev => [...prev, keywordInput.trim()]);
                        }
                        setKeywordInput("");
                      }
                    }}
                    className="flex-1 px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    placeholder="Type keyword and press Enter"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      if (keywordInput.trim() && !keywords.includes(keywordInput.trim())) {
                        setKeywords(prev => [...prev, keywordInput.trim()]);
                        setKeywordInput("");
                      }
                    }}
                    className="px-3 py-2 rounded-lg border border-border text-sm font-medium text-foreground hover:bg-muted transition-colors"
                  >
                    Add
                  </button>
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">AI Persona</label>
                <select value={persona} onChange={e => setPersona(e.target.value)} className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground">
                  {personas.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">COMMENT REPLY PROMPT</label>
                <textarea value={commentPrompt} onChange={e => setCommentPrompt(e.target.value)} rows={3} placeholder="Prompt used when replying to a matched post…" className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground resize-none focus:outline-none focus:ring-2 focus:ring-ring" />
              </div>
              <div className="border-t border-border pt-4">
                <label className="text-xs font-semibold text-foreground mb-1 block">DM PROMPT</label>
                <textarea value={dmPrompt} onChange={e => setDmPrompt(e.target.value)} rows={3} placeholder="Prompt used when sending a DM after engagement…" className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground resize-none focus:outline-none focus:ring-2 focus:ring-ring" />
                <div className="flex gap-4 mt-3">
                  <div className="flex-1">
                    <label className="text-xs text-muted-foreground mb-1 block">Max DM Replies</label>
                    <input type="number" min={1} max={20} value={dmMaxReplies} onChange={e => setDmMaxReplies(Number(e.target.value))} className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
                  </div>
                  <div className="flex-1">
                    <label className="text-xs text-muted-foreground mb-1 block">Delay Between Replies (min)</label>
                    <input type="number" min={0.5} step={0.5} value={dmReplyDelay} onChange={e => setDmReplyDelay(Number(e.target.value))} className="w-full px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring" />
                  </div>
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-2 block">Assign to Profiles (optional — empty = all profiles)</label>
                <div className="flex flex-wrap gap-2">
                  {profiles.map(p => (
                    <button
                      key={p.id}
                      onClick={() => toggleProfileAssign(p.id)}
                      className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                        assignedProfiles.includes(p.id) ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:bg-muted/80'
                      }`}
                    >
                      {p.name}
                    </button>
                  ))}
                </div>
              </div>
              <button onClick={handleSave} className="w-full py-2.5 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors">Save Rule</button>
            </div>
          </div>
        </div>
      )}

      {/* Persona Manager Modal */}
      {showPersonaManager && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/20 animate-fade-in" onClick={() => setShowPersonaManager(false)}>
          <div className="bg-card border border-border rounded-2xl w-full max-w-md p-6 shadow-xl animate-count-up" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-5">
              <h3 className="text-lg font-semibold text-foreground">Manage AI Personas</h3>
              <button onClick={() => setShowPersonaManager(false)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="space-y-2 mb-4">
              {personas.map((p, i) => (
                <div key={i} className="flex items-center gap-2 py-2 px-3 bg-muted/30 rounded-lg">
                  {editingPersonaIdx === i ? (
                    <>
                      <input value={editingPersonaName} onChange={e => setEditingPersonaName(e.target.value)}
                        className="flex-1 px-2 py-1 rounded border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                        onKeyDown={e => { if (e.key === 'Enter') { setPersonas(prev => prev.map((pp, idx) => idx === i ? editingPersonaName.trim() || pp : pp)); setEditingPersonaIdx(null); } }} />
                      <button onClick={() => { setPersonas(prev => prev.map((pp, idx) => idx === i ? editingPersonaName.trim() || pp : pp)); setEditingPersonaIdx(null); }}
                        className="p-1 rounded hover:bg-muted text-primary"><Pencil className="w-3.5 h-3.5" /></button>
                    </>
                  ) : (
                    <>
                      <span className="flex-1 text-sm text-foreground">{p}</span>
                      <button onClick={() => { setEditingPersonaIdx(i); setEditingPersonaName(p); }}
                        className="p-1 rounded hover:bg-muted"><Pencil className="w-3.5 h-3.5 text-muted-foreground" /></button>
                      <button onClick={() => setPersonas(prev => prev.filter((_, idx) => idx !== i))}
                        className="p-1 rounded hover:bg-muted"><X className="w-3.5 h-3.5 text-destructive" /></button>
                    </>
                  )}
                </div>
              ))}
            </div>
            <div className="flex gap-2">
              <input value={newPersonaName} onChange={e => setNewPersonaName(e.target.value)} placeholder="New persona name..."
                className="flex-1 px-3 py-2 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                onKeyDown={e => { if (e.key === 'Enter' && newPersonaName.trim()) { setPersonas(prev => [...prev, newPersonaName.trim()]); setNewPersonaName(""); } }} />
              <button onClick={() => { if (newPersonaName.trim()) { setPersonas(prev => [...prev, newPersonaName.trim()]); setNewPersonaName(""); } }}
                className="px-3 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors">Add</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

