import React from 'react';
import {
  Layers,
  Sparkles,
  Sliders,
  Crop,
  Maximize2,
  Tv,
  Users,
  Gamepad2,
  UserCheck,
  Video,
  MonitorPlay,
  Film,
  Type,
  Eye,
  CheckCircle2,
} from 'lucide-react';
import { LayoutDef } from './LayoutModal';

export interface FaceRegion {
  cx: number;
  cy: number;
  w: number;
  h: number;
}

export interface LayoutAnalysis {
  analysis?: {
    primary_face?: FaceRegion | null;
    webcam_region?: FaceRegion | null;
    primary_face_cx?: number;
    left_face_cx?: number;
    right_face_cx?: number;
    face_detected_ratio?: number;
    multi_face_ratio?: number;
    recommended_layout?: string;
  };
  auto_hybrid_plan?: { layout_id: string }[];
  unavailable?: boolean;
}

interface LayoutControlsProps {
  selectedLayoutId: string;
  onSelectLayout: (id: string) => void;
  layoutParams: Record<string, any>;
  onUpdateParams: (newParams: Partial<Record<string, any>>) => void;
  onOpenCatalog: () => void;
  analysis: LayoutAnalysis | null;
  implementedLayouts: LayoutDef[];
}

const LAYOUT_ICONS: Record<string, React.ReactNode> = {
  center_crop: <Crop size={14} />,
  blur_fill: <Maximize2 size={14} />,
  fit_pad: <Tv size={14} />,
  smart_face_crop: <UserCheck size={14} className="text-emerald-400" />,
  active_speaker_crop: <Video size={14} className="text-cyan-400" />,
  split_gaming: <Gamepad2 size={14} className="text-[var(--cf-accent)]" />,
  split_2_faces: <Users size={14} className="text-purple-400" />,
  floating_facecam: <MonitorPlay size={14} className="text-pink-400" />,
  screen_facecam: <MonitorPlay size={14} className="text-blue-400" />,
  framed_headline: <Type size={14} className="text-amber-400" />,
  dynamic_pan_zoom: <Film size={14} className="text-teal-400" />,
  auto_hybrid: <Sparkles size={14} className="text-amber-300" />,
};

export const DEFAULT_CORE_LAYOUTS: LayoutDef[] = [
  { id: 'center_crop', name: 'Center Crop', category: 'focus', description: 'Standard 9:16 portrait crop with horizontal pan adjustment.', implemented: true, aspect_ratio: '9:16' },
  { id: 'blur_fill', name: 'Blur Fill', category: 'focus', description: 'Original video centered over dynamic moving blurred background.', implemented: true, aspect_ratio: '9:16' },
  { id: 'fit_pad', name: 'Fit & Pad', category: 'focus', description: 'Full original video visible with clean dark matte padding.', implemented: true, aspect_ratio: '9:16' },
  { id: 'smart_face_crop', name: 'Smart Face Crop', category: 'focus', description: 'AI face tracking automatically centers on the speaker.', implemented: true, aspect_ratio: '9:16' },
  { id: 'active_speaker_crop', name: 'Active Speaker Crop', category: 'focus', description: 'Switches camera focus to whichever speaker is actively talking.', implemented: true, aspect_ratio: '9:16' },
  { id: 'split_gaming', name: 'Split Gaming (70/30)', category: 'split', description: 'Top 30% webcam reaction, Bottom 70% gameplay focus.', implemented: true, aspect_ratio: '9:16' },
  { id: 'split_2_faces', name: 'Split 2-Faces (50/50)', category: 'split', description: 'Top 50% Speaker 1, Bottom 50% Speaker 2 (Podcasts/Interviews).', implemented: true, aspect_ratio: '9:16' },
  { id: 'floating_facecam', name: 'Floating Facecam (PiP)', category: 'pip', description: 'Full 9:16 gameplay with floating bordered facecam in corner.', implemented: true, aspect_ratio: '9:16' },
  { id: 'screen_facecam', name: 'Screen Share + Facecam', category: 'pip', description: 'Top 65% screen share, Bottom 35% presenter webcam.', implemented: true, aspect_ratio: '9:16' },
  { id: 'framed_headline', name: 'Framed Landscape + Headline', category: 'framed', description: 'Centered video with prominent top headline banner.', implemented: true, aspect_ratio: '9:16' },
  { id: 'dynamic_pan_zoom', name: 'Dynamic Pan & Zoom', category: 'focus', description: 'Cinematic Ken Burns camera motion across widescreen footage.', implemented: true, aspect_ratio: '9:16' },
  { id: 'auto_hybrid', name: 'Auto Hybrid', category: 'dynamic', description: 'Intelligent AI layout switching based on faces, speakers & scenes.', implemented: true, aspect_ratio: '9:16' },
];

