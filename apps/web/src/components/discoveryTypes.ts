export interface CandidateClip {
  id: string;
  start_ms: number;
  end_ms: number;
  headline: string;
  hook: string;
  summary: string;
  why_this_works: string;
  ai_quality: number;
  informativeness: number;
  standalone_clarity: number;
  audio_clarity: number;
  replay_signal: number | null;
  signal_source: string;
  final_score: number;
  confidence: number;
  suggested_layout: string | null;
  suggested_subtitle_preset: string | null;
  warnings: string[];
}

export interface CandidateResult {
  status: string;
  error: string | null;
  candidates: CandidateClip[];
  dropped: number;
  model: string | null;
  settings: Record<string, unknown>;
  signal_sources: { transcript?: boolean; audio_energy?: boolean; manual_peaks?: boolean };
  has_transcript: boolean;
}
