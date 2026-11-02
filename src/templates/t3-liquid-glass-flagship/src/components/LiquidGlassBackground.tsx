"use client";

import { useEffect, useRef, useState } from "react";
import { detectGpuTier } from "@/lib/gpu";

// Hero backdrop. The static poster (CSS) is always rendered underneath and is all that low-GPU,
// reduced-motion and no-JS visitors ever get. Capable devices download the shader in idle time and
// fade it in over the poster; if the watchdog gives up, it fades back out.
export function LiquidGlassBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [live, setLive] = useState(false);

  useEffect(() => {
    const tier = detectGpuTier();
    document.documentElement.dataset.gpuTier = tier; // visible in devtools and QA screenshots
    if (tier === "none") return;

    const canvas = canvasRef.current!;
    let stop: (() => void) | undefined;
    let cancelled = false;
    const run = async () => {
      const { startLiquidGlass } = await import("@/lib/liquid-scene");
      if (cancelled) return;
      stop = startLiquidGlass(canvas, tier, canvas.parentElement!, () => {
        setLive(false);
        stop?.();
        stop = undefined;
      });
      setLive(true);
    };
    // Safari lacks requestIdleCallback; a short timeout still keeps 3D off the first render.
    const idle = "requestIdleCallback" in window;
    const handle = idle ? requestIdleCallback(() => void run(), { timeout: 2500 }) : window.setTimeout(() => void run(), 400);
    return () => {
      cancelled = true;
      (idle ? cancelIdleCallback : window.clearTimeout)(handle);
      stop?.();
    };
  }, []);

  return (
    <div aria-hidden="true" className="absolute inset-0 -z-10">
      <div className="liquid-poster absolute inset-0" />
      <canvas ref={canvasRef} className={`absolute inset-0 h-full w-full transition-opacity duration-1000 ${live ? "opacity-100" : "opacity-0"}`} />
    </div>
  );
}
