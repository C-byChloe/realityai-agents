import { useCallback, useEffect, useState } from "react";

export interface ServiceStatus {
  name: string;
  status: "healthy" | "unhealthy";
  detail: string;
}

export interface HealthData {
  status: "healthy" | "degraded";
  services: ServiceStatus[];
}

export function useHealthCheck(intervalMs = 15000) {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch("/api/health");
      if (!res.ok) throw new Error(`Health check failed: ${res.status}`);
      const data = await res.json();
      setHealth(data);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Polling effect: kick off an immediate fetch then poll on an interval.
    // setState happens asynchronously after each await, so this does not
    // cascade renders synchronously — the strict-mode rule is over-broad here.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void fetchHealth();
    const id = setInterval(() => {
      void fetchHealth();
    }, intervalMs);
    return () => clearInterval(id);
  }, [fetchHealth, intervalMs]);

  return { health, loading, error, refresh: fetchHealth };
}
