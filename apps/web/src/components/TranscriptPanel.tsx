import React from 'react';
import {
  AudioLines, ChevronLeft, ChevronRight, Loader2
} from 'lucide-react';

export interface TranscriptWord {
  word_id: string;
  token: string;
  source_start_us: number;
  source_end_us: number;
  output_start_us: number | null;
  output_end_us: number | null;
  speaker_id: string | null;
  confidence: number | null;
  caption_group_id: string | null;
  manual_override: boolean;
}

export interface TranscriptionModels {
  models: string[];
  default_model: string;
  device: string;
  compute_type: string;
  cpu_threads: number;
}

/** A pair of buttons that nudge one word boundary by a single frame. */
function NudgeButtons({
  label,
  onBack,
  onForward
}: {
  label: string;
  onBack: () => void;
  onForward: () => void;
}) {
  return (
    <div className="flex items-center gap-1">
      <span className="text-[9px] text-gray-500 mr-1">{label}</span>
      <button
        onClick={onBack}
        className="p-1 rounded bg-[var(--cf-raised)] border border-[var(--cf-line)] text-gray-300 hover:text-white hover:border-[var(--cf-accent)] transition"
        title={`Nudge ${label.toLowerCase()} back 1 frame`}
      >
        <ChevronLeft size={11} />
      </button>
      <button
        onClick={onForward}
        className="p-1 rounded bg-[var(--cf-raised)] border border-[var(--cf-line)] text-gray-300 hover:text-white hover:border-[var(--cf-accent)] transition"
        title={`Nudge ${label.toLowerCase()} forward 1 frame`}
      >
        <ChevronRight size={11} />
      </button>
    </div>
  );
}

