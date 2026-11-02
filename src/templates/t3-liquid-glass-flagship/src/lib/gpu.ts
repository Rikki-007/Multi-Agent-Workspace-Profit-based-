// Decides how much WebGL this visitor gets, before any 3D code is downloaded.
//   none → static CSS poster only (reduced motion, Save-Data, no WebGL, software rendering)
//   low  → shader at low resolution, 30 fps (few cores, little memory, or touch devices)
//   high → shader at full budget
export type GpuTier = "none" | "low" | "high";

type NavigatorHints = Navigator & { deviceMemory?: number; connection?: { saveData?: boolean } };

export function detectGpuTier(): GpuTier {
  const nav = navigator as NavigatorHints;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || nav.connection?.saveData) return "none";

  const probe = document.createElement("canvas");
  const gl = probe.getContext("webgl2") ?? probe.getContext("webgl");
  if (!gl) return "none";
  const debug = gl.getExtension("WEBGL_debug_renderer_info");
  const renderer = debug ? String(gl.getParameter(debug.UNMASKED_RENDERER_WEBGL)) : "";
  gl.getExtension("WEBGL_lose_context")?.loseContext();
  // Software rasterisers draw shaders on the CPU: worse than no shader at all.
  if (/swiftshader|llvmpipe|softpipe|software|basic render/i.test(renderer)) return "none";

  const lowEnd =
    (nav.deviceMemory ?? 8) <= 4 ||
    (navigator.hardwareConcurrency ?? 8) <= 4 ||
    window.matchMedia("(pointer: coarse)").matches;
  return lowEnd ? "low" : "high";
}
