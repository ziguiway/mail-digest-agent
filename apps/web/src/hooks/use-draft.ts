import { useRef, useState } from 'react';

/** Store only explicit non-secret form fields; never pass credentials here. */
export function useDraft<T>(name: string, initial: T) {
  const key = `mail:draft:${name}`;
  const dirty = useRef(false);
  const [value, setValue] = useState<T>(() => {
    try {
      const saved = localStorage.getItem(key);
      if (saved) {
        dirty.current = true;
        return JSON.parse(saved) as T;
      }
    } catch {
      /* Storage may be unavailable in private browser contexts. */
    }
    return initial;
  });
  function update(next: T) {
    dirty.current = true;
    setValue(next);
    try {
      localStorage.setItem(key, JSON.stringify(next));
    } catch {
      /* Keep the live form usable. */
    }
  }
  function hydrate(next: T) {
    if (!dirty.current) setValue(next);
  }
  function clear() {
    dirty.current = false;
    try {
      localStorage.removeItem(key);
    } catch {
      /* No persisted draft to remove. */
    }
  }
  return { value, update, hydrate, clear };
}
