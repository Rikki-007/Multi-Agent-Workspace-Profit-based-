"use client";

import { useEffect } from "react";
import { initReveals, initSmoothScroll, ScrollTrigger } from "@/lib/motion";

// Mounted once in the layout: starts Lenis and the page-wide reveals after hydration.
export function MotionRoot() {
  useEffect(() => {
    const stopScroll = initSmoothScroll();
    const stopReveals = initReveals();
    document.fonts.ready.then(() => ScrollTrigger.refresh()); // web fonts change heights; re-measure
    return () => {
      stopReveals();
      stopScroll();
    };
  }, []);
  return null;
}
