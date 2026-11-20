export type VandalismSeverity = "none" | "low" | "medium" | "high";

export interface VandalismAssessment {
  flagged: boolean;
  severity: VandalismSeverity;
  confidence: number;
  reason: string;
  source: "heuristic" | "llm";
}

export interface WikiEdit {
  id: string;
  wiki: string;
  title: string;
  title_url: string;
  diff_url: string | null;
  user: string;
  is_bot: boolean;
  is_anon: boolean;
  is_minor: boolean;
  is_new: boolean;
  namespace: number;
  comment: string;
  old_length: number;
  new_length: number;
  byte_delta: number;
  timestamp: number;
  server_name: string;
  vandalism: VandalismAssessment;
}

export interface TopPageStat {
  title: string;
  wiki: string;
  title_url: string;
  edit_count: number;
  unique_editors: number;
  net_byte_delta: number;
  flagged_count: number;
  last_edit_ts: number;
}

export interface MinuteBucket {
  minute_ts: number;
  label: string;
  count: number;
  flagged_count: number;
}

export interface WikiCount {
  wiki: string;
  count: number;
}

export interface DashboardStats {
  wiki_filter: string | null;
  edits_per_minute: number;
  edits_last_5m: number;
  flagged_last_5m: number;
  human_edits_last_5m: number;
  bot_edits_last_5m: number;
  total_processed: number;
  active_pages_5m: number;
  top_pages: TopPageStat[];
  epm_history: MinuteBucket[];
  available_wikis: WikiCount[];
  stream_mode: "live" | "replay" | string;
  stream_connected: boolean;
  llm_provider: string;
  updated_at: number;
}

export interface PulseSummary {
  wiki_filter: string | null;
  summary: string;
  highlights: string[];
  provider: string;
  generated_at: number;
  edit_sample_size: number;
}

export type ConnectionState = "connecting" | "connected" | "reconnecting";

export type ServerMessage =
  | {
      type: "snapshot";
      stats: DashboardStats;
      recent_edits: WikiEdit[];
      pulse: PulseSummary;
    }
  | {
      type: "edit";
      edit: WikiEdit;
    }
  | {
      type: "stats";
      stats: DashboardStats;
    }
  | {
      type: "pulse";
      pulse: PulseSummary;
    }
  | {
      type: "vandalism_update";
      edit_id: string;
      vandalism: VandalismAssessment;
    }
  | {
      type: "pong";
    };