export const LayoutControls: React.FC<LayoutControlsProps> = ({
  selectedLayoutId,
  onSelectLayout,
  layoutParams,
  onUpdateParams,
  onOpenCatalog,
  analysis,
  implementedLayouts,
}) => {
  const list = implementedLayouts && implementedLayouts.length > 0 ? implementedLayouts : DEFAULT_CORE_LAYOUTS;
  const activeLayout = list.find((l) => l.id === selectedLayoutId) || list[0];

  return (
    <div className="space-y-4">
      {/* Header with Browse Catalog button */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold text-white flex items-center gap-1.5">
          <Layers size={15} className="text-[var(--cf-accent)]" />
          Shorts Layout
        </h2>
        <button
          onClick={onOpenCatalog}
          className="text-[11px] text-[var(--cf-accent)] hover:text-[var(--cf-accent)] bg-[var(--cf-accent-soft)] hover:bg-[var(--cf-accent-soft)] border border-[var(--cf-accent)]/30 px-2 py-0.5 rounded-md font-medium transition"
        >
          Browse layouts
        </button>
      </div>

      {/* Quick Select Grid for Core 12 Layouts */}
      <div className="grid grid-cols-2 gap-1.5">
        {list.map((l) => {
          const isSelected = selectedLayoutId === l.id;
          return (
            <button
              key={l.id}
              aria-pressed={isSelected}
              onClick={() => onSelectLayout(l.id)}
              className={`p-2 rounded-lg border text-left transition relative flex flex-col justify-between ${
                isSelected
                  ? 'bg-[var(--cf-accent-soft)] border-[var(--cf-accent)] text-white'
                  : 'bg-[var(--cf-raised)] border-[var(--cf-line)] text-gray-400 hover:text-gray-200 hover:bg-neutral-700'
              }`}
            >
              <div className="flex items-center gap-1.5 mb-0.5">
                {LAYOUT_ICONS[l.id] || <Crop size={14} />}
                <span className="text-xs font-semibold leading-tight">{l.name}</span>
                {isSelected && (
                  <span className="ml-auto w-1.5 h-1.5 rounded-full bg-[var(--cf-accent)]" />
                )}
              </div>
            </button>
          );
        })}
      </div>

      {/* Contextual Parameters & Adjustments Inspector */}
      <div className="bg-[var(--cf-base)] border border-[#232736] rounded-xl p-3.5 space-y-3">
        <div className="flex items-center justify-between border-b border-white/5 pb-2">
          <span className="text-xs font-bold text-gray-200 flex items-center gap-1.5">
            <Sliders size={13} className="text-[var(--cf-accent)]" />
            Adjust {activeLayout?.name || 'Layout'}
          </span>
          <span className="text-xs text-gray-400">9:16</span>
        </div>

        {/* 1. Center Crop Controls */}
        {selectedLayoutId === 'center_crop' && (
          <div className="space-y-2.5">
            <div>
              <div className="flex justify-between text-[11px] text-gray-400 mb-1">
                <span>Horizontal Pan</span>
                <span className="font-mono text-gray-300">
                  {Math.round((layoutParams.crop_x ?? 0.5) * 100)}%
                </span>
              </div>
              <input
                type="range"
                min="0"
                max="1"
                step="0.02"
                value={layoutParams.crop_x ?? 0.5}
                onChange={(e) => onUpdateParams({ crop_x: parseFloat(e.target.value) })}
                className="w-full accent-[var(--cf-accent)] bg-[#222634] h-1.5 rounded-lg appearance-none cursor-pointer"
              />
              <div className="flex justify-between text-[9px] text-gray-500 mt-0.5">
                <span>Left (0%)</span>
                <span>Center (50%)</span>
                <span>Right (100%)</span>
              </div>
            </div>
            <div>
              <div className="flex justify-between text-[11px] text-gray-400 mb-1">
                <span>Punch-In Zoom</span>
                <span className="font-mono text-gray-300">{(layoutParams.zoom ?? 1.0).toFixed(2)}x</span>
              </div>
              <input
                type="range"
                min="1.0"
                max="1.5"
                step="0.05"
                value={layoutParams.zoom ?? 1.0}
                onChange={(e) => onUpdateParams({ zoom: parseFloat(e.target.value) })}
                className="w-full accent-[var(--cf-accent)] bg-[#222634] h-1.5 rounded-lg appearance-none cursor-pointer"
              />
            </div>
          </div>
        )}

        {/* 2. Blur Fill Controls */}
        {selectedLayoutId === 'blur_fill' && (
          <div className="space-y-2.5">
            <div>
              <div className="flex justify-between text-[11px] text-gray-400 mb-1">
                <span>Ambient Blur Radius</span>
                <span className="font-mono text-gray-300">{layoutParams.blur_radius ?? 25}px</span>
              </div>
              <input
                type="range"
                min="5"
                max="50"
                step="1"
                value={layoutParams.blur_radius ?? 25}
                onChange={(e) => onUpdateParams({ blur_radius: parseInt(e.target.value) })}
                className="w-full accent-[var(--cf-accent)] bg-[#222634] h-1.5 rounded-lg appearance-none cursor-pointer"
              />
            </div>
            <div>
              <div className="flex justify-between text-[11px] text-gray-400 mb-1">
                <span>Foreground Scale</span>
                <span className="font-mono text-gray-300">
                  {Math.round((layoutParams.fg_scale ?? 1.0) * 100)}%
                </span>
              </div>
              <input
                type="range"
                min="0.8"
                max="1.2"
                step="0.05"
                value={layoutParams.fg_scale ?? 1.0}
                onChange={(e) => onUpdateParams({ fg_scale: parseFloat(e.target.value) })}
                className="w-full accent-[var(--cf-accent)] bg-[#222634] h-1.5 rounded-lg appearance-none cursor-pointer"
              />
            </div>
          </div>
        )}

        {/* 3. Fit & Pad Controls */}
        {selectedLayoutId === 'fit_pad' && (
          <div className="space-y-2">
            <span className="text-[11px] text-gray-400">Pillarbox Matte Color</span>
            <div className="grid grid-cols-3 gap-1.5">
              {[
                { label: 'Black', color: '#000000' },
                { label: 'Dark Slate', color: '#0f1117' },
                { label: 'Deep Indigo', color: '#111428' },
              ].map((item) => (
                <button
                  key={item.color}
                  onClick={() => onUpdateParams({ pad_color: item.color })}
                  className={`py-1.5 px-2 rounded-lg border text-xs font-medium text-center transition ${
                    (layoutParams.pad_color ?? '#000000') === item.color
                      ? 'bg-[var(--cf-accent-soft)]/30 border-[var(--cf-accent)] text-white'
                      : 'bg-[var(--cf-raised)] border-[var(--cf-line)] text-gray-400 hover:text-gray-200'
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* 4. Smart Face Crop Controls */}
        {selectedLayoutId === 'smart_face_crop' && (
          <div className="space-y-2.5">
            <div className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-xs text-emerald-300 flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <CheckCircle2 size={13} className="text-emerald-400" />
                Face-based framing
              </span>
              <span className="font-mono text-[11px] font-bold">
                {analysis?.unavailable ? 'Scan unavailable' : analysis?.analysis?.primary_face ? 'Face detected' : analysis ? 'No face detected' : 'Scanning clip…'}
              </span>
            </div>
            <div>
              <div className="flex justify-between text-[11px] text-gray-400 mb-1">
                <span>Fine Centering Offset</span>
                <span className="font-mono text-gray-300">
                  {Math.round((layoutParams.manual_offset_x ?? 0.0) * 100)}%
                </span>
              </div>
              <input
                type="range"
                min="-0.4"
                max="0.4"
                step="0.02"
                value={layoutParams.manual_offset_x ?? 0.0}
                onChange={(e) => onUpdateParams({ manual_offset_x: parseFloat(e.target.value) })}
                className="w-full accent-emerald-500 bg-[#222634] h-1.5 rounded-lg appearance-none cursor-pointer"
              />
            </div>
          </div>
        )}

        {/* 5. Active Speaker Crop Controls */}
        {selectedLayoutId === 'active_speaker_crop' && (
          <div className="space-y-2">
            <span className="text-[11px] text-gray-400">Speaker Focus Target</span>
            <div className="grid grid-cols-3 gap-1.5">
              {[
                { id: 'auto', label: 'Auto Track' },
                { id: 'speaker_1', label: 'Speaker 1 (L)' },
                { id: 'speaker_2', label: 'Speaker 2 (R)' },
              ].map((opt) => (
                <button
                  key={opt.id}
                  onClick={() => onUpdateParams({ speaker_mode: opt.id })}
                  className={`py-1.5 px-1.5 rounded-lg border text-[11px] font-semibold text-center transition ${
                    (layoutParams.speaker_mode ?? 'auto') === opt.id
                      ? 'bg-cyan-600/30 border-cyan-500 text-cyan-200'
                      : 'bg-[var(--cf-raised)] border-[var(--cf-line)] text-gray-400 hover:text-gray-200'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {selectedLayoutId === 'split_gaming' && (
          <div className="space-y-2">
            <label className="block text-xs text-gray-300">
              Webcam source
              <select className="studio-field mt-1 w-full" value={layoutParams.cam_pos ?? 'auto'}
                onChange={(event) => onUpdateParams({ cam_pos: event.target.value })}>
                <option value="auto">Auto-detect</option>
                <option value="top_left">Top left</option>
                <option value="top_right">Top right</option>
                <option value="bottom_left">Bottom left</option>
                <option value="bottom_right">Bottom right</option>
                <option value="center">Center</option>
              </select>
            </label>
          </div>
        )}

        {(['split_gaming', 'floating_facecam', 'screen_facecam'].includes(selectedLayoutId)
          && (selectedLayoutId !== 'split_gaming' || (layoutParams.cam_pos ?? 'auto') === 'auto')) && (
          <p className="text-xs text-gray-400" role="status">
            {analysis?.unavailable ? 'Face scan unavailable. Using the preset crop.'
              : analysis?.analysis?.webcam_region ? 'Facecam detected'
              : analysis?.analysis?.primary_face ? 'Framing the detected face'
              : analysis ? 'No face detected. Using the preset crop.'
              : 'Scanning this clip for a facecam…'}
          </p>
        )}

        {/* 7. Split 2-Faces Controls */}
        {selectedLayoutId === 'split_2_faces' && (
          <div className="space-y-2 text-xs text-gray-400">
            <div className="p-2 rounded-lg bg-purple-500/10 border border-purple-500/20 text-purple-300">
              Top 50% Face 1 · Bottom 50% Face 2. Perfect for podcasts, interviews, and multi-speaker dialogues.
            </div>
          </div>
        )}

        {/* 8. Floating Facecam (PiP) Controls */}
        {selectedLayoutId === 'floating_facecam' && (
          <div className="space-y-2.5">
            <div>
              <span className="text-[11px] text-gray-400 mb-1 block">Facecam PiP Corner</span>
              <div className="grid grid-cols-2 gap-1.5">
                {[
                  { id: 'bottom_right', label: 'Bottom-Right' },
                  { id: 'top_right', label: 'Top-Right' },
                  { id: 'bottom_left', label: 'Bottom-Left' },
                  { id: 'top_left', label: 'Top-Left' },
                ].map((pos) => (
                  <button
                    key={pos.id}
                    onClick={() => onUpdateParams({ pip_pos: pos.id })}
                    className={`py-1.5 px-2 rounded-lg border text-xs font-medium text-center transition ${
                      (layoutParams.pip_pos ?? 'bottom_right') === pos.id
                        ? 'bg-pink-600/30 border-pink-500 text-pink-200'
                        : 'bg-[var(--cf-raised)] border-[var(--cf-line)] text-gray-400 hover:text-gray-200'
                    }`}
                  >
                    {pos.label}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <span className="text-[11px] text-gray-400 mb-1 block">PiP Size</span>
              <div className="grid grid-cols-3 gap-1.5">
                {['small', 'medium', 'large'].map((s) => (
                  <button
                    key={s}
                    onClick={() => onUpdateParams({ pip_size: s })}
                    className={`py-1 px-2 rounded-lg border text-xs capitalize text-center transition ${
                      (layoutParams.pip_size ?? 'medium') === s
                        ? 'bg-pink-600/30 border-pink-500 text-pink-200'
                        : 'bg-[var(--cf-raised)] border-[var(--cf-line)] text-gray-400 hover:text-gray-200'
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* 9. Screen Share + Facecam Controls */}
        {selectedLayoutId === 'screen_facecam' && (
          <div className="space-y-2.5">
            <div>
              <div className="flex justify-between text-[11px] text-gray-400 mb-1">
                <span>Screen Share Height</span>
                <span className="font-mono text-gray-300">{layoutParams.screen_ratio ?? 65}%</span>
              </div>
              <input
                type="range"
                min="50"
                max="75"
                step="5"
                value={layoutParams.screen_ratio ?? 65}
                onChange={(e) => onUpdateParams({ screen_ratio: parseInt(e.target.value) })}
                className="w-full accent-blue-500 bg-[#222634] h-1.5 rounded-lg appearance-none cursor-pointer"
              />
            </div>
            <div className="text-[11px] text-gray-500">
              Top panel fits presentations/desktop recordings; bottom panel crops into presenter face.
            </div>
          </div>
        )}

        {/* 10. Framed Landscape + Headline Controls */}
        {selectedLayoutId === 'framed_headline' && (
          <div className="space-y-2.5">
            <div>
              <span className="text-[11px] text-gray-400 mb-1 block">Top Headline Text</span>
              <input
                type="text"
                value={layoutParams.headline ?? 'WATCH THIS'}
                onChange={(e) => onUpdateParams({ headline: e.target.value })}
                className="w-full px-2.5 py-1.5 bg-[var(--cf-raised)] border border-[var(--cf-line)] rounded-lg text-xs text-white placeholder-gray-500 focus:outline-none focus:border-amber-400 transition"
                placeholder="e.g. UNBELIEVABLE MOMENT"
              />
            </div>
            <div className="text-[11px] text-gray-500">
              Renders a bold punchy top banner above the 16:9 video, preserving space for subtitle captions below.
            </div>
          </div>
        )}

        {/* 11. Dynamic Pan & Zoom Controls */}
        {selectedLayoutId === 'dynamic_pan_zoom' && (
          <div className="space-y-2.5">
            <div>
              <div className="flex justify-between text-[11px] text-gray-400 mb-1">
                <span>Pan Speed Period</span>
                <span className="font-mono text-gray-300">{layoutParams.pan_period ?? 8}s</span>
              </div>
              <input
                type="range"
                min="4"
                max="15"
                step="1"
                value={layoutParams.pan_period ?? 8}
                onChange={(e) => onUpdateParams({ pan_period: parseFloat(e.target.value) })}
                className="w-full accent-teal-500 bg-[#222634] h-1.5 rounded-lg appearance-none cursor-pointer"
              />
            </div>
            <div className="text-[11px] text-gray-500">
              Smooth sinusoidal Ken Burns horizontal movement across wide landscape scenes.
            </div>
          </div>
        )}

        {/* 12. Auto Hybrid Insights */}
        {selectedLayoutId === 'auto_hybrid' && (
          <div className="space-y-2 text-xs">
            <p className="text-xs text-gray-300">Switches framing based on faces, speakers, and scene changes.</p>
            {analysis?.analysis && (
              <div className="bg-[var(--cf-raised)] p-2 rounded-lg border border-[var(--cf-line)] text-[10px] space-y-1">
                <div className="flex justify-between text-gray-400">
                  <span>Detected Faces:</span>
                  <span className="font-mono text-white">
                    {Math.round((analysis.analysis.face_detected_ratio || 0) * 100)}% of video
                  </span>
                </div>
                <div className="flex justify-between text-gray-400">
                  <span>Interview / 2-Face Signal:</span>
                  <span className="font-mono text-white">
                    {Math.round((analysis.analysis.multi_face_ratio || 0) * 100)}%
                  </span>
                </div>
                <div className="flex justify-between text-gray-400">
                  <span>Primary Recommendation:</span>
                  <span className="font-mono text-[var(--cf-accent)] font-bold uppercase">
                    {analysis.analysis.recommended_layout}
                  </span>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