/** The scrollable list of transcribed words. */
function WordList({
  words,
  activeWordId,
  selectedWordId,
  onSelectWord,
  onUpdateWord,
  onNudgeWord
}: {
  words: TranscriptWord[];
  activeWordId: string | null;
  selectedWordId: string | null;
  onSelectWord: (word: TranscriptWord) => void;
  onUpdateWord: (wordId: string, patch: Partial<TranscriptWord>) => void;
  onNudgeWord: (wordId: string, edge: 'start' | 'end', direction: number) => void;
}) {
  return (
    <div className="space-y-1">
      {words.map((w, idx) => {
        const isActive = w.word_id === activeWordId;
        const isSelected = w.word_id === selectedWordId;
        return (
          <div
            key={w.word_id}
            className={`rounded-md border transition ${
              isSelected
                ? 'bg-[var(--cf-accent-soft)] border-[var(--cf-accent)]'
                : isActive
                ? 'bg-amber-500/10 border-amber-500/40'
                : 'bg-[var(--cf-panel)] border-[var(--cf-line)] hover:border-gray-600'
            }`}
          >
            <button type="button" aria-pressed={isSelected} onClick={() => onSelectWord(w)} className="flex min-h-9 w-full items-center justify-between gap-2 px-2 text-left">
              <span className="flex items-center gap-2 min-w-0">
                <span className="text-[9px] text-gray-600 font-mono shrink-0">{idx + 1}</span>
                <span
                  className={`text-xs truncate ${isActive ? 'text-amber-300 font-bold' : 'text-gray-200'}`}
                >
                  {w.token}
                </span>
                {w.manual_override && (
                  <span className="text-[8px] px-1 py-0.5 rounded bg-[var(--cf-accent-soft)] text-[var(--cf-accent)] shrink-0">
                    edited
                  </span>
                )}
              </span>
              <span className="text-[11px] text-gray-400 font-mono shrink-0">
                {(w.source_start_us / 1e6).toFixed(2)}s
                {w.confidence !== null && (
                  <span className="ml-1.5 text-gray-600">
                    {Math.round(w.confidence * 100)}%
                  </span>
                )}
              </span>
            </button>

            {/* Word boundary nudge controls + text edit */}
            {isSelected && (
              <div className="p-2 border-t border-[#2a3045] flex flex-wrap items-center gap-2">
                <NudgeButtons
                  label="Start"
                  onBack={() => onNudgeWord(w.word_id, 'start', -1)}
                  onForward={() => onNudgeWord(w.word_id, 'start', 1)}
                />
                <NudgeButtons
                  label="End"
                  onBack={() => onNudgeWord(w.word_id, 'end', -1)}
                  onForward={() => onNudgeWord(w.word_id, 'end', 1)}
                />
                <input
                  type="text"
                  value={w.token}
                  onChange={(e) => onUpdateWord(w.word_id, { token: e.target.value })}
                  className="flex-1 min-w-0 bg-[var(--cf-base)] border border-[var(--cf-line)] focus:border-[var(--cf-accent)] rounded px-2 py-1 text-[11px] text-white focus:outline-none"
                  aria-label="Edit word text"
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/**
 * The Transcript & Karaoke editor panel.
 *
 * Shows the transcribed words with their timings, lets the user edit the text
 * and nudge the word boundaries by single frames, and drives the karaoke
 * highlight in the 9:16 player.
 */
export default function TranscriptPanel({
  words,
  segments,
  activeWordId,
  selectedWordId,
  onSelectWord,
  onUpdateWord,
  onNudgeWord,
  isTranscribing,
  transcribeMessage,
  transcribeError,
  transcribeModel,
  transcribeModels,
  onModelChange,
  transcribeLanguage = 'auto',
  onLanguageChange,
  onTranscribe,
  karaokeEnabled,
  onKaraokeToggle,
  saved,
  canTranscribe
}: {
  words: TranscriptWord[];
  segments: { id: number; start: number; end: number; text: string }[];
  activeWordId: string | null;
  selectedWordId: string | null;
  onSelectWord: (word: TranscriptWord) => void;
  onUpdateWord: (wordId: string, patch: Partial<TranscriptWord>) => void;
  onNudgeWord: (wordId: string, edge: 'start' | 'end', direction: number) => void;
  isTranscribing: boolean;
  transcribeMessage: string | null;
  transcribeError: string | null;
  transcribeModel: string;
  transcribeModels: TranscriptionModels | null;
  onModelChange: (model: string) => void;
  transcribeLanguage?: string;
  onLanguageChange?: (lang: string) => void;
  onTranscribe: () => void;
  karaokeEnabled: boolean;
  onKaraokeToggle: (enabled: boolean) => void;
  saved: boolean;
  canTranscribe: boolean;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-white">Transcript</h2>
        <span className={`text-[10px] ${saved ? 'text-emerald-400' : 'text-amber-400'}`}>
          {saved ? 'Saved' : 'Saving…'}
        </span>
      </div>

      {/* Transcribe controls */}
      <div className="p-2.5 bg-[var(--cf-panel)] border border-[var(--cf-line)] rounded-lg space-y-2">
        <div className="grid grid-cols-2 gap-1.5">
          <div>
            <label htmlFor="transcription-language" className="text-xs text-gray-300 block mb-1">Language</label>
            <select
              id="transcription-language"
              value={transcribeLanguage}
              onChange={(e) => onLanguageChange && onLanguageChange(e.target.value)}
              disabled={isTranscribing}
              className="w-full bg-[var(--cf-raised)] border border-[var(--cf-line)] rounded px-2 py-1 text-[11px] text-gray-200 focus:outline-none focus:border-[var(--cf-accent)] disabled:opacity-50"
            >
              <option value="auto">Auto Detect</option>
              <option value="en">English</option>
              <option value="hi">Hindi (Devanagari)</option>
              <option value="hinglish">Hinglish (Roman Hindi)</option>
            </select>
          </div>
          <div>
            <label htmlFor="transcription-model" className="text-xs text-gray-300 block mb-1">Model size</label>
            <select
              id="transcription-model"
              value={transcribeModel}
              onChange={(e) => onModelChange(e.target.value)}
              disabled={isTranscribing}
              className="w-full bg-[var(--cf-raised)] border border-[var(--cf-line)] rounded px-2 py-1 text-[11px] text-gray-200 focus:outline-none focus:border-[var(--cf-accent)] disabled:opacity-50"
            >
              {(transcribeModels?.models || ['tiny', 'base', 'small']).map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
        </div>
        <p className="text-xs text-gray-400">Choose the spoken language and a larger model for difficult audio. Larger models take longer.</p>
        <div className="flex items-center justify-between gap-2 pt-1">
          <button
            onClick={onTranscribe}
            disabled={isTranscribing || !canTranscribe}
            className="w-full py-1.5 rounded text-[11px] font-semibold bg-[var(--cf-accent-soft)] hover:bg-[var(--cf-accent-soft)] text-white transition disabled:opacity-40 flex items-center justify-center gap-1.5"
          >
            {isTranscribing ? <Loader2 size={12} className="animate-spin" /> : <AudioLines size={12} />}
            {isTranscribing ? 'Transcribing…' : 'Transcribe audio'}
          </button>
        </div>
        {transcribeMessage && (
          <div role="status" className="text-xs text-emerald-400">{transcribeMessage}</div>
        )}
        {transcribeError && (
          <div role="alert" className="text-xs text-red-400 bg-red-500/10 border border-red-500/30 rounded px-2 py-1">
            {transcribeError}
          </div>
        )}
      </div>

      {words.length === 0 ? (
        <div className="text-center py-8 text-gray-500 text-xs">
          {isTranscribing
            ? 'Transcribing…'
            : 'No transcript yet. Run transcription to generate word-level timings.'}
        </div>
      ) : (
        <>
          {/* Karaoke toggle */}
          <label className="flex items-center justify-between text-[11px] text-gray-300 bg-[var(--cf-panel)] border border-[var(--cf-line)] rounded-lg px-2.5 py-2 cursor-pointer">
            <span className="flex items-center gap-1.5">
              <AudioLines size={12} className="text-[var(--cf-accent)]" /> Karaoke Highlight
            </span>
            <input
              type="checkbox"
              checked={karaokeEnabled}
              onChange={(e) => onKaraokeToggle(e.target.checked)}
              className="accent-[var(--cf-accent)] w-3.5 h-3.5"
            />
          </label>

          <WordList
            words={words}
            activeWordId={activeWordId}
            selectedWordId={selectedWordId}
            onSelectWord={onSelectWord}
            onUpdateWord={onUpdateWord}
            onNudgeWord={onNudgeWord}
          />

          {/* Full transcript by segment */}
          {segments.length > 0 && (
            <details className="mt-3 bg-[var(--cf-panel)] border border-[var(--cf-line)] rounded-lg">
              <summary className="px-2.5 py-2 text-[11px] text-gray-400 cursor-pointer select-none hover:text-gray-200">
                Full transcript ({segments.length} segment{segments.length === 1 ? '' : 's'})
              </summary>
              <div className="px-2.5 pb-2.5 space-y-1.5">
                {segments.map((s) => (
                  <div key={s.id} className="text-[11px] text-gray-300">
                    <span className="text-gray-600 font-mono mr-1.5">
                      {s.start.toFixed(1)}s
                    </span>
                    {s.text}
                  </div>
                ))}
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}
