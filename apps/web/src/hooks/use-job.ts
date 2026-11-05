import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api, type Task } from '@/api/client';
export function useJob(scope?: string) {
  const cache = useQueryClient();
  const [job, setJob] = useState<Task | null>(null);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const pendingCreation = useRef(false);
  const generation = useRef(0);
  const isActive = !!job && ['queued', 'running'].includes(job.status);
  const storageKey = `mail:last-task:${scope || window.location.pathname}`;
  useEffect(() => {
    let active = true;
    const restoredGeneration = generation.current;
    let id: string | null = null;
    try {
      id = sessionStorage.getItem(storageKey);
    } catch {
      /* Continue without restoration. */
    }
    if (id)
      void api<Task>(`/tasks/${id}`)
        .then((task) => {
          if (active && restoredGeneration === generation.current) {
            setJob(task);
            setError(['failed', 'cancelled'].includes(task.status) ? task.error || '' : '');
          }
        })
        .catch(() => {
          try {
            sessionStorage.removeItem(storageKey);
          } catch {
            /* No storage. */
          }
        });
    return () => {
      active = false;
    };
  }, [storageKey]);
  useEffect(() => {
    if (job)
      try {
        sessionStorage.setItem(storageKey, job.id);
      } catch {
        /* No storage. */
      }
  }, [job?.id, storageKey]);
  useEffect(() => {
    if (!job || !isActive) return;
    const activeGeneration = generation.current;
    const stream = new EventSource(`/api/v1/tasks/${job.id}/events`);
    const refresh = async () => {
      try {
        const next = await api<Task>(`/tasks/${job.id}`);
        if (activeGeneration !== generation.current) return;
        setJob((current) => (current?.id === next.id ? next : current));
        setError(['failed', 'cancelled'].includes(next.status) ? next.error || '' : '');
        if (['failed', 'succeeded', 'cancelled'].includes(next.status)) {
          cache.invalidateQueries();
          stream.close();
        }
      } catch (e) {
        if (activeGeneration === generation.current) setError((e as Error).message);
      }
    };
    stream.addEventListener('stage', (event) => {
      if (activeGeneration !== generation.current) return;
      const data = JSON.parse((event as MessageEvent).data);
      setJob((current) => (current ? { ...current, phase: data.phase } : current));
      void refresh();
    });
    stream.addEventListener('result', () => void refresh());
    stream.addEventListener('error', () => void refresh());
    stream.addEventListener('done', () => void refresh());
    const polling = window.setInterval(refresh, 2500);
    return () => {
      stream.close();
      window.clearInterval(polling);
    };
  }, [job?.id, isActive, cache]);
  const run = useCallback(
    async (create: () => Promise<Task>) => {
      if (pendingCreation.current) return null;
      pendingCreation.current = true;
      generation.current += 1;
      setCreating(true);
      setError('');
      try {
        const result = await create();
        setJob(result);
        cache.invalidateQueries({ queryKey: ['tasks'] });
        if (['failed', 'succeeded', 'cancelled'].includes(result.status)) {
          cache.invalidateQueries();
          setError(['failed', 'cancelled'].includes(result.status) ? result.error || '' : '');
        }
        return result;
      } catch (e) {
        setError((e as Error).message);
        return null;
      } finally {
        pendingCreation.current = false;
        setCreating(false);
      }
    },
    [cache],
  );
  return {
    job,
    error,
    setError,
    run,
    busy: creating || isActive,
  };
}
