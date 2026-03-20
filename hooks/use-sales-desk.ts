/**
 * v1.1 Sales Desk Hook
 * 今日作战台 Hook
 */

import { useState, useEffect, useCallback } from 'react';
import { salesDeskClientService } from '@/services/sales-desk-client-service';
import type { SalesDeskQueueResult, SalesDeskQueueItem, CustomerTimelineItem } from '@/types/sales-desk';

export function useSalesDeskQueue() {
  const [queue, setQueue] = useState<SalesDeskQueueResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadQueue = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await salesDeskClientService.getSalesDeskQueue();
      setQueue(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load sales desk queue');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadQueue();
  }, [loadQueue]);

  const totalAlerts = queue
    ? queue.totalCounts.rhythmBreach +
      queue.totalCounts.highIntentSilent +
      queue.totalCounts.quoteWaiting
    : 0;

  const urgentItems = queue
    ? [
        ...queue.queues.rhythmBreach.slice(0, 3),
        ...queue.queues.highIntentSilent.slice(0, 3),
        ...queue.queues.quoteWaiting.slice(0, 3)
      ]
    : [];

  return {
    queue,
    loading,
    error,
    reload: loadQueue,
    totalAlerts,
    urgentItems
  };
}

export function useCustomerTimeline(customerId: string | null, limit = 30) {
  const [timeline, setTimeline] = useState<{ items: CustomerTimelineItem[]; loading: boolean; error: string | null }>({
    items: [],
    loading: false,
    error: null
  });

  useEffect(() => {
    if (!customerId) {
      setTimeline({ items: [], loading: false, error: null });
      return;
    }

    setTimeline(prev => ({ ...prev, loading: true, error: null }));

    const loadTimeline = async () => {
      try {
        const result = await salesDeskClientService.getCustomerTimeline({ customerId, limit });
        setTimeline({ items: result.items, loading: false, error: null });
      } catch (err) {
        setTimeline(prev => ({
          ...prev,
          loading: false,
          error: err instanceof Error ? err.message : 'Failed to load timeline'
        }));
      }
    };

    void loadTimeline();
  }, [customerId, limit]);

  return timeline;
}
