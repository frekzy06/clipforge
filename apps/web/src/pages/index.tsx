import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import Head from 'next/head';
import {
  Film, Settings, Play, Pause,
  Scissors, Download, Upload,
  Volume2, VolumeX, Type,
  Search, Palette, Loader2, ArrowLeft, Trash2, Check
} from 'lucide-react';
import TranscriptPanel, {
  TranscriptWord,
  TranscriptionModels
} from '@/components/TranscriptPanel';
import type {
  CandidateClip,
  CandidateResult
} from '@/components/discoveryTypes';
import SettingsPanel from '@/components/SettingsPanel';
import { LayoutModal, LayoutDef } from '@/components/LayoutModal';
import { LayoutControls, DEFAULT_CORE_LAYOUTS, type FaceRegion, type LayoutAnalysis } from '@/components/LayoutControls';

interface Segment {
  id: string;
  start: number;
  end: number;
  title?: string;
  origin?: 'gemini' | 'local';
  layout_id?: string;
  layout_params?: Record<string, any>;
  caption_preset?: string;
  caption_style?: Partial<CaptionStyle>;
}

type GenerationMode = 'auto' | 'local' | 'highlights' | 'full' | 'transcript' | 'plan';

interface GenerationJob {
  mode: GenerationMode;
  status: 'queued' | 'importing' | 'transcribing' | 'ranking' | 'local' | 'ready' | 'failed';
  detail: string;
  percent: number | null;
  settings: {
    num_clips: number;
    min_duration: number;
    max_duration: number;
    language: string | null;
    model: string | null;
  };
}

function generationPending(job?: GenerationJob | null) {
  return Boolean(job && job.status !== 'ready' && job.status !== 'failed');
}

interface Project {
  id: string;
  name: string;
  video_filename: string | null;
  duration: number;
  width: number;
  height: number;
  timeline?: Segment[];
  caption_preset?: string;
  caption_style?: CaptionStyle;
  caption_text?: string;
  layout_id?: string;
  layout_params?: Record<string, any>;
  export_status?: string;
  export_progress?: number;
  exported_path?: string;
  error_message?: string;
  generation?: GenerationJob | null;
  youtube_url?: string | null;
}

function facecamStyle(face: FaceRegion | null, position: string, sourceAspect: number, outputAspect: number): React.CSSProperties {
  const ratio = outputAspect / sourceAspect;
  const right = position === 'top_right' || position === 'bottom_right';
  const middle = position === 'center' || position === 'left_half';
  const cx = face?.cx ?? (right ? 5 / 6 : position === 'center' ? 0.5 : position === 'left_half' ? 0.25 : 1 / 6);
  const cy = face?.cy ?? (position.startsWith('bottom_') ? 0.75 : middle ? 0.5 : 0.25);
  const width = Math.min(1, ratio, face
    ? Math.max(2.6 * face.w, 2.35 * face.h * ratio)
    : position === 'left_half' ? Math.min(0.5, ratio) : Math.min(1 / 3, ratio / 2));
  const height = width / ratio;
  const x = Math.max(0, Math.min(1 - width, cx - width / 2));
  const y = Math.max(0, Math.min(1 - height, cy - height / 2));
  return { width: `${100 / width}%`, height: `${100 / height}%`, left: `${-100 * x / width}%`, top: `${-100 * y / height}%` };
}

function cropPosition(focusX: number, sourceAspect: number, outputAspect: number) {
  const width = outputAspect / sourceAspect;
  if (width >= 1) return 'center';
  const position = Math.max(0, Math.min(1, (focusX - width / 2) / (1 - width)));
  return `${position * 100}% center`;
}

interface CaptionStyle {
  schema_version: number;
  font_family: string;
  font_weight: number;
  font_size: number;
  uppercase: boolean;
  letter_spacing: number;
  line_spacing: number;
  text_color: string;
  glow_color: string;
  stroke_color: string;
  shadow_color: string;
  karaoke_color?: string;
  caption_mode?: string;
  stroke_width: number;
  shadow_depth: number;
  glow_enabled: boolean;
  glow_radius: number;
  glow_intensity: number;
  alignment: number;
  margin_v: number;
  margin_h: number;
}

interface FontFamily {
  id: string;
  family: string;
  category: string;
  license: string;
  source_url: string;
  weights: number[];
  installed: boolean;
  installed_weights: number[];
  css_family: string;
}

interface CaptionPreset {
  id: string;
  name: string;
  desc: string;
  style: CaptionStyle;
  preview: {
    font_family: string;
    /** Quoted CSS font-family. Never assign the raw name to `style.fontFamily`. */
    css_font_family: string;
    font_weight: number;
    text_color: string;
    glow_color: string;
    text_shadow: string;
    stroke: string;
    /** Scaled variants for the small preset cards. */
    text_shadow_scaled: string;
    stroke_scaled: string;
  };
}

/** A transcribed word with its source (media) timing in microseconds. */
interface MappedWord extends TranscriptWord {
  output_start_us: number;
  output_end_us: number;
}

/** A group of words shown together as one caption. */
interface CaptionGroup {
  id: string;
  text: string;
  output_start_us: number;
  output_end_us: number;
  words: TranscriptWord[];
}

interface TranscriptSegment {
  id: number;
  start: number;
  end: number;
  text: string;
}

const DEFAULT_CAPTION_TEXT = 'DON\u2019T MISS THIS!';

const STYLE_DEFAULTS: CaptionStyle = {
  schema_version: 1,
  font_family: 'CF Anton 400',
  font_weight: 400,
  font_size: 96,
  uppercase: true,
  letter_spacing: 1.5,
  line_spacing: 1.2,
  text_color: '#facc15',
  glow_color: '#facc15',
  stroke_color: '#000000',
  shadow_color: '#000000',
  karaoke_color: '#ffffff',
  caption_mode: 'chunk',
  stroke_width: 3,
  shadow_depth: 2,
  glow_enabled: true,
  glow_radius: 14,
  glow_intensity: 0.9,
  alignment: 2,
  margin_v: 220,
  margin_h: 80,
};

/**
 * Build the CSS text-shadow that mirrors the libass glow used on export.
 * Kept in sync with `caption_style.preview_text_shadow` on the API side.
 *
 * `scale` converts the export-space radii into preview pixels.
 */
function buildGlowShadow(style: CaptionStyle, scale = 1): string {
  const rgba = (hex: string, alpha: number) => {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) return `rgba(255, 255, 255, ${alpha})`;
    const n = parseInt(m[1], 16);
    const r = (n >> 16) & 255;
    const g = (n >> 8) & 255;
    const b = n & 255;
    return `rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(1, alpha)).toFixed(3)})`;
  };
  const num = (v: number) => (Number.isInteger(v) ? String(v) : String(Number(v.toFixed(2))));

  const depth = style.shadow_depth * scale;
  if (!style.glow_enabled || style.glow_radius <= 0) {
    return `${num(depth)}px ${num(depth)}px ${num(
      Math.max(1, depth)
    )}px ${rgba(style.shadow_color, 0.8)}`;
  }
  const radius = style.glow_radius * scale;
  const intensity = style.glow_intensity;
  const layers = [
    `0 0 ${num(radius)}px ${rgba(style.glow_color, intensity)}`,
    `0 0 ${num(radius * 2)}px ${rgba(style.glow_color, intensity * 0.6)}`,
    `0 0 ${num(radius * 3)}px ${rgba(style.glow_color, intensity * 0.35)}`,
  ];
  if (depth > 0) {
    layers.push(
      `${num(depth)}px ${num(depth)}px ${num(
        Math.max(1, depth)
      )}px ${rgba(style.shadow_color, 0.85)}`
    );
  }
  return layers.join(', ');
}

interface FontOption {
  value: string;
  label: string;
  family: string;
  weight: number;
}

/**
 * The caption geometry (font size, stroke, glow) is authored against a
 * 1080x1920 export. The browser preview renders the same caption inside a
 * much smaller box, so every length has to be scaled by the ratio between the
 * preview width and the export width. Without this the stroke is several
 * times too thick and the text turns into a blob.
 */
const EXPORT_WIDTH = 1080;

/** Scale factor from export pixels to preview pixels. */
function previewScale(previewWidth: number): number {
  if (!previewWidth || previewWidth <= 0) return 0.3;
  return Math.max(0.05, Math.min(1.5, previewWidth / EXPORT_WIDTH));
}

/**
 * The caption lengths expressed in preview pixels.
 * `fontSize` is the size of the glyphs; the rest are the outline, glow and
 * shadow radii that surround them.
 */
function scaledCaption(style: CaptionStyle, scale: number) {
  return {
    fontSize: Math.max(6, style.font_size * scale),
    strokeWidth: style.stroke_width * scale,
    glowRadius: style.glow_radius * scale,
    shadowDepth: style.shadow_depth * scale,
    letterSpacing: style.letter_spacing * scale,
  };
}

/**
 * Quote a font family name for a CSS `font-family` value.
 *
 * ClipForge installs each weight under its own family name (`CF Anton 400`),
 * so the name is always multi-word and ends in a digit. Unquoted, that is
 * invalid CSS: the CSSOM rejects the declaration, the browser drops
 * `font-family` entirely and the text silently renders in the system font.
 * Always quote it.
 */
