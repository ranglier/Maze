import { useCallback, useEffect, useRef, useState } from 'react';
import { clamp } from './mapTree.js';
import { panelBeside } from './constants.js';

/**
 * Vue d'une carte interactive : taille du cadre, déplacement et zoom ({ x, y, k } : écran = x + k × pixel de
 * l'image), bornes (la carte ne sort jamais entièrement de l'écran), vue d'ensemble, glissements animés, recentrage
 * demandé (`center`), zoom à la molette.
 */
export default function useMapViewport({ ref, W, H, center }) {
  const [size, setSize] = useState(null);
  const [view, setView] = useState(null);           // { x, y, k } : écran = x + k × pixel de l'image
  const anim = useRef(0);

  useEffect(() => {
    const el = ref.current;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const fitK = size ? Math.min(size.w / W, size.h / H) : 1;
  const kMin = fitK * 0.6;
  const kMax = Math.max(2, fitK * 2);
  const limit = useCallback((v) => {
    if (!size) return v;
    const k = clamp(v.k, kMin, kMax);
    const m = 80;   // la carte ne sort jamais entièrement de l'écran
    return { k, x: clamp(v.x, m - W * k, size.w - m), y: clamp(v.y, m - H * k, size.h - m) };
  }, [size, kMin, kMax, W, H]);

  const fit = useCallback(() => {
    if (!size) return;
    const k = fitK * 0.96;
    setView({ k, x: (size.w - W * k) / 2, y: (size.h - H * k) / 2 });
  }, [size, fitK, W, H]);
  useEffect(() => { if (size && !view) fit(); }, [size, view, fit]);
  useEffect(() => {   // la fenêtre change de taille : la carte reste dans le cadre
    if (size) setView((v) => (v ? limit(v) : v));
  }, [size, limit]);

  /** Glissement en douceur vers une vue (recentrage, anneau à rendre visible). */
  const animateTo = useCallback((target) => {
    cancelAnimationFrame(anim.current);
    const start = performance.now();
    let origin = null;
    const step = (now) => {
      const t = Math.min(1, (now - start) / 280);
      const e = 1 - (1 - t) ** 3;
      setView((v) => {
        origin = origin || v;
        return { k: origin.k + (target.k - origin.k) * e, x: origin.x + (target.x - origin.x) * e, y: origin.y + (target.y - origin.y) * e };
      });
      if (t < 1) anim.current = requestAnimationFrame(step);
    };
    anim.current = requestAnimationFrame(step);
  }, []);
  useEffect(() => () => cancelAnimationFrame(anim.current), []);

  // Recentrage demandé (lieu choisi dans la liste ou arrivée depuis une fiche).
  useEffect(() => {
    if (!center || !size || !view) return;
    const k = Math.max(view.k, Math.min(kMax, fitK * 1.6));
    // Grand écran : fiche du lieu à droite ; téléphone : en feuille basse, le lieu se place dans le haut.
    const side = panelBeside();
    const free = side ? size.w - 354 : size.w;
    const freeH = side ? size.h : size.h * 0.5;
    animateTo(limit({ k, x: free / 2 - center.x * W * k, y: freeH / 2 - center.y * H * k }));
  }, [center, size && view !== null]); // eslint-disable-line react-hooks/exhaustive-deps

  const zoomAt = useCallback((factor, sx, sy) => {
    setView((v) => {
      if (!v) return v;
      const k = clamp(v.k * factor, kMin, kMax);
      const f = k / v.k;
      return limit({ k, x: sx - (sx - v.x) * f, y: sy - (sy - v.y) * f });
    });
  }, [kMin, kMax, limit]);

  useEffect(() => {
    const el = ref.current;
    const onWheel = (e) => {
      e.preventDefault();
      cancelAnimationFrame(anim.current);
      const r = el.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0018)), e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [zoomAt]); // eslint-disable-line react-hooks/exhaustive-deps

  return { size, view, setView, limit, fit, animateTo, zoomAt, anim };
}
