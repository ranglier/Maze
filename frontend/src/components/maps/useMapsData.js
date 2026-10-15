import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../../api.js';
import { bestMapFor } from './mapTree.js';

/**
 * Cartes interactives d'une campagne et arbre des lieux ; carte affichée : celle de l'adresse, sinon la plus proche
 * du lieu demandé (?place=), sinon la première.
 */
export function useMapsData({ cid, run, mapId, focusPlace }) {
  const [maps, setMaps] = useState(null);
  const [tree, setTree] = useState(null);         // { places, links, kinds }

  const loadMaps = useCallback(async () => {
    const d = await run(() => api.maps(cid));
    if (d) setMaps(d.maps);
    return d?.maps;
  }, [cid, run]);
  const loadTree = useCallback(async () => {
    const d = await run(() => api.places(cid));
    if (d) setTree(d);
  }, [cid, run]);
  useEffect(() => { loadMaps(); loadTree(); }, [loadMaps, loadTree]);

  const places = useMemo(() => tree?.places || [], [tree]);
  const byId = useMemo(() => new Map(places.map((p) => [p.id, p])), [places]);

  const currentId = useMemo(() => {
    if (!maps?.length) return null;
    if (mapId && maps.some((m) => m.id === mapId)) return mapId;
    if (focusPlace && tree) return bestMapFor(maps, byId, focusPlace)?.id || maps[0].id;
    return maps[0].id;
  }, [maps, mapId, focusPlace, tree, byId]);

  return { maps, setMaps, tree, loadMaps, places, byId, currentId };
}

/** Épingles de la carte affichée : mise à jour locale immédiate, puis enregistrement (carte relue en cas d'échec). */
export function useMapPins({ currentId, run, setDetail, setMaps, setPath, loadMaps }) {
  const reloadDetail = async () => {
    const d = await run(() => api.getMap(currentId));
    if (d) setDetail(d);
    loadMaps();
  };
  const savePin = async (placeId, x, y) => {
    const pin = { place_id: placeId, x, y };
    setDetail((d) => d && ({ ...d, pins: [...d.pins.filter((p) => p.place_id !== placeId), pin] }));
    setMaps((ms) => ms?.map((m) => (m.id === currentId && !m.pins.includes(placeId) ? { ...m, pins: [...m.pins, placeId] } : m)));
    const ok = await run(() => api.setPin(currentId, placeId, x, y));
    if (ok === undefined) reloadDetail();
  };
  const removePin = async (placeId) => {
    setDetail((d) => d && ({ ...d, pins: d.pins.filter((p) => p.place_id !== placeId) }));
    setMaps((ms) => ms?.map((m) => (m.id === currentId ? { ...m, pins: m.pins.filter((x) => x !== placeId) } : m)));
    setPath((p) => (p[0] === placeId ? [] : p));
    if (await run(() => api.deletePin(currentId, placeId)) === undefined) reloadDetail();
  };
  return { savePin, removePin, reloadDetail };
}