function cssFontFamily(family: string): string {
  const name = (family || '').trim();
  if (!name) return 'inherit';
  return `"${name.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}

/**
 * The font weight to request from the browser.
 *
 * Each installed face declares exactly one weight, so the preview must ask
 * for the weight of the face it selected. Asking for 900 from a face declared
 * as 400 makes the browser synthesise a fake bold and the preview no longer
 * matches the exported MP4.
 */
function cssFontWeight(weight: number): number {
  if (!Number.isFinite(weight)) return 400;
  return Math.max(100, Math.min(900, Math.round(weight)));
}

/** A labelled colour input with a native picker and a hex text field. */
function ColorField({
  label,
  value,
  onChange
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="bg-[var(--cf-panel)] border border-[var(--cf-line)] rounded-lg px-2 py-1.5">
      <label className="text-[10px] text-gray-400 block mb-1">{label}</label>
      <div className="flex items-center gap-1.5">
        <input
          type="color"
          aria-label={`${label} color picker`}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-6 h-6 rounded cursor-pointer bg-transparent p-0 border-0 shrink-0"
          title={`${label} colour`}
        />
        <input
          type="text"
          aria-label={`${label} hex value`}
          value={value}
          onChange={(e) => {
            const v = e.target.value.trim();
            if (/^#?[0-9a-fA-F]{0,6}$/.test(v)) onChange(v);
          }}
          onBlur={(e) => {
            const v = e.target.value.trim();
            if (!/^#?[0-9a-fA-F]{6}$/.test(v)) onChange(value);
          }}
          className="flex-1 bg-transparent text-[11px] text-gray-200 font-mono focus:outline-none min-w-0"
        />
      </div>
    </div>
  );
}

/** A labelled range slider with a live value readout. */
function SliderField({
  label,
  value,
  min,
  max,
  step,
  onChange,
  suffix,
  format
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  suffix?: string;
  format?: (value: number) => string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between text-[11px] text-gray-400 mb-1">
        <span>{label}</span>
        <span className="font-mono text-gray-300">
          {format ? format(value) : `${value}${suffix || ''}`}
        </span>
      </div>
      <input
        type="range"
        aria-label={label}
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-[var(--cf-accent)] h-1"
      />
    </div>
  );
}

/**
 * Renders a caption group with the currently spoken word highlighted.
 *
 * The active word gets an extra glow in karaoke_color so the karaoke emphasis
 * reads clearly against the rest of the caption, which keeps the base caption style.
 */
/**
 * True when the text contains right-to-left script characters.
 *
 * The browser lays out a whole paragraph with the Unicode bidi algorithm, so
 * the preview does not need the token reversal the ASS export needs. It does
 * need the correct base direction, otherwise a line that starts with a
 * Latin word is laid out left-to-right.
 */
function isRtlText(text: string): boolean {
  return /[\u0590-\u05FF\u0600-\u06FF\u0700-\u074F\u0750-\u077F\u0780-\u07BF\u07C0-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]/.test(text);
}

function KaraokeCaption({
  group,
  activeWordId,
  style,
  scale = 1
}: {
  group: CaptionGroup;
  activeWordId: string | null;
  style?: CaptionStyle;
  scale?: number;
}) {
  const karaokeColor = style?.karaoke_color || '#ffffff';
  const rtl = isRtlText(group.text);
  const rgba = (hex: string, alpha: number) => {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) return `rgba(255, 255, 255, ${alpha})`;
    const n = parseInt(m[1], 16);
    const r = (n >> 16) & 255;
    const g = (n >> 8) & 255;
    const b = n & 255;
    return `rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(1, alpha)).toFixed(3)})`;
  };
  const activeGlow = `0 0 ${10 * scale}px ${rgba(karaokeColor, 0.95)}, 0 0 ${22 * scale}px ${rgba(karaokeColor, 0.7)}, 0 0 ${40 * scale}px ${rgba(karaokeColor, 0.45)}`;

  return (
    <>
      {group.words.map((w, i) => {
        const isActive = w.word_id === activeWordId;
        return (
          <span key={w.word_id}>
            {i > 0 ? ' ' : ''}
            <span
              style={
                isActive
                  ? {
                      color: karaokeColor,
                      textShadow: activeGlow,
                      transform: 'scale(1.06)',
                      display: 'inline-block',
                      transition: 'all 90ms ease-out',
                    }
                  : { transition: 'all 90ms ease-out' }
              }
            >
              {w.token}
            </span>
          </span>
        );
      })}
    </>
  );
}
/**
 * The editable caption style panel: colours, glow and typography.
 * Every control writes straight back into the shared caption style, which the
 * preview renders and the exporter burns into the MP4.
 */
function CaptionStyleEditor({
  style,
  text,
  fontOptions,
  saved,
  error,
  onChange,
  onTextChange
}: {
  style: CaptionStyle;
  text: string;
  fontOptions: FontOption[];
  saved: boolean;
  error: string | null;
  onChange: (patch: Partial<CaptionStyle>) => void;
  onTextChange: (text: string) => void;
}) {
  return (
    <div className="border-t border-[var(--cf-line)] pt-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Palette size={14} aria-hidden="true" /> Caption style
        </div>
        <span className={`text-[10px] ${saved ? 'text-emerald-400' : 'text-amber-400'}`}>
          {saved ? 'Saved' : 'Saving...'}
        </span>
      </div>

      {error && (
        <div className="text-[10px] text-red-400 bg-red-500/10 border border-red-500/30 rounded px-2 py-1">
          {error}
        </div>
      )}

      <div>
        <label className="text-[11px] text-gray-400 block mb-1">Caption Mode</label>
        <div className="grid grid-cols-4 gap-1 bg-[var(--cf-base)] p-1 rounded-lg border border-[var(--cf-line)]">
          {[
            { id: '1-word', label: '1-Word' },
            { id: 'chunk', label: '2–4 Words' },
            { id: 'phrase', label: 'Phrase' },
            { id: 'sentence', label: 'Sentence' },
          ].map((mode) => {
            const isSelected = (style.caption_mode || 'chunk') === mode.id;
            return (
              <button
                key={mode.id}
                type="button"
                aria-pressed={isSelected}
                onClick={() => onChange({ caption_mode: mode.id })}
                className={`py-1 text-[11px] font-medium rounded transition text-center ${
                  isSelected
                    ? 'bg-[var(--cf-accent-soft)] text-white shadow-sm'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-[var(--cf-raised)]'
                }`}
              >
                {mode.label}
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <label className="text-[11px] text-gray-400 block mb-1">Caption Text</label>
        <input
          type="text"
          value={text}
          onChange={(e) => onTextChange(e.target.value)}
          placeholder="Your caption text"
          className="w-full bg-[var(--cf-base)] border border-[var(--cf-line)] focus:border-[var(--cf-accent)] rounded px-2.5 py-1.5 text-xs text-white placeholder-gray-500 focus:outline-none"
        />
      </div>

      <div>
        <label className="text-[11px] text-gray-400 block mb-1">Font Family</label>
        <select
          value={style.font_family}
          onChange={(e) => {
            const opt = fontOptions.find(o => o.value === e.target.value);
            onChange({
              font_family: e.target.value,
              font_weight: opt ? opt.weight : style.font_weight
            });
          }}
          className="w-full bg-[var(--cf-raised)] border border-[var(--cf-line)] rounded-lg px-2.5 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-[var(--cf-accent)]"
        >
          {fontOptions.length === 0 && (
            <option value={style.font_family}>
              {style.font_family} (install fonts in the Fonts tab)
            </option>
          )}
          {fontOptions.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <ColorField
          label="Text"
          value={style.text_color}
          onChange={(v) => onChange({ text_color: v })}
        />
        <ColorField
          label="Karaoke Active"
          value={style.karaoke_color || '#ffffff'}
          onChange={(v) => onChange({ karaoke_color: v })}
        />
        <ColorField
          label="Glow"
          value={style.glow_color}
          onChange={(v) => onChange({ glow_color: v })}
        />
        <ColorField
          label="Stroke"
          value={style.stroke_color}
          onChange={(v) => onChange({ stroke_color: v })}
        />
        <div className="col-span-2">
          <ColorField
            label="Shadow"
            value={style.shadow_color}
            onChange={(v) => onChange({ shadow_color: v })}
          />
        </div>
      </div>

      <label className="flex items-center justify-between text-[11px] text-gray-300 bg-[var(--cf-panel)] border border-[var(--cf-line)] rounded-lg px-2.5 py-2 cursor-pointer">
        <span className="flex items-center gap-1.5">
          Glow
        </span>
        <input
          type="checkbox"
          checked={style.glow_enabled}
          onChange={(e) => onChange({ glow_enabled: e.target.checked })}
          className="accent-[var(--cf-accent)] w-3.5 h-3.5"
        />
      </label>

      <SliderField
        label="Glow Radius"
        value={style.glow_radius}
        min={0} max={60} step={1}
        suffix="px"
        onChange={(v) => onChange({ glow_radius: v })}
      />
      <SliderField
        label="Glow Intensity"
        value={style.glow_intensity}
        min={0} max={1} step={0.05}
        format={(v) => `${Math.round(v * 100)}%`}
        onChange={(v) => onChange({ glow_intensity: v })}
      />
      <SliderField
        label="Stroke Width"
        value={style.stroke_width}
        min={0} max={20} step={0.5}
        format={(v) => `${v}px`}
        onChange={(v) => onChange({ stroke_width: v })}
      />
      <SliderField
        label="Font Size"
        value={style.font_size}
        min={8} max={400} step={1}
        suffix="px"
        onChange={(v) => onChange({ font_size: v })}
      />
      <SliderField
        label="Letter Spacing"
        value={style.letter_spacing}
        min={-10} max={50} step={0.5}
        suffix="px"
        onChange={(v) => onChange({ letter_spacing: v })}
      />
    </div>
  );
}

export default function Home() {
  const [currentProject, setCurrentProject] = useState<Project | null>(null);
  const [activeClipId, setActiveClipId] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(false);

  // Navigation tabs
  const [activeTab, setActiveTab] = useState<'clips' | 'captions' | 'fonts' | 'transcript' | 'layout' | 'settings'>('clips');

  // YouTube link paster state
  const [ytUrl, setYtUrl] = useState('');
  const [isImportingYt, setIsImportingYt] = useState(false);
  const [ytError, setYtError] = useState<string | null>(null);
  const [ytBrowser, setYtBrowser] = useState<string>('none');
  const [availableBrowsers, setAvailableBrowsers] = useState<string[]>([]);

  // Transcript / karaoke
  const [words, setWords] = useState<TranscriptWord[]>([]);
  const [mappedWords, setMappedWords] = useState<MappedWord[]>([]);
  const [captionGroups, setCaptionGroups] = useState<CaptionGroup[]>([]);
  const [transcriptSegments, setTranscriptSegments] = useState<TranscriptSegment[]>([]);
  const [transcribeError, setTranscribeError] = useState<string | null>(null);
  const [transcribeModel, setTranscribeModel] = useState<string>('base');
  const [transcribeModels, setTranscribeModels] = useState<TranscriptionModels | null>(null);
  const [selectedWordId, setSelectedWordId] = useState<string | null>(null);
  const [karaokeEnabled, setKaraokeEnabled] = useState(true);
  const [transcriptSaved, setTranscriptSaved] = useState(true);
  const transcriptSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Caption preset + editable style
  const [selectedPresetId, setSelectedPresetId] = useState<string>('hormozi-glow');
  const [captionPresets, setCaptionPresets] = useState<CaptionPreset[]>([]);
  const [captionStyle, setCaptionStyle] = useState<CaptionStyle>(STYLE_DEFAULTS);
  const [captionText, setCaptionText] = useState<string>(DEFAULT_CAPTION_TEXT);
  const [styleSaved, setStyleSaved] = useState(true);
  const [styleError, setStyleError] = useState<string | null>(null);

  // Font library
  const [fontFamilies, setFontFamilies] = useState<FontFamily[]>([]);
  const [fontCategories, setFontCategories] = useState<string[]>([]);
  const [fontSearch, setFontSearch] = useState('');
  const [fontCategory, setFontCategory] = useState<string>('All');
  const [installingFont, setInstallingFont] = useState<string | null>(null);
  const [fontError, setFontError] = useState<string | null>(null);
  const [installAllBusy, setInstallAllBusy] = useState(false);

  // Shorts Layout System controls
  const [layoutsCatalog, setLayoutsCatalog] = useState<LayoutDef[]>(DEFAULT_CORE_LAYOUTS);
  const [layoutCategories, setLayoutCategories] = useState<Record<string, string>>({});
  const [selectedLayoutId, setSelectedLayoutId] = useState<string>('auto_hybrid');
  const [layoutParams, setLayoutParams] = useState<Record<string, any>>({
    crop_x: 0.5,
    zoom: 1.0,
    blur_radius: 25,
    fg_scale: 1.0,
    headline: 'WATCH THIS',
    pip_pos: 'bottom_right',
    pip_size: 'medium',
    speaker_mode: 'auto',
    manual_offset_x: 0.0,
    pad_color: '#000000',
    screen_ratio: 65,
    pan_period: 8.0,
  });
  const [layoutAnalysis, setLayoutAnalysis] = useState<LayoutAnalysis | null>(null);
  const [isLayoutModalOpen, setIsLayoutModalOpen] = useState(false);
  const [fps, setFps] = useState<number>(30);
  const [isExporting, setIsExporting] = useState(false);
  const [exportMessage, setExportMessage] = useState<string | null>(null);
  const [subLanguage, setSubLanguage] = useState<string>('auto');
  const [workspace, setWorkspace] = useState<'review' | 'editor'>('review');
  const editorRef = useRef<HTMLElement>(null);
  const [sourceAction, setSourceAction] = useState('');
  const [trimDraft, setTrimDraft] = useState<{ start: string; end: string } | null>(null);
  const [trimError, setTrimError] = useState('');
  const [savingEdit, setSavingEdit] = useState(false);
  const [reviewed, setReviewed] = useState<Record<string, boolean>>({});

  // AI clip discovery
  const [candidates, setCandidates] = useState<CandidateClip[]>([]);
  const [numClips, setNumClips] = useState(5);
  const [allClips, setAllClips] = useState(false);
  const [durationPreset, setDurationPreset] = useState('30-60');
  const [selectedClipIds, setSelectedClipIds] = useState<string[]>([]);
  const [clipError, setClipError] = useState('');
  const [generationConnection, setGenerationConnection] = useState('');
  const generation = currentProject?.generation;
  const generating = generationPending(generation);
  const transcriptJob = generation?.mode === 'transcript' ? generation : null;
  const isTranscribing = generationPending(transcriptJob);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const bgVideoRef = useRef<HTMLVideoElement | null>(null);
  const topVideoRef = useRef<HTMLVideoElement | null>(null);
  const previewBoxRef = useRef<HTMLDivElement | null>(null);
  // Rendered width of the 9:16 preview box, used to scale the caption
  // geometry from export pixels into preview pixels.
  const [previewWidth, setPreviewWidth] = useState(0);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const openLatestProject = async () => {
    try {
      const res = await fetch('/api/projects');
      if (res.ok) {
        const data: Project[] = await res.json();
        if (data.length > 0) {
          const first = data.find((project) => generationPending(project.generation)) || data[0];
          selectProject(first.id);
        }
      }
    } catch (err) {
      console.error("Failed to load projects", err);
    }
  };

  useEffect(() => {
    openLatestProject();
    loadLayouts();
    loadCaptionPresets();
    loadFonts();
    loadTranscriptionModels();
    loadYouTubeSettings();
  }, []);

  const loadLayouts = async () => {
    try {
      const res = await fetch('/api/layouts');
      if (res.ok) {
        const data = await res.json();
        if (data.layouts) setLayoutsCatalog(data.layouts);
        if (data.categories) setLayoutCategories(data.categories);
      }
    } catch (err) {
      console.error("Failed to load layouts catalog", err);
    }
  };

  // Reload the cached discovery result whenever another project is opened.
  useEffect(() => {
    if (currentProject?.id) {
      setCandidates([]);
      setClipError('');
      setSelectedClipIds([]);
      const request = new AbortController();
      loadCandidates(currentProject.id, request.signal);
      try {
        setReviewed(JSON.parse(localStorage.getItem(`clipforge-review:${currentProject.id}`) || '{}'));
      } catch {
        setReviewed({});
      }
      return () => request.abort();
    }
  }, [currentProject?.id, generation?.status === 'ready']);

  // Track the rendered width of the preview box so the caption geometry can be
  // scaled from export pixels to preview pixels.
  useEffect(() => {
    const box = previewBoxRef.current;
    if (!box || typeof ResizeObserver === 'undefined') return;
    const measure = () => setPreviewWidth(box.getBoundingClientRect().width);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    return () => observer.disconnect();
  }, [currentProject?.video_filename, workspace]);

  const loadCaptionPresets = async () => {
    try {
      const res = await fetch('/api/caption-presets');
      if (res.ok) {
        const data = await res.json();
        setCaptionPresets(data.presets || []);
      }
    } catch (err) {
      console.error("Failed to load caption presets", err);
    }
  };

  const loadFonts = async () => {
    try {
      const res = await fetch('/api/fonts');
      if (res.ok) {
        const data = await res.json();
        setFontFamilies(data.families || []);
        setFontCategories(data.categories || []);
      }
    } catch (err) {
      console.error("Failed to load fonts", err);
    }
  };

  const loadYouTubeSettings = async () => {
    try {
      const res = await fetch('/api/settings/youtube');
      if (res.ok) {
        const data = await res.json();
        if (data.browser) setYtBrowser(data.browser);
        if (Array.isArray(data.available_browsers)) setAvailableBrowsers(data.available_browsers);
      }
    } catch (err) {
      console.error("Failed to load YouTube settings", err);
    }
  };

  const handleYtBrowserChange = async (browser: string) => {
    setYtBrowser(browser);
    try {
      await fetch('/api/settings/youtube', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ browser }),
      });
    } catch (err) {
      console.error("Failed to save YouTube settings", err);
    }
  };

  /** Load the latest discovery result for the open project. */
  const loadCandidates = async (id: string, signal: AbortSignal) => {
    try {
      const res = await fetch(`/api/projects/${id}/candidates`, { signal });
      if (!res.ok) return;
      const data: CandidateResult = await res.json();
      if (signal.aborted) return;
      setCandidates(data.candidates || []);
    } catch (err) {
      if (!signal.aborted) console.error("Failed to load candidates", err);
    }
  };

  /**
   * Persist the caption style + text to the project. Debounced so dragging a
   * colour picker does not fire a request per pixel.
   */
  const saveCaptionStyle = useCallback((style: CaptionStyle, text: string) => {
    if (!currentProject) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      try {
        const res = await fetch(`/api/projects/${currentProject.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ caption_style: style, caption_text: text })
        });
        if (res.ok) {
          setStyleSaved(true);
          setStyleError(null);
        } else {
          const err = await res.json();
          setStyleError(err.detail || 'Failed to save caption style');
        }
      } catch (err: any) {
        setStyleError(err.message || 'Failed to save caption style');
      }
    }, 400);
  }, [currentProject]);

  const updateCaptionStyle = (patch: Partial<CaptionStyle>) => {
    setCaptionStyle((prev) => {
      const next = { ...prev, ...patch };
      setStyleSaved(false);
      saveCaptionStyle(next, captionText);
      return next;
    });
    if (patch.caption_mode) {
      setCaptionGroups(groupWordsIntoCaptions(mappedWords, patch.caption_mode));
    }
  };

  const updateCaptionText = (text: string) => {
    setCaptionText(text);
    setStyleSaved(false);
    saveCaptionStyle(captionStyle, text);
  };

  const installFont = async (familyId: string) => {
    setInstallingFont(familyId);
    setFontError(null);
    try {
      const res = await fetch('/api/fonts/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ family_id: familyId })
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Font installation failed');
      }
      await loadFonts();
      // Reload the generated @font-face rules so the new face is usable.
      await loadFontCss();
    } catch (err: any) {
      setFontError(err.message || 'Font installation failed');
    } finally {
      setInstallingFont(null);
    }
  };

  const installAllFonts = async () => {
    setInstallAllBusy(true);
    setFontError(null);
    try {
      const res = await fetch('/api/fonts/install-all', { method: 'POST' });
      const data = await res.json();
      if (data.failed > 0) {
        setFontError(`Installed ${data.installed} families, ${data.failed} failed.`);
      }
      await loadFonts();
      await loadFontCss();
    } catch (err: any) {
      setFontError(err.message || 'Bulk install failed');
    } finally {
      setInstallAllBusy(false);
    }
  };

  const loadFontCss = async () => {
    try {
      await fetch('/api/fonts/css');
    } catch (err) {
      console.error("Failed to refresh font css", err);
    }
  };

  // -------------------------------------------------------------------------
  // Transcript / karaoke
  // -------------------------------------------------------------------------

  const loadTranscriptionModels = useCallback(async () => {
    try {
      const res = await fetch('/api/transcription/models');
      if (res.ok) {
        const data = await res.json();
        setTranscribeModels(data);
        setTranscribeModel(data.default_model);
      }
    } catch (err) {
      console.error("Failed to load transcription models", err);
    }
  }, []);

  const runTranscription = () => {
    if (!transcriptSaved) return;
    if (words.some((word) => word.manual_override) && !window.confirm('Replace the current transcript and its edited words?')) return;
    setTranscribeError(null);
    void startGeneration(undefined, 'transcript');
  };

  /** Persist edited words back to the project (debounced). */
  const saveWords = useCallback((nextWords: TranscriptWord[]) => {
    if (!currentProject) return;
    if (transcriptSaveTimer.current) clearTimeout(transcriptSaveTimer.current);
    transcriptSaveTimer.current = setTimeout(async () => {
      try {
        const res = await fetch(`/api/projects/${currentProject.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ words: nextWords })
        });
        if (res.ok) {
          setTranscriptSaved(true);
        } else {
          const err = await res.json();
          setTranscribeError(err.detail || 'Failed to save transcript');
        }
      } catch (err: any) {
        setTranscribeError(err.message || 'Failed to save transcript');
      }
    }, 500);
  }, [currentProject]);

  /**
   * Apply an edit to a word and refresh the derived karaoke state.
   * Edits are made on the SOURCE timing; the server re-maps them onto the
   * output timeline so the preview and the export stay in parity.
   */
  const updateWord = (wordId: string, patch: Partial<TranscriptWord>) => {
    const next = words.map((w) =>
      w.word_id === wordId ? { ...w, ...patch, manual_override: true } : w
    );
    setWords(next);
    setTranscriptSaved(false);
    saveWords(next);
    // Re-derive the mapped words/groups locally for instant feedback.
    applyTimelineMapping(next);
  };

  /**
   * Nudge a word boundary by a number of frames.
   * `edge` is 'start' | 'end'; `direction` is -1 or +1.
   */
  const nudgeWord = (wordId: string, edge: 'start' | 'end', direction: number) => {
    const frameUs = Math.round(1_000_000 / fps);
    const word = words.find((w) => w.word_id === wordId);
    if (!word) return;
    const patch: Partial<TranscriptWord> = {};
    if (edge === 'start') {
      const next = Math.max(0, word.source_start_us + direction * frameUs);
      patch.source_start_us = Math.min(next, word.source_end_us - frameUs);
    } else {
      const next = word.source_end_us + direction * frameUs;
      patch.source_end_us = Math.max(next, word.source_start_us + frameUs);
    }
    updateWord(wordId, patch);
  };

  /**
   * Recompute the output-timeline mapping locally, mirroring the server's
   * TimelineMap so the karaoke preview updates without a round trip.
   */
  const applyTimelineMapping = (sourceWords: TranscriptWord[], timeline = clips) => {
    const spans = timeline
      .filter((s) => s.end > s.start)
      .map((s) => ({
        start: Math.round(s.start * 1e6),
        end: Math.round(s.end * 1e6),
      }));
    if (spans.length === 0) {
      setMappedWords([]);
      setCaptionGroups([]);
      return;
    }
    const mapped: MappedWord[] = [];
    let elapsed = 0;
    for (const span of spans) {
      for (const w of sourceWords) {
        const overlapStart = Math.max(w.source_start_us, span.start);
        const overlapEnd = Math.min(w.source_end_us, span.end);
        if (overlapEnd > overlapStart) {
          mapped.push({
            ...w,
            output_start_us: elapsed + (overlapStart - span.start),
            output_end_us: elapsed + (overlapEnd - span.start),
          });
        }
      }
      elapsed += span.end - span.start;
    }
    setMappedWords(mapped);
    setCaptionGroups(groupWordsIntoCaptions(mapped));
  };

  /** Mirror of the server-side caption grouping (1-word, 2-4 words, phrase, sentence). */
  const groupWordsIntoCaptions = (mapped: MappedWord[], mode?: string): CaptionGroup[] => {
    const currentMode = (mode || captionStyle.caption_mode || 'chunk').toLowerCase().trim();
    let effectiveMaxWords = 4;
    let effectiveMaxDurUs = 2_500_000;
    let effectiveMaxPauseUs = 500_000;

    if (currentMode === '1-word') {
      effectiveMaxWords = 1;
      effectiveMaxDurUs = 2_000_000;
      effectiveMaxPauseUs = 500_000;
    } else if (currentMode === 'phrase') {
      effectiveMaxWords = 8;
      effectiveMaxDurUs = 5_000_000;
      effectiveMaxPauseUs = 800_000;
    } else if (currentMode === 'sentence') {
      effectiveMaxWords = 999;
      effectiveMaxDurUs = 12_000_000;
      effectiveMaxPauseUs = 1_200_000;
    }

    const groups: CaptionGroup[] = [];
    let chunk: MappedWord[] = [];

    const flush = () => {
      if (chunk.length === 0) return;
      const id = `cg-${chunk[0].word_id}`;
      groups.push({
        id,
        text: chunk.map((w) => w.token).join(' '),
        output_start_us: chunk[0].output_start_us,
        output_end_us: chunk[chunk.length - 1].output_end_us,
        words: chunk.map((w) => ({ ...w })),
      });
      chunk = [];
    };

    for (const w of mapped) {
      if (chunk.length === 0) {
        chunk.push(w);
        continue;
      }
      const prev = chunk[chunk.length - 1];
      const pause = w.output_start_us - prev.output_end_us;
      const groupDuration = w.output_end_us - chunk[0].output_start_us;
      const isSentenceEnd = /[.!?]['"”’]?$/.test(prev.token.trim());

      const shouldBreak =
        currentMode === '1-word'
          ? true
          : currentMode === 'sentence'
          ? isSentenceEnd || chunk.length >= effectiveMaxWords || groupDuration > effectiveMaxDurUs || pause > effectiveMaxPauseUs
          : chunk.length >= effectiveMaxWords || groupDuration > effectiveMaxDurUs || pause > effectiveMaxPauseUs || isSentenceEnd;

      if (shouldBreak) flush();
      chunk.push(w);
    }
    flush();
    return groups;
  };

  const projectRequest = useRef(0);
  const selectProject = async (id: string, keepClipId?: string | null) => {
    const request = ++projectRequest.current;
    try {
      const res = await fetch(`/api/projects/${id}`, { signal: AbortSignal.timeout(15000) });
      if (res.ok) {
        const data: Project & {
          words?: TranscriptWord[];
          mapped_words?: MappedWord[];
          caption_groups?: CaptionGroup[];
          segments?: TranscriptSegment[];
        } = await res.json();
        if (request !== projectRequest.current) return;
        setCurrentProject(data);
        setTrimDraft(null);
        setTrimError('');
        setSelectedWordId(null);
        setTranscribeError(null);
        setTranscriptSegments(data.segments || []);
        setCaptionStyle({ ...STYLE_DEFAULTS, ...data.caption_style });
        setSelectedPresetId(data.caption_preset || 'hormozi-glow');
        setCaptionText(data.caption_text ?? DEFAULT_CAPTION_TEXT);
        setSelectedLayoutId(data.layout_id || 'auto_hybrid');
        setLayoutParams(data.layout_params || {});
        setLayoutAnalysis(null);
        if (data.generation?.settings && data.generation.mode !== 'transcript') {
          const settings = data.generation.settings;
          setDurationPreset(data.generation.mode === 'full' ? 'full' : `${settings.min_duration}-${settings.max_duration}`);
          setNumClips(settings.num_clips);
          setSubLanguage(settings.language || 'auto');
        }

        // Restore any transcript that was already produced for this project.
        if (Array.isArray(data.words) && data.words.length > 0) {
          setWords(data.words);
          setMappedWords(data.mapped_words || []);
          setCaptionGroups(data.caption_groups || []);
        } else {
          setWords([]);
          setMappedWords([]);
          setCaptionGroups([]);
        }
        setTranscriptSaved(true);
        setStyleSaved(true);
        setStyleError(null);
        if (data.timeline && data.timeline.length > 0) {
          const clip = data.timeline.find((item) => item.id === keepClipId) || data.timeline[0];
          setActiveClipId(clip.id);
          setCurrentTime(clip.start);
          if (clip.layout_id) setSelectedLayoutId(clip.layout_id);
          if (clip.layout_params) setLayoutParams((prev) => ({ ...prev, ...clip.layout_params }));
          if (clip.caption_preset) setSelectedPresetId(clip.caption_preset);
          if (clip.caption_style) setCaptionStyle((prev) => ({ ...prev, ...clip.caption_style }));
          if (videoRef.current) {
            videoRef.current.currentTime = clip.start;
          }
        } else {
          setActiveClipId(null);
          setCurrentTime(0);
        }
        setIsPlaying(false);
        setExportMessage(null);
        return data as Project;
      }
    } catch (err) {
      console.error("Failed to load project details", err);
    }
  };

  const trackedLayout = workspace === 'editor' && !generating &&
    ['smart_face_crop', 'active_speaker_crop', 'split_2_faces', 'split_faces',
      'split_gaming', 'floating_facecam', 'screen_facecam', 'auto_hybrid'].includes(selectedLayoutId);
  const analysisClip = currentProject?.timeline?.find((clip) => clip.id === activeClipId);

  useEffect(() => {
    setLayoutAnalysis(null);
    if (!trackedLayout || !currentProject?.video_filename) return;
    const controller = new AbortController();
    const query = analysisClip ? `?clip_id=${encodeURIComponent(analysisClip.id)}` : '';
    fetch(`/api/projects/${currentProject.id}/layout-analysis${query}`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error('Could not load framing analysis');
        return response.json();
      })
      .then((result) => { if (!controller.signal.aborted) setLayoutAnalysis(result); })
      .catch(() => { if (!controller.signal.aborted) setLayoutAnalysis({ unavailable: true }); });
    return () => controller.abort();
  }, [currentProject?.id, currentProject?.video_filename, trackedLayout, analysisClip?.id, analysisClip?.start, analysisClip?.end]);

  const updateLayoutParams = (newParams: Partial<Record<string, any>>) => {
    setLayoutParams((prev) => {
      const updated = { ...prev, ...newParams };
      if (currentProject) {
        fetch(`/api/projects/${currentProject.id}/layout`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ layout_id: selectedLayoutId, layout_params: updated }),
        }).catch(console.error);
      }
      return updated;
    });
  };

  const handleSelectLayout = (layoutId: string) => {
    setSelectedLayoutId(layoutId);
    if (currentProject) {
      fetch(`/api/projects/${currentProject.id}/layout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ layout_id: layoutId, layout_params: layoutParams }),
      }).catch(console.error);
    }
  };

  const startGeneration = async (url?: string, mode?: GenerationMode) => {
    if (isImportingYt || generating || savingEdit || isExporting || trimDraft) return;
    const reusePlan = url && url === currentProject?.youtube_url && generation?.mode === 'plan' &&
      generation.status === 'ready' && !currentProject.video_filename && durationPreset !== 'full';
    const newUrl = reusePlan ? undefined : url;
    const source = newUrl ? null : currentProject;
    if (!newUrl && !source?.video_filename && !reusePlan) return;
    const selectedMode = mode || (durationPreset === 'full' ? 'full' : newUrl ? 'auto' : 'local');
    const fullDownload = Boolean(reusePlan || (newUrl && selectedMode !== 'plan'));
    setYtError(null);
    setGenerationConnection('');
    if (selectedMode !== 'transcript' && source?.timeline?.length &&
      !window.confirm('Replace the current cuts if generation succeeds? The source video will stay saved.')) return;

    const [minDuration, maxDuration] = (durationPreset === 'full' ? '30-60' : durationPreset).split('-').map(Number);
    const options = {
      mode: selectedMode,
      model: transcribeModel,
      language: subLanguage === 'auto' ? null : subLanguage,
      browser: ytBrowser !== 'none' ? ytBrowser : undefined,
      num_clips: allClips ? 0 : numClips,
      all_clips: allClips,
      min_duration: minDuration,
      max_duration: maxDuration,
      confirm_full_download: fullDownload,
    };
    setIsImportingYt(true);
    setSourceAction('Starting generation…');
    if (selectedMode !== 'transcript') closeEditor();
    try {
      const response = await fetch(newUrl ? '/api/projects/youtube' : `/api/projects/${source?.id}/generation`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          newUrl
            ? { url: newUrl, browser: ytBrowser !== 'none' ? ytBrowser : undefined, generation: options, confirm_full_download: fullDownload }
            : options
        ),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not start generation.');
      const id = newUrl ? data.id : source?.id;
      if (typeof id !== 'string') throw new Error('Could not locate the generation project.');
      const saved = await selectProject(id, selectedMode === 'transcript' ? activeClipId : null);
      if (!saved) throw new Error('The job was started. Reopen the project to check progress.');
      if (selectedMode !== 'transcript' && saved.generation?.status === 'ready' && saved.timeline?.[0]) openEditor(saved.timeline[0]);
    } catch (err) {
      const detail = err instanceof Error ? err.message : 'Could not start generation.';
      if (selectedMode === 'transcript') setTranscribeError(detail);
      else setYtError(detail);
    } finally {
      setIsImportingYt(false);
      setSourceAction('');
    }
  };

  useEffect(() => {
    const sourceUrl = currentProject?.youtube_url;
    if (generation?.status !== 'ready' || !sourceUrl) return;
    if (generation.mode === 'plan') {
      setYtUrl(sourceUrl);
    } else if (currentProject?.video_filename) {
      setYtUrl((value) => value.trim() === sourceUrl ? '' : value);
    }
  }, [currentProject?.id, currentProject?.youtube_url, currentProject?.video_filename, generation?.mode, generation?.status]);

  const handleYouTubeImport = (event: React.FormEvent) => {
    event.preventDefault();
    const url = ytUrl.trim();
    if (url) void startGeneration(url);
    else if (currentProject?.video_filename) void startGeneration();
  };

  useEffect(() => {
    if (!currentProject?.id || !generating) return;
    const controller = new AbortController();
    const id = currentProject.id;
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;

    const poll = async () => {
      try {
        const response = await fetch(`/api/projects/${id}/generation`, {
          cache: 'no-store',
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12000)]),
        });
        if (!response.ok) throw new Error('Could not read generation status.');
        const job: GenerationJob = await response.json();
        if (controller.signal.aborted) return;
        if (generationPending(job)) {
          failures = 0;
          setGenerationConnection('');
          setCurrentProject((previous) => previous?.id === id ? { ...previous, generation: job } : previous);
        } else {
          const saved = await selectProject(id, job.mode === 'transcript' ? activeClipId : null);
          if (!saved) throw new Error('Could not load completed project.');
          setGenerationConnection('');
          if (!controller.signal.aborted && job.mode !== 'transcript' && job.status === 'ready' && saved.timeline?.[0]) {
            openEditor(saved.timeline[0]);
          }
          return;
        }
      } catch {
        if (controller.signal.aborted) return;
        if (++failures >= 3) setGenerationConnection('Lost contact with the server. Reconnecting…');
      }
      timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [currentProject?.id, generating]);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0 || generating) return;
    const file = e.target.files[0];

    e.target.value = '';
    setYtError(null);
    setIsImportingYt(true);
    setSourceAction('Importing local video…');
    const formData = new FormData();
    formData.append('name', file.name.replace(/\.[^/.]+$/, ''));
    formData.append('file', file);
    try {
      const res = await fetch('/api/projects', { method: 'POST', body: formData });
      const project = await res.json();
      if (!res.ok) throw new Error(String(project.detail || 'Upload failed.'));
      await selectProject(project.id);
      setActiveTab('clips');
      setWorkspace('review');
    } catch (err) {
      setYtError(err instanceof Error ? err.message : 'Upload failed.');
    } finally {
      setIsImportingYt(false);
      setSourceAction('');
    }
  };

  const togglePlay = () => {
    if (!videoRef.current) return;
    if (isPlaying) {
      videoRef.current.pause();
      bgVideoRef.current?.pause();
      topVideoRef.current?.pause();
    } else {
      if (previewRange && (videoRef.current.currentTime < previewRange.start || videoRef.current.currentTime >= previewRange.end)) {
        videoRef.current.currentTime = previewRange.start;
      }
      // `play()` rejects when the media is not ready yet; swallow it so it
      // never shows up as an unhandled promise rejection.
      videoRef.current.play().catch(() => {
        setIsPlaying(false);
      });
      bgVideoRef.current?.play().catch(() => {});
      topVideoRef.current?.play().catch(() => {});
    }
    setIsPlaying(!isPlaying);
  };

  const handleTimeUpdate = () => {
    if (videoRef.current) {
      const t = videoRef.current.currentTime;
      setCurrentTime(t);

      if (bgVideoRef.current && Math.abs(bgVideoRef.current.currentTime - t) > 0.15) {
        bgVideoRef.current.currentTime = t;
      }
      if (topVideoRef.current && Math.abs(topVideoRef.current.currentTime - t) > 0.15) {
        topVideoRef.current.currentTime = t;
      }

      if (previewRange && isPlaying && (t < previewRange.start || t >= previewRange.end)) {
        videoRef.current.currentTime = previewRange.start;
      }
    }
  };

  const handleSplit = async () => {
    if (!currentProject || !currentProject.timeline || currentProject.timeline.length === 0) return;
    const t = currentTime;
    const segIdx = currentProject.timeline.findIndex(s => s.id === activeClipId && t > s.start + 0.1 && t < s.end - 0.1);
    if (segIdx === -1) return;

    const seg = currentProject.timeline[segIdx];
    const newSeg1: Segment = { id: `clip-${Date.now()}-1`, start: seg.start, end: t, title: `${seg.title || 'Clip'} (Part A)`, origin: seg.origin };
    const newSeg2: Segment = { id: `clip-${Date.now()}-2`, start: t, end: seg.end, title: `${seg.title || 'Clip'} (Part B)`, origin: seg.origin };

    const newTimeline = [
      ...currentProject.timeline.slice(0, segIdx),
      newSeg1,
      newSeg2,
      ...currentProject.timeline.slice(segIdx + 1)
    ];

    try {
      await updateTimeline(newTimeline);
      openEditor(newSeg2);
    } catch (err) {
      setTrimError(err instanceof Error ? err.message : 'Could not split clip.');
    }
  };

  const updateTimeline = async (timeline: Segment[]) => {
    if (!currentProject) return;
    const response = await fetch(`/api/projects/${currentProject.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ timeline })
    });
    if (!response.ok) throw new Error('Could not save the timeline.');
    setCurrentProject({ ...currentProject, timeline });
    applyTimelineMapping(words, timeline);
    setSelectedClipIds((previous) => previous.filter((id) => timeline.some((clip) => clip.id === id)));
  };

  const selectCaptionPreset = async (presetId: string) => {
    setSelectedPresetId(presetId);
    // Applying a preset resets the editable style to that preset's values.
    const preset = captionPresets.find(p => p.id === presetId);
    if (preset) {
      const next = { ...STYLE_DEFAULTS, ...preset.style };
      setCaptionStyle(next);
      setStyleSaved(false);
      saveCaptionStyle(next, captionText);
      if (next.caption_mode) {
        setCaptionGroups(groupWordsIntoCaptions(mappedWords, next.caption_mode));
      }
    }
    if (currentProject) {
      setCurrentProject({ ...currentProject, caption_preset: presetId });
      await fetch(`/api/projects/${currentProject.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caption_preset: presetId })
      });
    }
  };

  const handleExport = async (clip?: Segment) => {
    if (!currentProject || isExporting || savingEdit || trimDraft) return;
    setIsExporting(true);
    setExportMessage('Rendering 9:16 MP4…');
    const exportSpans = clip ? [clip] : currentProject.timeline || [];
    const firstExportedWord = words.find((word) => exportSpans.some((span) =>
      word.source_start_us < span.end * 1e6 && word.source_end_us > span.start * 1e6
    ));
    // The existing renderer enables subtitles with non-empty caption text, then uses saved word timings.
    const exportCaptionText = captionText.trim() || firstExportedWord?.token.trim() || '';
    try {
      const res = await fetch(`/api/projects/${currentProject.id}/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          clip_id: clip?.id,
          crop_mode: clip ? selectedLayoutId : (currentProject.layout_id || selectedLayoutId),
          layout_id: clip ? selectedLayoutId : undefined,
          layout_params: clip ? layoutParams : undefined,
          headline_text: clip ? (layoutParams.headline || clip.title || currentProject.name || undefined) : undefined,
          fps: Number(fps),
          caption_preset: clip ? selectedPresetId : (currentProject.caption_preset || selectedPresetId),
          caption_style: clip ? captionStyle : undefined,
          caption_text: exportCaptionText
        })
      });
      if (res.ok) {
        const result = await fetch(`/api/projects/${currentProject.id}`);
        if (!result.ok) throw new Error('Could not verify the export.');
        const project: Project = await result.json();
        setCurrentProject((previous) => previous?.id === project.id ? {
          ...previous, export_status: project.export_status, exported_path: project.exported_path,
        } : previous);
        setExportMessage(project.export_status === 'completed' ? 'Export ready.' : project.error_message || 'Export was requested.');
      } else {
        const err = await res.json();
        setExportMessage(`Export failed: ${err.detail || 'Unknown error'}`);
      }
    } catch (err: any) {
      setExportMessage(`Export error: ${err.message}`);
    } finally {
      setIsExporting(false);
    }
  };

  const duration = currentProject?.duration || 1;
  const clips = useMemo(() => currentProject?.timeline || [], [currentProject?.timeline]);
  const clipTranscripts = useMemo(() => new Map(clips.map((clip) => [
    clip.id, words
      .filter((word) => word.source_start_us < clip.end * 1e6 && word.source_end_us > clip.start * 1e6)
      .map((word) => word.token).join(' '),
  ])), [clips, words]);
  // Scale the export-space caption geometry into the preview box.
  const captionScale = previewScale(previewWidth);
  const captionPx = scaledCaption(captionStyle, captionScale);
  const glowShadow = buildGlowShadow(captionStyle, captionScale);
  const installedFonts = fontFamilies.filter(f => f.installed);

  // Fonts available in the caption font picker: installed families only, one
  // entry per installed weight.
  const captionFontOptions = installedFonts.flatMap((f) =>
    f.installed_weights.map((w) => ({
      value: `CF ${f.family} ${w}`,
      label: `${f.family} ${w}`,
      family: f.family,
      weight: w
    }))
  );

  const filteredFonts = fontFamilies.filter((f) => {
    const matchesSearch =
      !fontSearch ||
      f.family.toLowerCase().includes(fontSearch.toLowerCase()) ||
      f.id.toLowerCase().includes(fontSearch.toLowerCase());
    const matchesCategory = fontCategory === 'All' || f.category === fontCategory;
    return matchesSearch && matchesCategory;
  });

  // -------------------------------------------------------------------------
  // Karaoke state
  // -------------------------------------------------------------------------

  // The word currently being spoken, in OUTPUT timeline time (seconds).
  // The preview plays the source media, so the output time is mapped back to
  // the source time to find the active word.
  const activeOutputTime = useMemo(() => {
    // Walk the timeline spans to convert the current source time into the
    // output position.
    const spans = clips
      .filter((s) => s.end > s.start)
      .map((s) => ({ start: s.start, end: s.end }));
    let elapsed = 0;
    for (const span of spans) {
      if (currentTime >= span.start && currentTime <= span.end) {
        return elapsed + (currentTime - span.start);
      }
      elapsed += span.end - span.start;
    }
    // Outside every span (e.g. in a cut region): fall back to the raw time.
    return currentTime;
  }, [currentTime, clips]);

  /** The caption group visible at the current output time. */
  const activeGroup = useMemo(() => {
    if (captionGroups.length === 0) return null;
    const t = activeOutputTime * 1e6;
    return captionGroups.find((group) => t >= group.output_start_us && t < group.output_end_us) || null;
  }, [captionGroups, activeOutputTime]);

  /** The word inside the active group that is currently being spoken. */
  const activeWordId = useMemo(() => {
    if (!activeGroup) return null;
    const t = activeOutputTime * 1e6;
    const spoken = activeGroup.words.find(
      (w) => t >= w.output_start_us && t < w.output_end_us
    );
    if (spoken) return spoken.word_id;
    // Before the first word: the first word is the "upcoming" one.
    const first = activeGroup.words[0];
    if (first && t < first.output_start_us) return first.word_id;
    return null;
  }, [activeGroup, activeOutputTime]);

  const selectedWord = useMemo(
    () => words.find((w) => w.word_id === selectedWordId) || null,
    [words, selectedWordId]
  );

  const sourceUrl = currentProject?.video_filename ? `/api/projects/${currentProject.id}/video` : null;
  const activeClip = clips.find((clip) => clip.id === activeClipId);
  const analyzed = layoutAnalysis?.analysis;
  const previewLayout = selectedLayoutId === 'auto_hybrid'
    ? layoutAnalysis?.auto_hybrid_plan?.[0]?.layout_id ?? analyzed?.recommended_layout ?? 'center_crop'
    : selectedLayoutId;
  const exportUnits = Math.max(60, Math.floor(Math.min(currentProject?.width || 1920, currentProject?.height || 1080) / 18));
  const sourceAspect = (currentProject?.width || 1920) / (currentProject?.height || 1080);
  const detectedFace = analyzed?.webcam_region ?? analyzed?.primary_face ?? null;
  const camPosition = layoutParams.cam_pos ?? 'auto';
  const webcam = camPosition === 'auto' ? detectedFace : null;
  const gameCamHeight = layoutParams.split_ratio === '50_50' ? 50 : layoutParams.split_ratio === '40_60' ? 40 : 30;
  const screenHeight = Math.max(50, Math.min(75, layoutParams.screen_ratio ?? 65));
  const pipWidth = layoutParams.pip_size === 'small' ? 300 : layoutParams.pip_size === 'large' ? 420 : 360;
  const recommendation = candidates.find((candidate) => candidate.id === activeClip?.id);
  const recommendedLayout = layoutsCatalog.find((layout) => layout.implemented && layout.id === recommendation?.suggested_layout);
  const recommendedCaption = captionPresets.find((preset) => preset.id === recommendation?.suggested_subtitle_preset);
  const draftStart = trimDraft ? Number(trimDraft.start) : activeClip?.start ?? 0;
  const draftEnd = trimDraft ? Number(trimDraft.end) : activeClip?.end ?? currentProject?.duration ?? 0;
  const validTrim = (!trimDraft || Boolean(trimDraft.start.trim() && trimDraft.end.trim())) &&
    Number.isFinite(draftStart) && Number.isFinite(draftEnd) && draftStart >= 0 &&
    draftEnd <= (currentProject?.duration ?? 0) && draftEnd > draftStart;
  const previewRange = validTrim ? { start: draftStart, end: draftEnd } : activeClip;
  const timelineStart = previewRange?.start ?? 0;
  const timelineEnd = previewRange?.end ?? duration;
  const timelineTime = Math.min(timelineEnd, Math.max(timelineStart, currentTime));
  const openEditor = (clip?: Segment) => {
    if (trimDraft && clip?.id === activeClipId) return;
    for (const player of [videoRef.current, bgVideoRef.current, topVideoRef.current]) {
      if (!player) continue;
      player.pause();
      if (player.readyState > 0) player.currentTime = clip?.start || 0;
    }
    setActiveClipId(clip?.id || null);
    setCurrentTime(clip?.start || 0);
    if (clip?.layout_id) setSelectedLayoutId(clip.layout_id);
    if (clip?.layout_params) setLayoutParams((prev) => ({ ...prev, ...clip.layout_params }));
    if (clip?.caption_preset) setSelectedPresetId(clip.caption_preset);
    if (clip?.caption_style) {
      const nextStyle = { ...STYLE_DEFAULTS, ...currentProject?.caption_style, ...clip.caption_style };
      setCaptionStyle(nextStyle);
      if (nextStyle.caption_mode) {
        setCaptionGroups(groupWordsIntoCaptions(mappedWords, nextStyle.caption_mode));
      }
    }
    setIsPlaying(false);
    setTrimDraft(null);
    setTrimError('');
    setActiveTab('transcript');
    setWorkspace('editor');
    requestAnimationFrame(() => {
      editorRef.current?.focus({ preventScroll: true });
      if (matchMedia('(max-width: 1023px)').matches) editorRef.current?.scrollIntoView({ block: 'start' });
    });
  };
  const backToClips = () => {
    if (trimDraft && !window.confirm('Discard the unsaved trim?')) return;
    closeEditor();
    requestAnimationFrame(() => document.getElementById('clips-heading')?.focus());
  };
  const closeEditor = () => {
    videoRef.current?.pause();
    setIsPlaying(false);
    setTrimDraft(null);
    setWorkspace('review');
    setActiveTab('clips');
  };
  const removeClips = async (ids: string[]) => {
    if (!currentProject || !ids.length) return;
    const message = ids.length === 1 ? 'Remove this clip from the project?' : `Remove ${ids.length} clips from the project?`;
    if (!window.confirm(message)) return;
    setClipError('');
    setSavingEdit(true);
    try {
      await updateTimeline(clips.filter((clip) => !ids.includes(clip.id)));
      setSelectedClipIds((previous) => previous.filter((id) => !ids.includes(id)));
      if (activeClipId && ids.includes(activeClipId)) {
        closeEditor();
        setActiveClipId(null);
      }
    } catch (err) {
      setClipError(err instanceof Error ? err.message : 'Could not remove clips.');
    } finally {
      setSavingEdit(false);
    }
  };

  const saveEdit = async () => {
    if (!currentProject || savingEdit) return;
    setTrimError('');
    const start = draftStart;
    const end = draftEnd;
    if (!validTrim) {
      setTrimError('Enter a start and end within the source duration.');
      return;
    }
    const updated: Segment = activeClip
      ? {
          ...activeClip,
          start,
          end,
          layout_id: selectedLayoutId,
          layout_params: layoutParams,
          caption_preset: selectedPresetId,
          caption_style: captionStyle,
        }
      : {
          id: crypto.randomUUID(),
          start,
          end,
          title: currentProject.name,
          layout_id: selectedLayoutId,
          layout_params: layoutParams,
          caption_preset: selectedPresetId,
          caption_style: captionStyle,
        };
    const timeline = activeClip ? clips.map((clip) => clip.id === updated.id ? updated : clip) : [...clips, updated];
    setSavingEdit(true);
    try {
      const response = await fetch(`/api/projects/${currentProject.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          timeline, words, caption_style: captionStyle, caption_preset: selectedPresetId,
          caption_text: captionText, layout_id: selectedLayoutId, layout_params: layoutParams,
        }),
      });
      if (!response.ok) throw new Error('Could not save changes.');
      const saved = await response.json();
      setCurrentProject({ ...currentProject, timeline: saved.timeline || timeline });
      applyTimelineMapping(words, timeline);
      const next = { ...reviewed, [updated.id]: true };
      setReviewed(next);
      try { localStorage.setItem(`clipforge-review:${currentProject.id}`, JSON.stringify(next)); } catch {}
      setTrimDraft(null);
      setActiveClipId(updated.id);
    } catch (err) {
      setTrimError(err instanceof Error ? err.message : 'Could not save changes.');
    } finally {
      setSavingEdit(false);
    }
  };

  const clipActionsDisabled = generating || isImportingYt || isExporting || savingEdit || Boolean(trimDraft);
  const fieldClass = 'studio-field';
  const primaryClass = 'studio-button studio-primary';
  const secondaryClass = 'studio-button';

  return (
    <div className="clipforge">
      <Head><title>Clip Cutter · ClipForge</title></Head>
      <a href="#workspace" className="sr-only focus:not-sr-only">Skip to workspace</a>
      <header className="studio-header flex items-center justify-between gap-3 px-4 sm:px-5">
        <div className="flex items-center gap-3">
          <span className="studio-mark"><Scissors size={16} aria-hidden="true" /></span>
          <span className="text-sm font-semibold text-white">ClipForge</span>

        </div>
        <nav aria-label="Workspace" className="flex items-center gap-1.5">
          <button className={secondaryClass} aria-pressed={activeTab === 'settings'} onClick={() => { closeEditor(); setActiveTab(activeTab === 'settings' ? 'clips' : 'settings'); }} disabled={generating || savingEdit}><Settings size={14} aria-hidden="true" /> Settings</button>
        </nav>
      </header>
      {activeTab !== 'settings' && (
        <main id="workspace" className="studio-workspace" data-view={workspace}>
          <nav className="studio-mobile-nav" aria-label="Jump to panel">
            <a href="#source-heading">Source</a><a href="#clips-heading">Clips</a>
          </nav>
          <div className="studio-pane studio-sidebar">
          <section aria-labelledby="source-heading">
            <h1 id="source-heading" className="studio-title">Generate clips</h1>
            <form onSubmit={handleYouTubeImport} className="mt-4 grid grid-cols-[minmax(0,1fr)_64px] items-end gap-3">
              <label className="space-y-1.5 text-xs font-medium col-span-full">
                <span>YouTube video URL</span>
                <input type="url" name="youtube-url" disabled={generating || isImportingYt} autoComplete="off" spellCheck={false} required={!currentProject?.video_filename} value={ytUrl} onChange={(e) => setYtUrl(e.target.value)} placeholder="Paste YouTube link…" className={fieldClass} />
              </label>
              <label className="space-y-1.5 text-xs font-medium">
                <span>Subtitle language</span>
                <select className={fieldClass} disabled={generating || isImportingYt} value={subLanguage} onChange={(e) => setSubLanguage(e.target.value)}>
                  <option value="auto">Auto-detect</option><option value="en">English</option><option value="hi">Hindi</option><option value="hinglish">Hinglish</option>
                </select>
              </label>
              <label className="space-y-1.5 text-xs font-medium">
                <span>Clips</span>
                <select
                  className={fieldClass}
                  aria-label="Number of clips"
                  disabled={generating || isImportingYt || durationPreset === 'full'}
                  value={durationPreset === 'full' ? '1' : allClips ? 'all' : String(numClips)}
                  onChange={(e) => {
                    if (e.target.value === 'all') {
                      setAllClips(true);
                    } else {
                      setAllClips(false);
                      setNumClips(Number(e.target.value) || 5);
                    }
                  }}
                >
                  <option value="1">1 clip</option>
                  <option value="3">3 clips</option>
                  <option value="5">5 clips</option>
                  <option value="10">10 clips</option>
                  <option value="15">15 clips</option>
                  <option value="all">All potential clips</option>
                </select>
              </label>
              <label className="col-span-full space-y-1.5 text-xs font-medium">
                <span>Clip duration</span>
                <select className={fieldClass} disabled={generating || isImportingYt} value={durationPreset} onChange={(e) => setDurationPreset(e.target.value)}>
                  <option value="full">Full Video</option><option value="15-30">15–30 seconds</option><option value="30-60">30–60 seconds</option>
                </select>
              </label>
              <button type="submit" className={`${primaryClass} col-span-full`} disabled={clipActionsDisabled}>
                {generating || isImportingYt ? 'Working…' : durationPreset === 'full' ? 'Import full video' : 'Generate clips'}
              </button>
              {durationPreset !== 'full' && (!currentProject?.video_filename || ytUrl.trim()) && (
                <p className="col-span-full text-xs text-gray-400">Uses available YouTube signals and downloads the source for editing. Import videos you own or have permission to use.</p>
              )}
            </form>
            <div className="studio-section mt-4 flex flex-wrap items-center gap-3">
              <label className={`${secondaryClass} relative cursor-pointer focus-within:ring-2 focus-within:ring-[var(--cf-accent)]`}>
                <Upload size={14} aria-hidden="true" /> Import local video
                <input type="file" accept="video/*" className="absolute inset-0 cursor-pointer opacity-0" aria-label="Import local video" onChange={handleFileUpload} disabled={clipActionsDisabled} />
              </label>
              {sourceAction && <p role="status" className="text-sm text-gray-300">{sourceAction}</p>}
              {ytError && <p role="alert" className="text-sm text-red-300">{ytError}</p>}
            </div>
            {generation && (
              <div className="studio-notice mt-3 space-y-2">
                <p role={generation.status === 'failed' ? 'alert' : 'status'} className={`text-xs ${generation.status === 'failed' ? 'text-red-300' : 'text-gray-200'}`}>
                  {generation.detail}
                </p>
                {generating && generation.percent !== null && (
                  <div className="flex items-center gap-2 text-xs tabular-nums">
                    <progress className="h-2 min-w-0 flex-1 accent-[var(--cf-accent)]" max={100} value={generation.percent} aria-label="Source audio transcribed" />
                    <span>{generation.percent}%</span>
                  </div>
                )}
                {generationConnection && generating && <p role="status" className="text-xs text-amber-300">{generationConnection}</p>}
              </div>
            )}
          </section>

          </div>
          <section aria-labelledby="clips-heading" className="studio-pane studio-collection">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div className="min-w-0">
                <h2 id="clips-heading" tabIndex={-1} className="studio-title">Clips <span className="ml-1 text-sm font-normal text-gray-400">{clips.length}</span></h2>
                {currentProject && <p className="mt-1 truncate text-sm text-gray-400">{currentProject.name}</p>}
              </div>
              {sourceUrl && (
                <div className="flex flex-wrap gap-2">
                  {clips.length === 0 && <button className={secondaryClass} onClick={() => openEditor()} disabled={clipActionsDisabled}>Open editor</button>}
                  <button className={secondaryClass} onClick={() => handleExport()} disabled={clips.length === 0 || clipActionsDisabled}><Download size={15} aria-hidden="true" />{isExporting ? 'Rendering…' : 'Export project'}</button>
                </div>
              )}
            </div>
            {clips.length > 0 && (
              <div className="studio-selection">
                <span className="mr-auto text-xs text-gray-400" role="status">{selectedClipIds.length} selected</span>
                <button className={secondaryClass} disabled={clipActionsDisabled} onClick={() =>
                  setSelectedClipIds(selectedClipIds.length === clips.length ? [] : clips.map((clip) => clip.id))
                }>{selectedClipIds.length === clips.length ? 'Deselect all' : 'Select all'}</button>
                {selectedClipIds.length > 0 && (
                  <button className={secondaryClass} disabled={clipActionsDisabled}
                    onClick={() => removeClips(selectedClipIds)}>Remove selected</button>
                )}
              </div>
            )}
            {clipError && <p role="alert" className="text-sm text-red-300">{clipError}</p>}
            {(exportMessage || currentProject?.export_status === 'completed') && (
              <div className="studio-notice flex flex-wrap items-center justify-between gap-2">
                {exportMessage && <p role="status" className="text-sm">{exportMessage}</p>}
                {currentProject?.export_status === 'completed' && (
                  <a className={secondaryClass} href={`/api/projects/${currentProject.id}/exported-video`} download>
                    <Download size={14} aria-hidden="true" /> Download exported MP4
                  </a>
                )}
              </div>
            )}
            {generation?.mode === 'plan' && generation.status === 'ready' && candidates.length > 0 && (
              <ol className="divide-y divide-[var(--cf-line)]">
                {candidates.map((candidate) => (
                  <li key={candidate.id} className="flex flex-wrap items-baseline justify-between gap-2 py-3">
                    <span className="text-sm">
                      {candidate.headline}
                      {candidate.signal_source === 'youtube_replay_and_chat' && <span className="ml-2 text-xs text-gray-400">Most Replayed · Chat spike</span>}
                      {candidate.signal_source === 'youtube_replay' && <span className="ml-2 text-xs text-gray-400">Most Replayed</span>}
                      {candidate.signal_source === 'youtube_chat_replay' && <span className="ml-2 text-xs text-gray-400">Chat spike</span>}
                    </span>
                    <span className="text-xs tabular-nums text-gray-300">
                      {(candidate.start_ms / 1000).toFixed(1)}–{(candidate.end_ms / 1000).toFixed(1)} s
                    </span>
                  </li>
                ))}
              </ol>
            )}
            {clips.length === 0 && !(generation?.mode === 'plan' && generation.status === 'ready') && (
              <div className="studio-empty">
                <Film size={25} aria-hidden="true" className="mx-auto mb-3 text-gray-400" />
                <p className="text-sm font-medium text-gray-200">No clips yet</p>
                <p className="mt-1 text-sm text-gray-400">{generating ? 'New cuts appear here when generation finishes.' : sourceUrl ? 'Generate from the saved video or open the editor to trim it.' : 'Enter a YouTube URL or import a local video.'}</p>
              </div>
            )}
            <div className="studio-cards">
              {clips.map((clip, index) => {
                const transcript = clipTranscripts.get(clip.id) || '';
                const hasTitle = clip.title && !/^(?:Viral Candidate|Clip) #?\d+$/i.test(clip.title);
                const excerpt = transcript.trim().split(/\s+/).slice(0, 8).join(' ');
                const title = (hasTitle ? clip.title : excerpt) || `${currentProject?.name || 'Clip'} · ${clip.start.toFixed(0)}s`;
                return (
                  <article key={clip.id} className="studio-card" data-active={workspace === 'editor' && activeClipId === clip.id}>
                    {sourceUrl && (
                      <video aria-label={`9:16 preview for ${title}`} src={`${sourceUrl}#t=${clip.start},${clip.end}`} controls preload={index < 6 && !generating ? 'metadata' : 'none'} playsInline
                        onPlay={({ currentTarget: player }) => {
                          if (player.currentTime < clip.start || player.currentTime >= clip.end) player.currentTime = clip.start;
                        }}
                        onTimeUpdate={({ currentTarget: player }) => {
                          if (player.paused) return;
                          if (player.currentTime < clip.start) player.currentTime = clip.start;
                          if (player.currentTime >= clip.end) player.pause();
                        }}
                        className="studio-card-video" />
                    )}
                    <div className="studio-card-body">
                      <div className="flex flex-wrap justify-between gap-x-2 gap-y-1 text-xs text-gray-400">
                        <span className="font-mono tabular-nums">{(clip.end - clip.start).toFixed(1)}s</span>
                        {clip.origin === 'local' ? <span>Local highlight</span>
                          : clip.title?.startsWith('Viral Candidate #') ? <span>Starter cut</span>
                          : null}
                        {reviewed[clip.id] && <span title="Reviewed" className="text-emerald-300"><Check size={14} aria-hidden="true" /><span className="sr-only">Reviewed</span></span>}
                      </div>
                      <h3 className="break-words text-sm font-semibold text-gray-100"><button onClick={() => openEditor(clip)} disabled={generating || isImportingYt || isExporting || savingEdit || Boolean(trimDraft && activeClipId !== clip.id)} className="text-left hover:text-[var(--cf-accent)]">{title}</button></h3>
                      {transcript
                        ? <details className="text-sm text-gray-300"><summary className="cursor-pointer text-xs font-medium">Transcript</summary><p className="mt-2 max-h-40 overflow-auto break-words">{transcript}</p></details>
                        : null}
                      <div className="studio-card-actions">
                        <label className="studio-check" title="Select clip">
                          <input type="checkbox" aria-label={`Select ${title}`} disabled={clipActionsDisabled} checked={selectedClipIds.includes(clip.id)}
                            onChange={(event) => setSelectedClipIds((previous) => event.target.checked
                              ? [...previous, clip.id] : previous.filter((id) => id !== clip.id))} />
                        </label>
                        <button className={secondaryClass} onClick={() => openEditor(clip)} disabled={generating || isImportingYt || isExporting || savingEdit || Boolean(trimDraft && activeClipId !== clip.id)} aria-pressed={workspace === 'editor' && activeClipId === clip.id} aria-label={`Edit ${title}`}>Edit clip</button>
                        <button className="studio-button studio-icon studio-remove" disabled={clipActionsDisabled}
                          title="Remove clip" aria-label={`Remove ${title}`} onClick={() => removeClips([clip.id])}><Trash2 size={15} aria-hidden="true" /></button>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          </section>
          {workspace === 'editor' && (
            <>
              <section id="clip-editor" ref={editorRef} tabIndex={-1} aria-labelledby="editing-clip-title" className="studio-pane studio-preview">
              <div className="studio-editor-header">
                <button className={secondaryClass} onClick={backToClips} disabled={savingEdit || isTranscribing}><ArrowLeft size={16} aria-hidden="true" /> Back to clips</button>
                <div className="min-w-0 flex-1">
                  <h2 id="editing-clip-title" className="truncate text-sm font-semibold text-white">{activeClip?.title || currentProject?.name || 'Clip editor'}</h2>
                </div>
              </div>
        {/* CENTER 9:16 VIDEO PREVIEW WITH REALTIME GLOW CAPTIONS */}
        <div className="studio-monitor">
          {currentProject?.video_filename ? (
            <div
              ref={previewBoxRef}
              className="studio-frame group"
            >
              {/* VIDEO ELEMENTS BASED ON SHORT LAYOUT */}
              {previewLayout === 'blur_fill' || previewLayout === 'blur_pad' ? (
                <>
                  <video
                    ref={bgVideoRef}
                    src={`/api/projects/${currentProject.id}/video`}
                    className="absolute inset-0 w-full h-full object-cover filter blur-xl scale-125 opacity-70 pointer-events-none"
                    muted
                    playsInline
                  />
                  <video
                    ref={videoRef}
                    src={`/api/projects/${currentProject.id}/video`}
                    onTimeUpdate={handleTimeUpdate}
                    onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; e.currentTarget.muted = isMuted; }}
                    onEnded={() => setIsPlaying(false)}
                    className="w-full h-full object-contain relative z-10 transition-transform duration-150"
                    style={{ transform: `scale(${layoutParams.fg_scale ?? 1.0})` }}
                  />
                </>
              ) : previewLayout === 'split_gaming' ? (
                <div className="w-full h-full flex flex-col relative z-10">
                  <div className="w-full overflow-hidden relative" style={{ height: `${gameCamHeight}%` }}>
                    <video
                      ref={topVideoRef}
                      src={`/api/projects/${currentProject.id}/video`}
                      className="absolute max-w-none"
                      style={facecamStyle(webcam, camPosition, sourceAspect, (9 / 16) / (gameCamHeight / 100))}
                      onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; }}
                      muted
                      playsInline
                    />
                  </div>
                  <div className="flex-1 min-h-0 w-full overflow-hidden relative">
                    <video
                      ref={videoRef}
                      src={`/api/projects/${currentProject.id}/video`}
                      onTimeUpdate={handleTimeUpdate}
                    onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; e.currentTarget.muted = isMuted; }}
                      onEnded={() => setIsPlaying(false)}
                      className="w-full h-full object-cover object-center"
                    />
                  </div>
                </div>
              ) : previewLayout === 'split_2_faces' || previewLayout === 'split_faces' ? (
                <div className="w-full h-full flex flex-col relative z-10">
                  <div className="h-1/2 w-full overflow-hidden border-b border-black relative">
                    <video
                      ref={topVideoRef}
                      src={`/api/projects/${currentProject.id}/video`}
                      className="w-full h-full object-cover"
                      style={{ objectPosition: cropPosition(analyzed?.left_face_cx ?? 0.25, sourceAspect, 9 / 8) }}
                      onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; }}
                      muted
                      playsInline
                    />
                  </div>
                  <div className="h-1/2 w-full overflow-hidden relative">
                    <video
                      ref={videoRef}
                      src={`/api/projects/${currentProject.id}/video`}
                      onTimeUpdate={handleTimeUpdate}
                    onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; e.currentTarget.muted = isMuted; }}
                      onEnded={() => setIsPlaying(false)}
                      className="w-full h-full object-cover"
                      style={{ objectPosition: cropPosition(analyzed?.right_face_cx ?? 0.75, sourceAspect, 9 / 8) }}
                    />
                  </div>
                </div>
              ) : previewLayout === 'floating_facecam' ? (
                <div className="relative w-full h-full">
                  <video
                    ref={videoRef}
                    src={`/api/projects/${currentProject.id}/video`}
                    onTimeUpdate={handleTimeUpdate}
                    onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; e.currentTarget.muted = isMuted; }}
                    onEnded={() => setIsPlaying(false)}
                    className="w-full h-full object-cover"
                  />
                  <div className={`absolute ${
                    layoutParams.pip_pos === 'top_right' ? 'top-[4.17%] right-[3.7%]' :
                    layoutParams.pip_pos === 'top_left' ? 'top-[4.17%] left-[3.7%]' :
                    layoutParams.pip_pos === 'bottom_left' ? 'bottom-[10.42%] left-[3.7%]' : 'bottom-[10.42%] right-[3.7%]'
                  } overflow-hidden border border-white/50 z-20`} style={{ width: `${pipWidth / 1080 * 100}%`, aspectRatio: '3 / 4' }}>
                    <video
                      ref={topVideoRef}
                      src={`/api/projects/${currentProject.id}/video`}
                      className="absolute max-w-none"
                      style={facecamStyle(detectedFace, 'auto', sourceAspect, 3 / 4)}
                      onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; }}
                      muted
                      playsInline
                    />
                  </div>
                </div>
              ) : previewLayout === 'screen_facecam' ? (
                <div className="w-full h-full flex flex-col relative z-10 bg-[var(--cf-base)]">
                  <div className="w-full overflow-hidden relative flex items-center justify-center bg-black" style={{ height: `${screenHeight}%` }}>
                    <video
                      ref={videoRef}
                      src={`/api/projects/${currentProject.id}/video`}
                      onTimeUpdate={handleTimeUpdate}
                    onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; e.currentTarget.muted = isMuted; }}
                      onEnded={() => setIsPlaying(false)}
                      className="w-full h-full object-contain"
                    />
                  </div>
                  <div className="flex-1 min-h-0 w-full overflow-hidden relative">
                    <video
                      ref={topVideoRef}
                      src={`/api/projects/${currentProject.id}/video`}
                      className="absolute max-w-none"
                      style={facecamStyle(detectedFace, detectedFace ? 'auto' : 'left_half', sourceAspect, (9 / 16) / (1 - screenHeight / 100))}
                      onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; }}
                      muted
                      playsInline
                    />
                  </div>
                </div>
              ) : previewLayout === 'framed_headline' ? (
                <div className="w-full h-full flex flex-col justify-between p-4 relative z-10" style={{ backgroundColor: layoutParams.bg_color || '#0f1117' }}>
                  <div className="w-full pt-4 pb-2 text-center">
                    <div className="inline-block px-3 py-1.5 rounded-lg bg-black/70 border border-white/20 text-white font-extrabold text-sm uppercase tracking-wide shadow-lg">
                      {layoutParams.headline || currentProject.name || "WATCH THIS"}
                    </div>
                  </div>
                  <div className="w-full aspect-video rounded-lg overflow-hidden shadow-2xl border border-white/10 relative">
                    <video
                      ref={videoRef}
                      src={`/api/projects/${currentProject.id}/video`}
                      onTimeUpdate={handleTimeUpdate}
                    onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; e.currentTarget.muted = isMuted; }}
                      onEnded={() => setIsPlaying(false)}
                      className="w-full h-full object-cover"
                    />
                  </div>
                  <div className="w-full pb-8 text-center text-[10px] text-gray-500 uppercase tracking-widest font-semibold">
                    ClipForge Portrait Studio
                  </div>
                </div>
              ) : previewLayout === 'dynamic_pan_zoom' ? (
                <div className="w-full h-full overflow-hidden relative">
                  <video
                    ref={videoRef}
                    src={`/api/projects/${currentProject.id}/video`}
                    onTimeUpdate={handleTimeUpdate}
                    onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; e.currentTarget.muted = isMuted; }}
                    onEnded={() => setIsPlaying(false)}
                    className="w-full h-full object-cover scale-110"
                    style={{ animation: 'pulse 8s ease-in-out infinite alternate' }}
                  />
                  <span className="absolute top-3 left-3 text-[10px] font-semibold bg-black/80 px-2 py-0.5 rounded text-teal-300 border border-teal-500/40">
                    🎬 Dynamic Pan & Zoom
                  </span>
                </div>
              ) : previewLayout === 'smart_face_crop' ? (
                <div className="relative w-full h-full">
                  <video
                    ref={videoRef}
                    src={`/api/projects/${currentProject.id}/video`}
                    onTimeUpdate={handleTimeUpdate}
                    onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; e.currentTarget.muted = isMuted; }}
                    onEnded={() => setIsPlaying(false)}
                    className="w-full h-full object-cover transition-[object-position] duration-300"
                    style={{ objectPosition: cropPosition(Math.max(0.1, Math.min(0.9, (analyzed?.primary_face_cx ?? 0.5) + (layoutParams.manual_offset_x ?? 0))), sourceAspect, 9 / 16) }}
                  />
                </div>
              ) : previewLayout === 'active_speaker_crop' ? (
                <div className="relative w-full h-full">
                  <video
                    ref={videoRef}
                    src={`/api/projects/${currentProject.id}/video`}
                    onTimeUpdate={handleTimeUpdate}
                    onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; e.currentTarget.muted = isMuted; }}
                    onEnded={() => setIsPlaying(false)}
                    className="w-full h-full object-cover transition-[object-position] duration-300"
                    style={{ objectPosition: cropPosition(layoutParams.speaker_mode === 'speaker_1' ? analyzed?.left_face_cx ?? 0.3 : layoutParams.speaker_mode === 'speaker_2' ? analyzed?.right_face_cx ?? 0.7 : analyzed?.primary_face_cx ?? 0.5, sourceAspect, 9 / 16) }}
                  />
                </div>
              ) : previewLayout === 'fit_pad' || previewLayout === 'fit' ? (
                <div className="w-full h-full flex items-center justify-center relative z-10" style={{ backgroundColor: layoutParams.pad_color || '#000000' }}>
                  <video
                    ref={videoRef}
                    src={`/api/projects/${currentProject.id}/video`}
                    onTimeUpdate={handleTimeUpdate}
                    onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; e.currentTarget.muted = isMuted; }}
                    onEnded={() => setIsPlaying(false)}
                    className="w-full h-full object-contain"
                  />
                </div>
              ) : (
                <video
                  ref={videoRef}
                  src={`/api/projects/${currentProject.id}/video`}
                  onTimeUpdate={handleTimeUpdate}
                    onLoadedMetadata={(e) => { e.currentTarget.currentTime = currentTime; e.currentTarget.muted = isMuted; }}
                  onEnded={() => setIsPlaying(false)}
                  className="w-full h-full object-cover transition-[object-position] duration-150"
                  style={{
                    objectPosition: `${(layoutParams.crop_x ?? 0.5) * 100}% center`,
                    transform: `scale(${layoutParams.zoom ?? 1.0})`
                  }}
                />
              )}

              {/* OVERLAID DYNAMIC GLOW CAPTION PREVIEW - uses the editable style */}
              <div
                className="absolute left-4 right-4 pointer-events-none text-center z-20"
                style={{
                  bottom: `${Math.max(5, Math.min(88, (captionStyle.margin_v / 1920) * 100))}%`,
                }}
              >
                <span
                  style={{
                    fontFamily: cssFontFamily(captionStyle.font_family),
                    color: captionStyle.text_color,
                    textShadow: glowShadow,
                    WebkitTextStroke: captionStyle.stroke_width > 0
                      ? `${captionPx.strokeWidth}px ${captionStyle.stroke_color}`
                      : '0px transparent',
                    fontWeight: cssFontWeight(captionStyle.font_weight),
                    letterSpacing: `${captionPx.letterSpacing}px`,
                    fontSize: `${captionPx.fontSize}px`,
                    textTransform: captionStyle.uppercase ? 'uppercase' : 'none',
                    // Match the export: a right-to-left caption is laid out
                    // right-to-left even when it starts with a Latin word.
                    direction: isRtlText(activeGroup?.text || captionText) ? 'rtl' : 'ltr',
                    unicodeBidi: 'isolate',
                  }}
                  className="inline-block transition-all drop-shadow-xl"
                >
                  {activeGroup && karaokeEnabled ? (
                    <KaraokeCaption group={activeGroup} activeWordId={activeWordId} style={captionStyle} scale={captionScale} />
                  ) : (
                    activeGroup?.text || (words.length === 0 ? captionText : '')
                  )}
                </span>
              </div>

            </div>
          ) : (
            <div className="flex flex-col items-center justify-center text-gray-500 border border-dashed border-[var(--cf-line)] rounded-2xl p-12 max-w-sm text-center">
              <Film size={44} className="mb-3 text-gray-600" />
              <div className="text-sm font-semibold text-gray-300 mb-1">No Video Loaded</div>
              <div className="text-xs text-gray-500 mb-4">Paste a YouTube URL in the left bar or import a video file.</div>
            </div>
          )}
        </div>

        {/* 3. BOTTOM TIMELINE CONTAINER */}
        <div className="studio-timeline">
          <div className="studio-transport">
            <div className="flex items-center gap-3">
              <button
                onClick={togglePlay}
                disabled={!currentProject?.video_filename}
                className="studio-button studio-quiet"
              >
                {isPlaying ? <Pause size={14} /> : <Play size={14} />} {isPlaying ? 'Pause' : 'Preview'}
              </button>

              <button
                onClick={handleSplit}
                disabled={!activeClip || currentTime <= activeClip.start + 0.1 || currentTime >= activeClip.end - 0.1 || Boolean(trimDraft) || isExporting || savingEdit}
                className="studio-button studio-quiet"
                title="Split segment at playhead (Scissors)"
              >
                <Scissors size={14} /> Split Clip
              </button>
            </div>

            <button className={secondaryClass} aria-label={isMuted ? 'Unmute preview' : 'Mute preview'} onClick={() => {
              if (videoRef.current) videoRef.current.muted = !isMuted;
              setIsMuted(!isMuted);
            }}>{isMuted ? <VolumeX size={16} /> : <Volume2 size={16} />}</button>

            <div className="tabular-nums text-gray-300">
              {(timelineTime - timelineStart).toFixed(2)}s / {(timelineEnd - timelineStart).toFixed(2)}s
            </div>
          </div>

          <label className="studio-timeline-slider">
            <span className="sr-only">Timeline playhead</span>
            <input className="w-full accent-[var(--cf-accent)]" type="range" min={timelineStart} max={timelineEnd} step="any" value={timelineTime} onChange={(e) => {
              const time = Number(e.target.value);
              if (videoRef.current) videoRef.current.currentTime = time;
              setCurrentTime(time);
            }} />
          </label>
        </div>
              </section>
              <aside aria-label="Clip editing panel" className="studio-pane studio-editor">
          <div className="flex flex-wrap items-end gap-2 px-4 py-2">
            {(['start', 'end'] as const).map((edge) => {
              const value = trimDraft?.[edge] ?? String(activeClip?.[edge] ?? (edge === 'start' ? 0 : currentProject?.duration ?? 0));
              return <label key={edge} className="w-20 space-y-1 text-xs capitalize"><span>{edge === 'start' ? 'Start' : 'End'} (s)</span>
                <input type="number" step="any" min={0} max={currentProject?.duration} value={value} className={fieldClass}
                  onChange={(e) => {
                    const nextValue = e.target.value;
                    setTrimDraft((draft) => ({
                      start: draft?.start ?? String(activeClip?.start ?? 0),
                      end: draft?.end ?? String(activeClip?.end ?? currentProject?.duration ?? 0),
                      [edge]: nextValue,
                    }));
                  }} />
              </label>;
            })}
            <button className={secondaryClass} onClick={() => { setTrimDraft(null); setTrimError(''); }}>Reset trim</button>
            {trimError && <p role="alert" className="w-full text-xs text-red-300">{trimError}</p>}
          </div>
          <div className="studio-editor-actions">
            <p role="status" className="text-xs text-gray-400">{trimDraft ? 'Unsaved trim' : activeClip && reviewed[activeClip.id] ? 'Reviewed' : '9:16 portrait'}</p>
            <div className="flex flex-wrap justify-end gap-2">
              <button onClick={saveEdit} disabled={savingEdit || isTranscribing || isExporting || !sourceUrl} className={secondaryClass}>{savingEdit ? 'Saving…' : 'Save clip'}</button>
              {activeClip && <button onClick={() => handleExport(activeClip)} disabled={savingEdit || isTranscribing || isExporting || Boolean(trimDraft) || !transcriptSaved}
                className={primaryClass}><Download size={14} aria-hidden="true" />{isExporting ? 'Rendering…' : 'Export Short'}</button>}
            </div>
          </div>
          {(recommendedLayout || recommendedCaption) && (
            <div className="studio-section px-4 pb-3">
              <p className="mb-2 text-xs text-gray-300">Saved suggestions · Apply to project</p>
              <div className="flex flex-wrap gap-2">
                {recommendedLayout && (
                  <button type="button" className={secondaryClass} disabled={generating || savingEdit || isExporting}
                    onClick={() => handleSelectLayout(recommendedLayout.id)} aria-pressed={selectedLayoutId === recommendedLayout.id}>
                    {recommendedLayout.name}
                  </button>
                )}
                {recommendedCaption && (
                  <button type="button" className={secondaryClass} disabled={generating || savingEdit || isExporting}
                    onClick={() => selectCaptionPreset(recommendedCaption.id)} aria-pressed={selectedPresetId === recommendedCaption.id}>
                    {recommendedCaption.name}
                  </button>
                )}
              </div>
            </div>
          )}
          <nav aria-label="Editor tools" className="studio-editor-tabs">
            {(['transcript', 'captions', 'layout', 'fonts'] as const).map((tab) => (
              <button key={tab} aria-pressed={activeTab === tab} className="studio-tab" onClick={() => setActiveTab(tab)}>{tab[0].toUpperCase() + tab.slice(1)}</button>
            ))}
          </nav>
          <div className="p-3">
            {/* 2. CAPTION STYLE EDITOR (COLOURS, GLOW, FONT) */}
            {activeTab === 'captions' && (
              <div className="space-y-4">
                <CaptionStyleEditor
                  style={captionStyle}
                  text={captionText}
                  fontOptions={captionFontOptions}
                  saved={styleSaved}
                  error={styleError}
                  onChange={updateCaptionStyle}
                  onTextChange={updateCaptionText}
                />
                <div className="text-sm font-semibold text-gray-200">
                  Caption presets
                </div>
                {captionPresets.map((preset) => {
                  const isSelected = selectedPresetId === preset.id;
                  return (
                    <button
                      key={preset.id}
                      aria-pressed={isSelected}
                      onClick={() => selectCaptionPreset(preset.id)}
                      className={`block w-full text-left p-3 rounded-xl border cursor-pointer transition ${isSelected ? 'bg-[var(--cf-accent-soft)] border-[var(--cf-accent)] ' : 'bg-[var(--cf-panel)] border-[var(--cf-line)] hover:border-gray-600'}`}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <div className="text-xs font-bold text-white flex items-center gap-1.5">
                          <Type size={13} className="text-[var(--cf-accent)]" />
                          {preset.name}
                        </div>
                        {isSelected && <span className="text-[10px] text-emerald-400 font-semibold">Active</span>}
                      </div>
                      <div className="text-[11px] text-gray-400 mb-2">{preset.desc}</div>

                      {/* Live visual preview card of glow text */}
                      <div className="h-10 bg-black/60 rounded-lg flex items-center justify-center p-2 border border-white/5">
                        <span
                          style={{
                            fontFamily: preset.preview.css_font_family,
                            color: preset.preview.text_color,
                            textShadow: preset.preview.text_shadow_scaled,
                            WebkitTextStroke: preset.preview.stroke_scaled,
                            fontWeight: preset.preview.font_weight,
                            letterSpacing: '0.5px'
                          }}
                          className="text-xs uppercase"
                        >
                          {captionText.slice(0, 28) || 'PREVIEW'}
                        </span>
                      </div>
                    </button>
                  );
                })}

              </div>
            )}

            {/* 2b. FONT LIBRARY */}
            {activeTab === 'fonts' && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-semibold text-gray-200">
                    Font Library ({installedFonts.length}/{fontFamilies.length})
                  </div>
                  <button
                    onClick={installAllFonts}
                    disabled={installAllBusy}
                    className="text-[10px] px-2 py-1 rounded bg-[var(--cf-accent-soft)] border border-[var(--cf-line)] text-[var(--cf-accent)] hover:bg-[var(--cf-accent-soft)]/30 transition disabled:opacity-50 flex items-center gap-1"
                  >
                    {installAllBusy ? <Loader2 size={10} className="animate-spin" /> : <Download size={10} />}
                    Install all
                  </button>
                </div>

                <div className="relative">
                  <Search size={13} className="absolute left-2.5 top-2.5 text-gray-500" />
                  <input
                    type="text"
                    value={fontSearch}
                    aria-label="Search fonts"
                    onChange={(e) => setFontSearch(e.target.value)}
                    placeholder="Search fonts..."
                    className="w-full bg-[var(--cf-base)] border border-[var(--cf-line)] focus:border-[var(--cf-accent)] rounded pl-8 pr-2.5 py-1.5 text-xs text-white placeholder-gray-500 focus:outline-none"
                  />
                </div>

                <div className="flex flex-wrap gap-1">
                  {['All', ...fontCategories].map((cat) => (
                    <button
                      key={cat}
                      onClick={() => setFontCategory(cat)}
                      className={`px-2 py-0.5 rounded text-[10px] font-medium transition ${
                        fontCategory === cat
                          ? 'bg-[var(--cf-accent-soft)] text-[var(--cf-accent)] border border-[var(--cf-line)]'
                          : 'bg-[var(--cf-raised)] text-gray-400 border border-[var(--cf-line)] hover:text-gray-200'
                      }`}
                    >
                      {cat}
                    </button>
                  ))}
                </div>

                {fontError && (
                  <div className="text-[10px] text-red-400 bg-red-500/10 border border-red-500/30 rounded px-2 py-1">
                    {fontError}
                  </div>
                )}

                <div className="space-y-1.5">
                  {filteredFonts.map((font) => (
                    <div
                      key={font.id}
                      className="p-2.5 rounded-lg border bg-[var(--cf-panel)] border-[var(--cf-line)] hover:border-gray-600 transition"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <div
                            className="text-sm font-semibold text-white truncate"
                            style={{
                              fontFamily: font.installed
                                ? cssFontFamily(`CF ${font.family} ${font.installed_weights[0] || 400}`)
                                : 'inherit',
                              fontWeight: font.installed ? 400 : undefined,
                            }}
                          >
                            {font.family}
                          </div>
                          <div className="text-[9px] text-gray-500 mt-0.5 flex items-center gap-1.5">
                            <span>{font.category}</span>
                            <span>•</span>
                            <span>{font.license}</span>
                            <span>•</span>
                            <span>{font.weights.join(', ')}</span>
                          </div>
                        </div>
                        {font.installed ? (
                          <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 shrink-0">
                            Installed
                          </span>
                        ) : (
                          <button
                            onClick={() => installFont(font.id)}
                            disabled={installingFont === font.id}
                            className="text-[10px] px-2 py-1 rounded bg-[var(--cf-accent-soft)] border border-[var(--cf-line)] text-[var(--cf-accent)] hover:bg-[var(--cf-accent-soft)]/30 transition disabled:opacity-50 shrink-0 flex items-center gap-1"
                          >
                            {installingFont === font.id
                              ? <Loader2 size={10} className="animate-spin" />
                              : <Download size={10} />}
                            Install
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                  {filteredFonts.length === 0 && (
                    <div className="text-center py-6 text-gray-500 text-xs">
                      No fonts match your search.
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* 2c. TRANSCRIPT & KARAOKE EDITOR */}
            {activeTab === 'transcript' && (
              <TranscriptPanel
                words={previewRange ? words.filter((word) =>
                  word.source_start_us < previewRange.end * 1e6 && word.source_end_us > previewRange.start * 1e6
                ) : words}
                segments={transcriptSegments}
                activeWordId={activeWordId}
                selectedWordId={selectedWordId}
                onSelectWord={(w) => {
                  setSelectedWordId(w.word_id);
                  if (videoRef.current) {
                    videoRef.current.currentTime = w.source_start_us / 1e6;
                    setCurrentTime(w.source_start_us / 1e6);
                  }
                }}
                onUpdateWord={updateWord}
                onNudgeWord={nudgeWord}
                isTranscribing={isTranscribing}
                transcribeMessage={transcriptJob?.status !== 'failed' ? transcriptJob?.detail ?? null : null}
                transcribeError={transcriptJob?.status === 'failed' ? transcriptJob.detail : transcribeError}
                transcribeModel={transcribeModel}
                transcribeModels={transcribeModels}
                onModelChange={setTranscribeModel}
                transcribeLanguage={subLanguage}
                onLanguageChange={setSubLanguage}
                onTranscribe={runTranscription}
                karaokeEnabled={karaokeEnabled}
                onKaraokeToggle={setKaraokeEnabled}
                saved={transcriptSaved}
                canTranscribe={!!currentProject?.video_filename && !clipActionsDisabled && transcriptSaved}
              />
            )}

          </div>
          {activeTab === 'layout' && (
      <div className="min-w-0 bg-[#17181e] lg:min-h-0 flex flex-col border-t border-[var(--cf-line)] p-4 space-y-5">
        <LayoutControls
          selectedLayoutId={selectedLayoutId}
          onSelectLayout={handleSelectLayout}
          layoutParams={layoutParams}
          onUpdateParams={updateLayoutParams}
          onOpenCatalog={() => setIsLayoutModalOpen(true)}
          analysis={layoutAnalysis}
          implementedLayouts={layoutsCatalog.filter((l) => l.implemented)}
        />

        <div>
          <h2 className="text-sm font-bold text-white mb-1">Caption Position</h2>
          <div className="grid grid-cols-3 gap-1.5 mt-2">
            <button
              onClick={() => updateCaptionStyle({ margin_v: 1620 })}
              className={`py-1.5 px-2 rounded-lg border text-center transition text-xs font-semibold ${
                captionStyle.margin_v >= 1350
                  ? 'bg-[var(--cf-accent-soft)] border-[var(--cf-accent)] text-white'
                  : 'bg-[var(--cf-raised)] border-[var(--cf-line)] text-gray-400 hover:text-gray-200'
              }`}
            >
              Top
            </button>
            <button
              onClick={() => updateCaptionStyle({ margin_v: 960 })}
              className={`py-1.5 px-2 rounded-lg border text-center transition text-xs font-semibold ${
                captionStyle.margin_v >= 600 && captionStyle.margin_v < 1350
                  ? 'bg-[var(--cf-accent-soft)] border-[var(--cf-accent)] text-white'
                  : 'bg-[var(--cf-raised)] border-[var(--cf-line)] text-gray-400 hover:text-gray-200'
              }`}
            >
              Center
            </button>
            <button
              onClick={() => updateCaptionStyle({ margin_v: 220 })}
              className={`py-1.5 px-2 rounded-lg border text-center transition text-xs font-semibold ${
                captionStyle.margin_v < 600
                  ? 'bg-[var(--cf-accent-soft)] border-[var(--cf-accent)] text-white'
                  : 'bg-[var(--cf-raised)] border-[var(--cf-line)] text-gray-400 hover:text-gray-200'
              }`}
            >
              Bottom
            </button>
          </div>
          <div className="mt-2.5">
            <div className="flex justify-between text-[10px] text-gray-400 mb-1">
              <span>Vertical offset</span>
              <span>{Math.round((captionStyle.margin_v / 1920) * 100)}% ({captionStyle.margin_v}px)</span>
            </div>
            <input
              type="range"
              aria-label="Caption vertical offset"
              min={100}
              max={1750}
              step={10}
              value={captionStyle.margin_v}
              onChange={(e) => updateCaptionStyle({ margin_v: Number(e.target.value) })}
              className="w-full accent-[var(--cf-accent)] h-1.5 bg-[var(--cf-raised)] rounded-lg cursor-pointer"
            />
          </div>
        </div>

        <div>
          <h2 className="text-sm font-bold text-white mb-2">Export settings</h2>
          <div className="space-y-2">
            <div>
              <label htmlFor="export-frame-rate" className="text-xs text-gray-400 block mb-1">Frame rate</label>
              <select id="export-frame-rate"
                value={fps}
                onChange={(e) => setFps(Number(e.target.value))}
                className="w-full bg-[var(--cf-raised)] border border-[var(--cf-line)] rounded-lg px-2.5 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-[var(--cf-accent)]"
              >
                <option value={30}>30 FPS</option>
                <option value={60}>60 FPS</option>
              </select>
            </div>
            <div>
              <p className="text-xs text-gray-400">{18 * exportUnits} × {32 * exportUnits} · 9:16 portrait</p>
            </div>
          </div>
        </div>

      </div>
          )}
              </aside>
            </>
          )}
        </main>
      )}
      {activeTab === 'settings' && (
        <main id="workspace" className="studio-page mx-auto w-full max-w-2xl p-4 sm:p-8">
          <SettingsPanel
            ytBrowser={ytBrowser}
            availableBrowsers={availableBrowsers}
            onYtBrowserChange={handleYtBrowserChange}
          />
        </main>
      )}
      <LayoutModal
        isOpen={isLayoutModalOpen}
        onClose={() => setIsLayoutModalOpen(false)}
        layouts={layoutsCatalog}
        categories={layoutCategories}
        selectedLayoutId={selectedLayoutId}
        onSelectLayout={handleSelectLayout}
      />
    </div>
  );
}
