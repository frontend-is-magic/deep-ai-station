export type TrackId = 'agent' | 'fullstack';
export type Language = 'typescript' | 'python' | 'go';
export type RunWorkflow = 'retrieval' | 'agent';
export type LiveProvider = 'demo' | 'deepseek';
export interface RunTrace {
  id?: string;
  title: string;
  detail: string;
  status: 'running' | 'success' | 'error';
}
export interface Lesson {
  id: string;
  track: TrackId;
  stage: string;
  title: string;
  objective: string;
  minutes: number;
  level: string;
  body: string[];
  steps: string[];
  criteria: string[];
  resources: { title: string; url: string }[];
  quiz: { question: string; options: string[]; answer: number; explanation: string };
  snippets: Partial<Record<Language, string>>;
}
export interface Track {
  id: TrackId;
  title: string;
  description: string;
  languages: Language[];
  stages: { id: string; number: number; title: string; description: string; lessons: string[] }[];
  lessons: Lesson[];
}
export interface FeedItem {
  id: string;
  title: string;
  summary: string;
  source: string;
  url: string;
  track: TrackId;
  tags: string[];
  kind: 'guide' | 'news';
  published: string | null;
}
export interface FeedResponse {
  items: FeedItem[];
  sources: { id: string; name: string; status: string; cached?: boolean }[];
  fetched_at: string;
  mode: string;
}
export interface RunRecord {
  id: string;
  prompt: string;
  answer: string;
  provider: string;
  track: TrackId;
  lesson_id?: string;
  workflow?: RunWorkflow;
  trace?: RunTrace[];
  usage?: Record<string, number> | null;
  usage_complete?: boolean;
  steps?: number;
  tool_count?: number;
  date: string;
  duration_ms: number;
}
export interface EvidenceRecord {
  lesson_id: string;
  language: Language;
  revision: string;
  command: string;
  success: string;
  failure: string;
  pending: string;
  updated_at: string;
}
export interface Progress {
  version: 1;
  completed: string[];
  bookmarks: string[];
  savedItems?: FeedItem[];
  notes: Record<string, string>;
  language: Language;
  runs: RunRecord[];
  evidence?: EvidenceRecord[];
}
export interface Capabilities {
  providers: { id: string; name: string; enabled: boolean; model: string | null }[];
  code_execution: string;
  sandbox?: { enabled: boolean; languages: Language[]; timeout_seconds: number; network: string };
  live_requires_access_token: boolean;
}
