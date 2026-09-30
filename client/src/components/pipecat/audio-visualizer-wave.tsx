"use client";
import { usePipecatClientMediaTrack } from "@pipecat-ai/client-react";
import {
  animate,
  motionValue,
  type MotionValue,
  type ValueAnimationTransition,
} from "motion/react";
import {
  memo,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";

import { WaveShader } from "@/components/pipecat/wave-shader";
import {
  createVisualizerAnalyser,
  resolveVisualizerColor,
  type VisualizerState,
} from "@/lib/visualizer";

export type { VisualizerState };

type ParticipantType = Parameters<typeof usePipecatClientMediaTrack>[1];

export type DitherMethod = "bayer2" | "bayer4" | "bayer8" | "ign" | "noise";

export interface DitherOptions {
  /** Threshold pattern (default "bayer8"). */
  method?: DitherMethod;
  /** Pixel grid size in CSS px (1–4096, default 3). */
  pixelSize?: number;
  /** Color steps per channel (rounded to 2–256, default 4). */
  levels?: number;
  /** Alpha steps (rounded to 2–256, defaults to levels). */
  alphaLevels?: number;
  /** Pattern strength (0–1, default 1); 0 gives plain posterization. */
  strength?: number;
}

export interface AudioVisualizerWaveViewProps {
  /** Audio track to visualize. Rests as a calm drifting aura when null. */
  track?: MediaStreamTrack | null;
  /**
   * Aura color. Any CSS color, "currentColor", or a "--css-var" name.
   * Defaults to a vivid cyan — the aura is made of light, so near-black
   * colors wash out to gray.
   */
  color?: string;
  /** Second CSS color; pins the palette and disables speech-driven hue spreading. */
  accentColor?: string;
  /** Palette banding frequency (default 0.05); hue range is controlled by hueSpread. */
  colorShift?: number;
  /** Accent hue offset as a fraction of the color wheel (default 0.18). Ignored with accentColor. */
  hueSpread?: number;
  /** Visualizer size in px (square, default 224). */
  size?: number;
  /** Animation speed multiplier over the state's base pace (default 1). */
  speed?: number;
  /** Edge-deformation multiplier over the state's base depth (default 1). */
  amplitude?: number;
  /** Brightness multiplier over the state's base glow (default 1). */
  glow?: number;
  /** Edge blur/softness of the aura (default 0.2). */
  softness?: number;
  /** Body fill: 0 for contour wisps, 1 for a solid orb (default 0.4). */
  fill?: number;
  /** Layer falloff relative to sample travel (0–1, default 0.28); higher values soften banding. */
  smoothing?: number;
  /**
   * Ink gain before tone mapping (default 0.55). The tone curve saturates
   * hard, so a lower density keeps the mid-tones inside its responsive
   * range and the contours visible; raise it for a denser, hotter aura.
   */
  density?: number;
  /**
   * Brightness at the very centre of the hollow (0–1, default 0.1). 0
   * empties the middle completely; raise it to keep a glow in there.
   */
  core?: number;
  /**
   * Fraction of the radius left open in the middle (0–1, default 0.3).
   * The ink ramps from `core` at this radius up to full at the surface,
   * so the orb reads as a shell with a void you can see into.
   */
  hollow?: number;
  /** Shell depth as a fraction of the radius (0–1, default 0.22). */
  depth?: number;
  /**
   * Specular highlight strength (default 0.2; 0 removes it). Kept low now
   * that the middle is hollow — it sits over the void, where it reads
   * much more strongly than it did against a filled body.
   */
  highlight?: number;
  /** Remove the specular shine over the hollow. */
  noHighlight?: boolean;
  /**
   * Shine tint. Any CSS color, "currentColor", or a "--css-var" name.
   * Defaults to white warmed a little toward the aura color.
   */
  highlightColor?: string;
  /** Ribbon count (default 24). Changing it recompiles the shader; GPU cost scales linearly. */
  iterations?: number;
  /**
   * Optional pixel grid and palette quantization (default false).
   * Toggling or changing method recompiles; numeric options update as uniforms.
   */
  dither?: boolean | DitherOptions;
  /**
   * Background optimization: "dark" composites tonemapped light, "light"
   * boosts saturation. Defaults to detecting the root element's "dark"
   * class, tracked live.
   */
  themeMode?: "dark" | "light";
  /**
   * Override the speech level computed from the track (0–1) — stands in
   * for both the phrase envelope and the gain-following pulse.
   */
  volume?: number;
  /** Connecting takes precedence over thinking and ignores the audio track. */
  isConnecting?: boolean;
  /**
   * Override: the bot is working on a response — the aura pulses deeply.
   * The track is ignored while set.
   */
  isThinking?: boolean;
  className?: string;
}

/** Ignore frame gaps longer than this (tab was backgrounded). */
const MAX_FRAME_SECONDS = 0.1;

/**
 * Band the level is measured over. Speech lives here, and the near-silent
 * top of the spectrum would otherwise drag the average down to a fraction
 * of its range — which reads as an aura that barely responds.
 */
const ENERGY_BAND_HZ = { low: 200, high: 8000 };

/** Band RMS at conversational level, which maps to a full-scale 1. */
const ENERGY_REFERENCE_RMS = 0.3;
/** Envelope time constants in seconds; a slower release smooths gaps between phrases. */
const ENERGY_ATTACK_SECONDS = 0.32;
const ENERGY_RELEASE_SECONDS = 1.3;

/** A faster envelope makes body scale follow syllables while other parameters follow phrases. */
const PULSE_ATTACK_SECONDS = 0.06;
const PULSE_RELEASE_SECONDS = 0.28;

/** Radians per second at speed 1. Integrate phase so pace changes do not jump the pattern. */
const PHASE_RATE = 0.05;

/**
 * Hue rotations per second at speed 1. Integrated at the same speed as the
 * phase, so the palette barely creeps at rest and visibly travels while
 * the bot is talking.
 */
const HUE_RATE = 0.0018;

const DEFAULT_TRANSITION: ValueAnimationTransition = {
  duration: 0.5,
  ease: "easeOut",
};
const DEFAULT_PULSE_TRANSITION: ValueAnimationTransition = {
  duration: 0.35,
  ease: "easeOut",
  repeat: Infinity,
  repeatType: "mirror",
};

interface AuraStateParams {
  speed: number;
  scale: number;
  amplitude: number;
  frequency: number;
  variance: number;
  brightness: number;
}

/** Speaking starts at the quiet baseline; ENERGY_GAIN adds the response to live speech. */
const STATE_PARAMS: Record<VisualizerState, AuraStateParams> = {
  silent: {
    speed: 6,
    scale: 0.22,
    amplitude: 0.35,
    frequency: 0.35,
    variance: 0.06,
    brightness: 1.15,
  },
  connecting: {
    speed: 20,
    scale: 0.26,
    amplitude: 0.45,
    frequency: 0.7,
    variance: 0.1,
    brightness: 1.5,
  },
  thinking: {
    speed: 30,
    scale: 0.26,
    amplitude: 0.4,
    frequency: 1.0,
    variance: 0.2,
    brightness: 1.5,
  },
  speaking: {
    speed: 8,
    scale: 0.25,
    amplitude: 0.35,
    frequency: 0.45,
    variance: 0.08,
    brightness: 1.25,
  },
};

/** Full-speech gains. Scale uses the fast pulse envelope; other parameters use slow energy. */
const ENERGY_GAIN = {
  /** ~35% of the resting radius at full gain. */
  scale: 0.085,
  speed: 70,
  amplitude: 1.0,
  frequency: 1.0,
  variance: 0.7,
  /** Fraction of extra glow at full energy. */
  brightness: 0.9,
  /** Multiplier on the caller's colorShift at full energy. */
  colorShift: 1.6,
  /**
   * Multiplier on the caller's hueSpread at full energy. Speech pushes the
   * two tones further apart — more color in the orb — without ever adding
   * a third.
   */
  hueSpread: 0.6,
};

/** The two waiting states breathe; the rest hold a steady glow. */
const PULSE_BRIGHTNESS: Partial<Record<VisualizerState, number[]>> = {
  connecting: [1.7, 2.3],
  thinking: [0.8, 2.8],
};

/**
 * Displacement budget of the turbulence, in units of amplitude/frequency:
 * each of the 4 layers pushes along the swirl matrix's first column
 * (length ≈ 0.65) at 1.4× the previous layer's frequency (Σ 1.4⁻ⁱ ≈ 2.58).
 */
const TURB_REACH = 0.65 * 2.58;
/** Visible fraction of the worst-case bound, calibrated against the shader at full speech energy. */
const TURB_VISIBLE_REACH = 0.74;

/** Fit full-energy speech to the canvas; the resting margin leaves room for swelling. */
function auraLogicalSpan(amplitudeProp: number, softness: number): number {
  const atFullEnergy = {
    scale: STATE_PARAMS.speaking.scale + ENERGY_GAIN.scale,
    amplitude: STATE_PARAMS.speaking.amplitude + ENERGY_GAIN.amplitude,
    frequency: STATE_PARAMS.speaking.frequency + ENERGY_GAIN.frequency,
  };
  const params = [...Object.values(STATE_PARAMS), atFullEnergy];
  const maxScale = Math.max(...params.map((p) => p.scale));
  // Displacement is amplitude over the shader's mix(2, 15, uFrequency).
  const maxTurb = Math.max(
    ...params.map((p) => p.amplitude / (2 + 13 * p.frequency)),
  );
  const churn = maxTurb * amplitudeProp * TURB_REACH * TURB_VISIBLE_REACH;
  // The blurred edge glows a little past the surface.
  const glow = 0.05 * softness + 0.005;
  return 2 * (maxScale + churn + glow);
}

// #1FD5F9 as shader RGB — the fallback when a color can't be parsed.
const DEFAULT_RGB: [number, number, number] = [0.121, 0.835, 0.976];

/** Resolved CSS color → RGB floats for the shader uniform. */
function colorToRgb(color: string): [number, number, number] {
  const ctx = document.createElement("canvas").getContext("2d");
  if (!ctx) return DEFAULT_RGB;
  ctx.fillStyle = color;
  const normalized = String(ctx.fillStyle);
  if (normalized.startsWith("#")) {
    const n = parseInt(normalized.slice(1, 7), 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }
  const parts = normalized.match(/[\d.]+/g);
  if (parts && parts.length >= 3) {
    return [
      Number(parts[0]) / 255,
      Number(parts[1]) / 255,
      Number(parts[2]) / 255,
    ];
  }
  return DEFAULT_RGB;
}

/**
 * RGB floats → HSV floats, the inverse of the shader's hsv2rgb. The
 * palette endpoints are resolved here so the shader never has to convert
 * a color forward per pixel.
 */
function rgbToHsv([r, g, b]: [number, number, number]): [
  number,
  number,
  number,
] {
  const max = Math.max(r, g, b);
  const d = max - Math.min(r, g, b);
  let h = 0;
  if (d > 0) {
    if (max === r) h = ((g - b) / d + 6) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h /= 6;
  }
  return [h, max === 0 ? 0 : d / max, max];
}

const DITHER_DEFAULTS = {
  method: "bayer8",
  pixelSize: 3,
  levels: 4,
  strength: 1,
} as const satisfies Required<Omit<DitherOptions, "alphaLevels">>;

const DITHER_PATTERN: Record<DitherMethod, string> = {
  bayer2: "b2(c)",
  bayer4: "b4(c)",
  bayer8: "b8(c)",
  ign: "fract(52.9829189 * fract(dot(c, vec2(0.06711056, 0.00583715))))",
  noise: "rf(c).x",
};

function ditherValue(
  value: number | undefined,
  fallback: number,
  min: number,
  max: number,
) {
  return Math.min(
    max,
    Math.max(
      min,
      value !== undefined && Number.isFinite(value) ? value : fallback,
    ),
  );
}

const makeDitherSource = (method: DitherMethod) => `
float b2(vec2 a) {
  a = mod(floor(a), 2.0);
  return fract(a.x * 0.5 + a.y * a.y * 0.75);
}
float b4(vec2 a) { return b2(a * 0.5) * 0.25 + b2(a); }
float b8(vec2 a) { return b4(a * 0.5) * 0.25 + b2(a); }

float dt(vec2 c) {
  return mix(0.5, ${DITHER_PATTERN[method]}, uDitherStrength);
}

float qz(float x, float l, float t) {
  float s = max(l - 1.0, 1.0);
  return floor(clamp(x, 0.0, 1.0) * s + t) / s;
}

`;

/** Ribbon count and dither method are compile-time constants; disabled dither adds no fragment work. */
const makeShaderSource = (iterations: number, dither: DitherMethod | null) => {
  const antiBanding = dither ? "" : "    cl += (rf(I).x - 0.5) / 255.0;\n";
  return `
const float T = 6.283185;
const float N = ${iterations.toFixed(1)};

vec2 rf(vec2 p) {
  p = fract(p * vec2(443.897, 441.423));
  p += dot(p, p.yx + 19.19);
  return fract((p.xx + p.yx) * p.xy);
}

vec3 tm(vec3 x) {
  x *= 4.0;
  return x / (1.0 + x);
}

float lm(vec3 cl) {
  return dot(cl, vec3(0.299, 0.587, 0.114));
}

vec3 hr(vec3 c) {
  vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

float sc(vec2 st, float r) {
  return length(st) - r;
}

vec2 tb(vec2 q, float t, float it) {

  mat2 m0 = mat2(0.6, -0.25, 0.25, 0.9);

  mat2 m1 = mat2(0.6, -0.8, 0.8, 0.6);

  float f = mix(2.0, 15.0, uFrequency);
  float a = uAmplitude;
  float g = 1.4;
  float at = t;

  const int L = 4;
  for(int i = 0; i < L; i++) {

    vec2 rp = q * m0;
    vec2 w = sin(f * rp + float(i) * at + it);

    q += (a / f) * m0[0] * w;

    m0 *= m1;
    a *= mix(1.0, max(w.x, w.y), uVariance);
    f *= g;
  }

  return q;
}

${dither ? makeDitherSource(dither) : ""}void mainImage(out vec4 O, in vec2 I) {
${
  dither
    ? `  vec2 c = floor(I / uPixelSize);
  I = (c + 0.5) * uPixelSize;
`
    : ""
}  vec2 uv = I / iResolution.xy;

  vec3 pp = vec3(0.0);

  float t = uPhase;

  vec2 q = (uv - 0.5) * uSpan;

  vec2 pv = tb(q, t, 0.0 - 1.0 / N);
  float sp = mix(1.0, T, 0.5);

  float rn = length(q) / max(uScale, 1e-5);

  float ho = mix(uCore, 1.0, smoothstep(uHollow, 1.0, rn));

  for(float i = 1.0; i < N + 1.0; i++) {
    float k = i / N;
    vec2 st = tb(q, t, k * sp);

    float sd = sc(st, uScale * (1.0 - uDepth * k));
    float d = abs(sd);
    float pd = distance(st, pv);
    pv = st;

    float db = exp2(pd * 2.0 * 1.4426950408889634) - 1.0;
    float e = uBlur * 0.05 + max(db * uSmoothing, 0.001);

    float sh = smoothstep(0.0, e, d) - 1.0;
    float bd = smoothstep(0.0, e, max(sd, 0.0)) - 1.0;
    float ik = mix(sh, bd, uFill) * ho;

    vec3 cl = uColor;
    if(dot(abs(uHsvDelta), vec3(1.0)) > 0.001) {

      float w = 0.5 + 0.5 * sin(T * (uHueDrift + k * uColorShift * 1.5));

      vec3 hs = uColorHsv + uHsvDelta * smoothstep(0.2, 0.8, w);
      hs.x = fract(hs.x);
      hs.yz = clamp(hs.yz, 0.0, 1.0);
      cl = hr(hs);
    }

    pp += ik * cl;
  }

  pp *= 1.0 / N;

  vec2 hp = q - vec2(-0.3, 0.3) * uScale;
  float ss = uHighlight * exp(-dot(hp, hp) / max(uScale * uScale * 0.07, 1e-6));
  vec3 hl = ss * uHighlightColor;

  vec3 cl;

  if(uMode < 0.5) {
    cl = (-pp * uDensity) * 1.2 + hl;
${antiBanding}    cl = tm(cl);
    float al = lm(cl) * uMix;
    O = vec4(cl * uMix, al);
  }

  else {
    cl = -pp * uDensity + hl;
${antiBanding}
    float br = length(cl);
    vec3 di = br > 0.0 ? cl / br : cl;

    float fa = 2.0;
    float mb = (br * fa) / (1.0 + br * fa);
    cl = di * mb;

    float gr = dot(cl, vec3(0.2, 0.5, 0.1));
    float sb = 3.0;
    cl = mix(vec3(gr), cl, sb);

    cl = clamp(cl, 0.0, 1.0);

    float al = mb * clamp(uMix, 1.0, 2.0);
    O = vec4(cl, al);
  }
${
  dither
    ? `
  float th = dt(c);
  O = vec4(
    qz(O.r, uDitherLevels, th),
    qz(O.g, uDitherLevels, th),
    qz(O.b, uDitherLevels, th),
    qz(O.a, uDitherAlphaLevels, th)
  );
`
    : ""
}}`;
};

/**
 * The mutable uniform store behind one shader instance. WaveShader
 * reads each .value fresh every frame, so animation writes land here
 * directly — React never re-renders on the hot path.
 */
type AuraUniformStore = {
  /** Accumulated wave phase, advanced per frame at the current pace. */
  uPhase: { type: "1f"; value: number };
  /** Fit full-energy speech to the canvas; the resting margin leaves room for swelling. */
  uSpan: { type: "1f"; value: number };
  /** Edge blur/softness. */
  uBlur: { type: "1f"; value: number };
  /** Orb radius in shader space (state base + pulse swell). */
  uScale: { type: "1f"; value: number };
  /** Wave frequency and complexity. */
  uFrequency: { type: "1f"; value: number };
  /** Turbulence amplitude (state base + energy, × amplitude prop). */
  uAmplitude: { type: "1f"; value: number };
  /** Brightness of the aurora (state base × energy boost × glow prop). */
  uMix: { type: "1f"; value: number };
  /** Amplitude variation across layers (0–1) — the churn's raggedness. */
  uVariance: { type: "1f"; value: number };
  /** Layer falloff relative to sample travel (0–1, default 0.28); higher values soften banding. */
  uSmoothing: { type: "1f"; value: number };
  /** Ink gain before tone mapping; low leaves headroom for gradients. */
  uDensity: { type: "1f"; value: number };
  /** 0 = open contour wisps, 1 = a filled body. */
  uFill: { type: "1f"; value: number };
  /** Brightness at the very centre (0–1); the floor of the hollow. */
  uCore: { type: "1f"; value: number };
  /** Fraction of the radius left open in the middle (0–1). */
  uHollow: { type: "1f"; value: number };
  /** How far the innermost layer recedes, as a fraction of the radius. */
  uDepth: { type: "1f"; value: number };
  /** Specular highlight strength. */
  uHighlight: { type: "1f"; value: number };
  /** Rotating hue offset, advanced per frame with the phase. */
  uHueDrift: { type: "1f"; value: number };
  /** How often the palette crosses between its two tones, per layer. */
  uColorShift: { type: "1f"; value: number };
  /** HSV step from the base color to its accent tone. */
  uHsvDelta: { type: "3fv"; value: [number, number, number] };
  /** Display mode: 0=dark background, 1=light background. */
  uMode: { type: "1f"; value: number };
  /** Aura color as shader RGB floats. */
  uColor: { type: "3fv"; value: [number, number, number] };
  /** The aura color again, pre-converted to HSV for the palette blend. */
  uColorHsv: { type: "3fv"; value: [number, number, number] };
  /** Shine tint as shader RGB floats. */
  uHighlightColor: { type: "3fv"; value: [number, number, number] };
  uPixelSize: { type: "1f"; value: number };
  uDitherLevels: { type: "1f"; value: number };
  uDitherAlphaLevels: { type: "1f"; value: number };
  uDitherStrength: { type: "1f"; value: number };
};

interface AuraEngine {
  speed: MotionValue<number>;
  scale: MotionValue<number>;
  amplitude: MotionValue<number>;
  frequency: MotionValue<number>;
  variance: MotionValue<number>;
  brightness: MotionValue<number>;
  /** Accumulated wave phase (see PHASE_RATE). */
  phase: number;
  /** Rotating hue offset 0–1 (see HUE_RATE). */
  hueDrift: number;
  uniforms: AuraUniformStore;
}

/**
 * Motion values plus the uniform store they feed; created once per
 * mounted visualizer, seeded so the mount animation eases into the
 * silent baseline.
 */
function createAuraEngine(): AuraEngine {
  return {
    speed: motionValue(STATE_PARAMS.silent.speed),
    scale: motionValue(0.2),
    amplitude: motionValue(1.0),
    frequency: motionValue(0.35),
    variance: motionValue(0.06),
    brightness: motionValue(0.9),
    phase: 0,
    hueDrift: 0,
    uniforms: {
      uPhase: { type: "1f", value: 0 },
      uSpan: { type: "1f", value: auraLogicalSpan(1, 0.2) },
      uBlur: { type: "1f", value: 0.2 },
      uScale: { type: "1f", value: 0.2 },
      uFrequency: { type: "1f", value: 0.35 },
      uAmplitude: { type: "1f", value: 1.0 },
      uMix: { type: "1f", value: 0.9 },
      uVariance: { type: "1f", value: 0.06 },
      uSmoothing: { type: "1f", value: 0.28 },
      uDensity: { type: "1f", value: 0.55 },
      uFill: { type: "1f", value: 0.4 },
      uCore: { type: "1f", value: 0.1 },
      uHollow: { type: "1f", value: 0.3 },
      uDepth: { type: "1f", value: 0.22 },
      uHighlight: { type: "1f", value: 0.2 },
      uHueDrift: { type: "1f", value: 0 },
      uColorShift: { type: "1f", value: 0.05 },
      uHsvDelta: { type: "3fv", value: [0.18, 0, 0] },
      uMode: { type: "1f", value: 1.0 },
      uColor: { type: "3fv", value: [...DEFAULT_RGB] },
      uColorHsv: { type: "3fv", value: rgbToHsv(DEFAULT_RGB) },
      uHighlightColor: { type: "3fv", value: defaultHighlightRgb(DEFAULT_RGB) },
      uPixelSize: { type: "1f", value: DITHER_DEFAULTS.pixelSize },
      uDitherLevels: { type: "1f", value: DITHER_DEFAULTS.levels },
      uDitherAlphaLevels: { type: "1f", value: DITHER_DEFAULTS.levels },
      uDitherStrength: { type: "1f", value: DITHER_DEFAULTS.strength },
    },
  };
}

/** The default shine tint: white warmed a little toward the aura color. */
function defaultHighlightRgb(
  rgb: [number, number, number],
): [number, number, number] {
  return [0.65 + 0.35 * rgb[0], 0.65 + 0.35 * rgb[1], 0.65 + 0.35 * rgb[2]];
}

/** A live analyser and the speech envelopes integrated over it. */
interface AuraAudio {
  analyser: AnalyserNode;
  data: Uint8Array<ArrayBuffer>;
  firstBin: number;
  lastBin: number;
  binCount: number;
  /** Phrase-shaped energy: slow to rise, slower to fall. */
  energy: number;
  /** Gain-following level for the body's swell. */
  pulse: number;
}

/** Per-frame inputs the compositor reads without re-subscribing. */
interface AuraFrameInputs {
  state: VisualizerState;
  volume?: number;
  speed: number;
  amplitude: number;
  glow: number;
  colorShift: number;
  hueSpread: number;
}

/** One rAF loop updates uniforms without React renders. Energy follows phrases; pulse follows syllables. Envelopes use elapsed time. */
function useAuraUniforms(opts: {
  state: VisualizerState;
  track: MediaStreamTrack | null;
  volume?: number;
  color: string;
  accentColor?: string;
  colorShift: number;
  hueSpread: number;
  speed: number;
  amplitude: number;
  glow: number;
  softness: number;
  fill: number;
  core: number;
  hollow: number;
  depth: number;
  smoothing: number;
  density: number;
  highlight: number;
  noHighlight: boolean;
  highlightColor?: string;
  ditherPixelSize: number;
  ditherLevels: number;
  ditherAlphaLevels: number;
  ditherStrength: number;
  themeMode?: "dark" | "light";
  className?: string;
  rootRef: RefObject<HTMLDivElement | null>;
}): AuraUniformStore {
  const {
    state,
    track,
    volume,
    color,
    accentColor,
    colorShift,
    hueSpread,
    speed,
    amplitude,
    glow,
    softness,
    fill,
    core,
    hollow,
    depth,
    smoothing,
    density,
    highlight,
    noHighlight,
    highlightColor,
    ditherPixelSize,
    ditherLevels,
    ditherAlphaLevels,
    ditherStrength,
    themeMode,
    className,
    rootRef,
  } = opts;
  const [engine] = useState(createAuraEngine);
  const audioRef = useRef<AuraAudio | null>(null);
  // The HSV step to an explicit accentColor; null derives it from
  // hueSpread instead.
  const accentDeltaRef = useRef<[number, number, number] | null>(null);
  const frameInputsRef = useRef<AuraFrameInputs>({
    state,
    volume,
    speed,
    amplitude,
    glow,
    colorShift,
    hueSpread,
  });

  useEffect(() => {
    frameInputsRef.current = {
      state,
      volume,
      speed,
      amplitude,
      glow,
      colorShift,
      hueSpread,
    };
  });

  // The compositor: one always-on rAF loop for everything per-frame.
  // Unmount cancels it and parks the motion values — the infinite pulse
  // animations would otherwise tick forever.
  useEffect(() => {
    const { uniforms } = engine;
    let rafId = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const delta = Math.min((now - last) / 1000, MAX_FRAME_SECONDS);
      last = now;
      const inputs = frameInputsRef.current;
      const audio = audioRef.current;
      const speaking = inputs.state === "speaking";

      // Only speech drives the boost; the other states are self-animated.
      // An explicit volume override stands in for both envelopes.
      let energy = 0;
      let pulse = 0;
      if (speaking && inputs.volume !== undefined) {
        energy = pulse = inputs.volume;
      } else if (speaking && audio) {
        audio.analyser.getByteFrequencyData(audio.data);
        let sum = 0;
        for (let i = audio.firstBin; i <= audio.lastBin; i++) {
          const v = (audio.data[i] ?? 0) / 255;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / audio.binCount);
        const level = Math.min(1, rms / ENERGY_REFERENCE_RMS);

        const seconds =
          level > audio.energy ? ENERGY_ATTACK_SECONDS : ENERGY_RELEASE_SECONDS;
        audio.energy +=
          (level - audio.energy) * (1 - Math.exp(-delta / seconds));
        const pulseSeconds =
          level > audio.pulse ? PULSE_ATTACK_SECONDS : PULSE_RELEASE_SECONDS;
        audio.pulse +=
          (level - audio.pulse) * (1 - Math.exp(-delta / pulseSeconds));
        energy = audio.energy;
        pulse = audio.pulse;
      }

      // Both phases integrate at the current pace, so the churn and the
      // color rotation accelerate together when the voice picks up.
      const pace =
        (engine.speed.get() + ENERGY_GAIN.speed * energy) * inputs.speed;
      engine.phase += delta * PHASE_RATE * pace;
      engine.hueDrift = (engine.hueDrift + delta * HUE_RATE * pace) % 1;

      // Energy rides on top of the animated baselines, so the two
      // compose: state changes still ease, and the voice moves the aura
      // within a state.
      uniforms.uPhase.value = engine.phase;
      uniforms.uHueDrift.value = engine.hueDrift;
      uniforms.uScale.value = engine.scale.get() + ENERGY_GAIN.scale * pulse;
      uniforms.uAmplitude.value =
        (engine.amplitude.get() + ENERGY_GAIN.amplitude * energy) *
        inputs.amplitude;
      uniforms.uFrequency.value =
        engine.frequency.get() + ENERGY_GAIN.frequency * energy;
      uniforms.uVariance.value =
        engine.variance.get() + ENERGY_GAIN.variance * energy;
      uniforms.uMix.value =
        engine.brightness.get() *
        (1 + ENERGY_GAIN.brightness * energy) *
        inputs.glow;
      // Speech interleaves the two tones more finely through the orb and,
      // when the accent is derived from hueSpread, pushes them further
      // apart; an explicit accentColor pins the pair instead.
      uniforms.uColorShift.value =
        inputs.colorShift * (1 + ENERGY_GAIN.colorShift * energy);
      const hsvDelta = uniforms.uHsvDelta.value;
      const accentDelta = accentDeltaRef.current;
      if (accentDelta) {
        hsvDelta[0] = accentDelta[0];
        hsvDelta[1] = accentDelta[1];
        hsvDelta[2] = accentDelta[2];
      } else {
        hsvDelta[0] = inputs.hueSpread * (1 + ENERGY_GAIN.hueSpread * energy);
        hsvDelta[1] = 0;
        hsvDelta[2] = 0;
      }

      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(rafId);
      engine.speed.stop();
      engine.scale.stop();
      engine.amplitude.stop();
      engine.frequency.stop();
      engine.variance.stop();
      engine.brightness.stop();
    };
  }, [engine]);

  // The analyser only exists while speaking without a volume override.
  // Its envelopes live with it, so a new speaking session starts from
  // silence rather than inheriting a stale level.
  useEffect(() => {
    if (state !== "speaking" || !track || volume !== undefined) return;
    const { analyser, dispose } = createVisualizerAnalyser(track);
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.55;
    const data = new Uint8Array(analyser.frequencyBinCount);

    // Bin range covering ENERGY_BAND_HZ.
    const binHz = analyser.context.sampleRate / 2 / analyser.frequencyBinCount;
    const firstBin = Math.max(1, Math.floor(ENERGY_BAND_HZ.low / binHz));
    const lastBin = Math.min(
      data.length - 1,
      Math.ceil(ENERGY_BAND_HZ.high / binHz),
    );
    audioRef.current = {
      analyser,
      data,
      firstBin,
      lastBin,
      binCount: Math.max(1, lastBin - firstBin + 1),
      energy: 0,
      pulse: 0,
    };
    return () => {
      audioRef.current = null;
      dispose();
    };
  }, [state, track, volume]);

  useEffect(() => {
    const base = STATE_PARAMS[state];
    const scaleTransition: ValueAnimationTransition =
      state === "connecting"
        ? { type: "spring", duration: 1.0, bounce: 0.35 }
        : DEFAULT_TRANSITION;
    const pulse = PULSE_BRIGHTNESS[state];

    animate(engine.speed, base.speed, DEFAULT_TRANSITION);
    animate(engine.scale, base.scale, scaleTransition);
    animate(engine.amplitude, base.amplitude, DEFAULT_TRANSITION);
    animate(engine.frequency, base.frequency, DEFAULT_TRANSITION);
    animate(engine.variance, base.variance, DEFAULT_TRANSITION);
    animate(
      engine.brightness,
      pulse ?? base.brightness,
      pulse ? DEFAULT_PULSE_TRANSITION : DEFAULT_TRANSITION,
    );
  }, [engine, state]);

  useEffect(() => {
    const { uniforms } = engine;
    uniforms.uSpan.value = auraLogicalSpan(amplitude, softness);
    uniforms.uBlur.value = softness;
    uniforms.uFill.value = fill;
    uniforms.uCore.value = core;
    uniforms.uHollow.value = hollow;
    uniforms.uDepth.value = depth;
    uniforms.uSmoothing.value = smoothing;
    uniforms.uDensity.value = density;
    uniforms.uHighlight.value = noHighlight ? 0 : highlight;
    uniforms.uPixelSize.value = ditherPixelSize;
    uniforms.uDitherLevels.value = ditherLevels;
    uniforms.uDitherAlphaLevels.value = ditherAlphaLevels;
    uniforms.uDitherStrength.value = ditherStrength;
  }, [
    engine,
    amplitude,
    softness,
    fill,
    core,
    hollow,
    depth,
    smoothing,
    density,
    highlight,
    noHighlight,
    ditherPixelSize,
    ditherLevels,
    ditherAlphaLevels,
    ditherStrength,
  ]);

  // Resolve CSS-flavored colors to shader RGB. className is a dependency
  // because it can change what currentColor resolves to.
  useEffect(() => {
    const { uniforms } = engine;
    const rgb = colorToRgb(resolveVisualizerColor(color, rootRef.current));
    const baseHsv = rgbToHsv(rgb);
    uniforms.uColor.value = rgb;
    uniforms.uColorHsv.value = baseHsv;

    if (accentColor) {
      const accentHsv = rgbToHsv(
        colorToRgb(resolveVisualizerColor(accentColor, rootRef.current)),
      );
      // Hue takes the short way around the wheel.
      let hue = accentHsv[0] - baseHsv[0];
      hue -= Math.round(hue);
      accentDeltaRef.current = [
        hue,
        accentHsv[1] - baseHsv[1],
        accentHsv[2] - baseHsv[2],
      ];
    } else {
      accentDeltaRef.current = null;
    }

    uniforms.uHighlightColor.value = highlightColor
      ? colorToRgb(resolveVisualizerColor(highlightColor, rootRef.current))
      : defaultHighlightRgb(rgb);
  }, [engine, color, accentColor, highlightColor, className, rootRef]);

  // Without an explicit themeMode, follow the documentElement's "dark"
  // class live.
  useEffect(() => {
    if (themeMode) {
      engine.uniforms.uMode.value = themeMode === "light" ? 1.0 : 0.0;
      return;
    }
    const root = document.documentElement;
    const apply = () => {
      engine.uniforms.uMode.value = root.classList.contains("dark") ? 0.0 : 1.0;
    };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, [engine, themeMode]);

  return engine.uniforms;
}

// MSAA does nothing for a fullscreen quad and the shader needs no depth
// buffer. Alpha stays on — both shader modes composite via destination
// alpha.
const AURA_CONTEXT_ATTRIBUTES = { antialias: false, depth: false };

// The aura is soft by design, so supersampling past 1.5x is invisible —
// and fragment cost is quadratic in the pixel ratio.
const MAX_PIXEL_RATIO = 1.5;

/** Track-reactive shader aura; connecting and thinking override track state. Requires WebGL. */
export const AudioVisualizerWaveView = memo(function AudioVisualizerWaveView({
  track = null,
  color = "#1FD5F9",
  accentColor,
  colorShift = 0.05,
  hueSpread = 0.18,
  size = 224,
  speed = 1,
  amplitude = 1,
  glow = 1,
  softness = 0.2,
  fill = 0.4,
  core = 0.1,
  hollow = 0.3,
  depth = 0.22,
  smoothing = 0.28,
  density = 0.55,
  highlight = 0.2,
  noHighlight = false,
  highlightColor,
  iterations = 24,
  dither = false,
  themeMode,
  volume,
  isConnecting = false,
  isThinking = false,
  className,
}: AudioVisualizerWaveViewProps) {
  const rootRef = useRef<HTMLDivElement>(null);

  const ditherOpts = typeof dither === "object" ? dither : undefined;
  const method = ditherOpts?.method ?? DITHER_DEFAULTS.method;
  const ditherMethod = dither
    ? Object.hasOwn(DITHER_PATTERN, method)
      ? method
      : DITHER_DEFAULTS.method
    : null;
  const ditherPixelSize = ditherValue(
    ditherOpts?.pixelSize,
    DITHER_DEFAULTS.pixelSize,
    1,
    4096,
  );
  const ditherLevels = Math.round(
    ditherValue(ditherOpts?.levels, DITHER_DEFAULTS.levels, 2, 256),
  );
  const ditherAlphaLevels = Math.round(
    ditherValue(ditherOpts?.alphaLevels, ditherLevels, 2, 256),
  );
  const ditherStrength = ditherValue(
    ditherOpts?.strength,
    DITHER_DEFAULTS.strength,
    0,
    1,
  );

  const state: VisualizerState = isConnecting
    ? "connecting"
    : isThinking
      ? "thinking"
      : track
        ? "speaking"
        : "silent";

  const uniforms = useAuraUniforms({
    state,
    track,
    volume,
    color,
    accentColor,
    colorShift,
    hueSpread,
    speed,
    amplitude,
    glow,
    softness,
    fill,
    core,
    hollow,
    depth,
    smoothing,
    density,
    highlight,
    noHighlight,
    highlightColor,
    ditherPixelSize,
    ditherLevels,
    ditherAlphaLevels,
    ditherStrength,
    themeMode,
    className,
    rootRef,
  });

  const ribbonCount = Math.max(1, Math.round(iterations));
  const shaderSource = useMemo(
    () => makeShaderSource(ribbonCount, ditherMethod),
    [ribbonCount, ditherMethod],
  );

  return (
    <div
      ref={rootRef}
      data-slot="audio-visualizer-wave"
      data-state={state}
      style={{ width: size, height: size }}
      className={className}
    >
      <WaveShader
        key={`${ribbonCount}:${ditherMethod ?? "off"}`}
        fs={shaderSource}
        devicePixelRatio={
          ditherMethod
            ? 1
            : Math.min(globalThis.devicePixelRatio ?? 1, MAX_PIXEL_RATIO)
        }
        uniforms={uniforms}
        contextAttributes={AURA_CONTEXT_ATTRIBUTES}
        onError={(error) => {
          console.error("Shader error:", error);
        }}
        onWarning={(warning) => {
          console.warn("Shader warning:", warning);
        }}
        style={{ width: "100%", height: "100%" }}
      />
    </div>
  );
});

export interface AudioVisualizerWaveProps extends Omit<
  AudioVisualizerWaveViewProps,
  "track"
> {
  /** Which participant's audio to visualize. */
  participantType: ParticipantType;
}

/** Requires PipecatClientProvider; silence and speech follow the participant track. */
export function AudioVisualizerWave({
  participantType,
  ...props
}: AudioVisualizerWaveProps) {
  const track = usePipecatClientMediaTrack("audio", participantType);
  return <AudioVisualizerWaveView track={track} {...props} />;
}
