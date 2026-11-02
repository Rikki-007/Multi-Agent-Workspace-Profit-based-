// The liquid glass shader. Loaded with a dynamic import() only after GPU tiering says yes, so
// Three.js never lands in the initial bundle. One full-screen triangle-pair, one fragment shader.
import { Color, Mesh, OrthographicCamera, PlaneGeometry, Scene, ShaderMaterial, Vector2, WebGLRenderer } from "three";
import type { GpuTier } from "./gpu";

const VERTEX = /* glsl */ `
void main() { gl_Position = vec4(position.xy, 0.0, 1.0); }`;

// A domain-warped height field acts as the glass surface: its normal refracts a soft colour field
// (with a little per-channel dispersion) and catches a specular highlight at grazing angles.
const FRAGMENT = /* glsl */ `
precision highp float;
uniform vec2 uRes;
uniform float uTime;
uniform vec2 uPointer;
uniform vec3 uBg, uA, uB, uSheen;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 4; i++) { v += a * noise(p); p *= 2.03; a *= 0.5; }
  return v;
}
float height(vec2 p) {
  float t = uTime * 0.05;
  vec2 q = vec2(fbm(p + t), fbm(p + vec2(5.2, 1.3) - t));
  return fbm(p + 1.6 * q + uPointer * 0.5);
}
vec3 field(vec2 uv) {
  vec3 c = mix(uA, uB, smoothstep(0.1, 0.9, uv.x + 0.25 * sin(uv.y * 3.0 + uTime * 0.03)));
  return mix(uBg, c, smoothstep(-0.1, 0.9, uv.y) * 0.75);
}
void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  vec2 p = uv * vec2(uRes.x / uRes.y, 1.0) * 2.2;
  float e = 0.02;
  float h = height(p);
  vec2 slope = vec2(height(p + vec2(e, 0.0)) - h, height(p + vec2(0.0, e)) - h) / e;
  vec3 n = normalize(vec3(-slope * 0.35, 1.0));
  vec2 r = n.xy * 0.07;
  vec3 col = vec3(field(uv + r).r, field(uv + r * 1.08).g, field(uv + r * 1.16).b);
  float fresnel = pow(1.0 - n.z, 2.0);
  float spec = pow(max(dot(reflect(-normalize(vec3(-0.4, 0.6, 0.7)), n), vec3(0.0, 0.0, 1.0)), 0.0), 48.0);
  col += uSheen * fresnel * 0.5 + spec * 0.8;
  col = mix(uBg, col, smoothstep(0.0, 0.45, uv.y));   // settle into the page below the hero
  gl_FragColor = vec4(col, 1.0);
}`;

const QUALITY = {
  high: { scale: 0.6, frameSkip: 1 },
  low: { scale: 0.35, frameSkip: 2 }, // every other frame ≈ 30 fps
} as const;

function cssColor(name: string): Color {
  return new Color(getComputedStyle(document.documentElement).getPropertyValue(name).trim());
}

/**
 * Renders into `canvas` while `activeWhile` is on screen and the tab is visible. A frame-time
 * watchdog steps high → low → `onGiveUp()` (caller switches to the static poster) if the
 * device can't keep up. Returns a stop function.
 */
export function startLiquidGlass(canvas: HTMLCanvasElement, tier: Exclude<GpuTier, "none">, activeWhile: Element, onGiveUp: () => void) {
  let quality: keyof typeof QUALITY = tier;
  const renderer = new WebGLRenderer({ canvas, antialias: false, alpha: false, powerPreference: tier === "high" ? "high-performance" : "low-power" });
  const scene = new Scene();
  const camera = new OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const uniforms = {
    uRes: { value: new Vector2() },
    uTime: { value: 0 },
    uPointer: { value: new Vector2() },
    uBg: { value: cssColor("--bg") },
    uA: { value: cssColor("--glow-a") },
    uB: { value: cssColor("--glow-b") },
    uSheen: { value: cssColor("--accent") },
  };
  const material = new ShaderMaterial({ vertexShader: VERTEX, fragmentShader: FRAGMENT, uniforms });
  const geometry = new PlaneGeometry(2, 2);
  scene.add(new Mesh(geometry, material));

  const resize = () => {
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5) * QUALITY[quality].scale);
    renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
    renderer.getDrawingBufferSize(uniforms.uRes.value);
  };
  resize();
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(canvas);

  const target = new Vector2();
  const onPointer = (e: PointerEvent) => target.set(e.clientX / window.innerWidth - 0.5, 0.5 - e.clientY / window.innerHeight);
  window.addEventListener("pointermove", onPointer, { passive: true });

  let visible = true;
  const io = new IntersectionObserver(([entry]) => (visible = entry.isIntersecting));
  io.observe(activeWhile);

  let raf = 0;
  let frame = 0;
  let last = performance.now();
  let slowFrames = 0;
  let sampled = 0;
  const loop = (now: number) => {
    raf = requestAnimationFrame(loop);
    const dt = now - last;
    last = now;
    if (!visible || document.hidden || frame++ % QUALITY[quality].frameSkip !== 0) return;

    // Watchdog over the first ~2s of rendering: mostly-slow frames means this GPU can't keep up.
    if (sampled < 120) {
      sampled++;
      if (dt > 1000 / 40) slowFrames++;
      if (sampled === 120 && slowFrames > 60) {
        if (quality === "high") {
          quality = "low";
          resize();
          sampled = slowFrames = 0;
        } else {
          onGiveUp();
          return;
        }
      }
    }
    uniforms.uTime.value = now / 1000;
    uniforms.uPointer.value.lerp(target, 0.04);
    renderer.render(scene, camera);
  };
  raf = requestAnimationFrame(loop);

  return () => {
    cancelAnimationFrame(raf);
    io.disconnect();
    resizeObserver.disconnect();
    window.removeEventListener("pointermove", onPointer);
    geometry.dispose();
    material.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
  };
}
