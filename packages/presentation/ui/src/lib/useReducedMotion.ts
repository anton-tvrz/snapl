import { useEffect, useState } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

function prefersReduced(): boolean {
  return typeof matchMedia === "function" && matchMedia(QUERY).matches;
}

/** True when the viewer asked the system for less motion (spec 011 FR-043). */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReduced);

  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const media = matchMedia(QUERY);
    const onChange = () => setReduced(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  return reduced;
}
