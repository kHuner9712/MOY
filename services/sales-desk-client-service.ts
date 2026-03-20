/**
 * v1.1 Sales Desk Client Service
 * 今日作战台前端调用封装
 */

import type {
  SalesDeskQueueResult,
  CommunicationReuseResult,
  MeetingPrepResult,
  CustomerTimelineResult
} from '@/types/sales-desk';

class SalesDeskClientService {
  private basePath = '/api/sales-desk';

  async getSalesDeskQueue(): Promise<SalesDeskQueueResult> {
    const response = await fetch(this.basePath, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' }
    });
    if (!response.ok) {
      throw new Error('Failed to load sales desk queue');
    }
    const data = await response.json();
    return data as SalesDeskQueueResult;
  }

  async generateCommunicationReuse(params: {
    communicationInputId: string;
    customerId?: string;
  }): Promise<CommunicationReuseResult> {
    const response = await fetch(`${this.basePath}/reuse`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params)
    });
    if (!response.ok) {
      throw new Error('Failed to generate communication reuse');
    }
    const data = await response.json();
    return data as CommunicationReuseResult;
  }

  async getMeetingPrep(params: {
    customerId: string;
    opportunityId?: string;
  }): Promise<MeetingPrepResult> {
    const response = await fetch(`${this.basePath}/meeting-prep`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params)
    });
    if (!response.ok) {
      throw new Error('Failed to load meeting prep');
    }
    const data = await response.json();
    return data as MeetingPrepResult;
  }

  async getCustomerTimeline(params: {
    customerId: string;
    limit?: number;
  }): Promise<CustomerTimelineResult> {
    const { customerId, limit = 50 } = params;
    const response = await fetch(`${this.basePath}/timeline/${customerId}?limit=${limit}`, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' }
    });
    if (!response.ok) {
      throw new Error('Failed to load customer timeline');
    }
    const data = await response.json();
    return data as CustomerTimelineResult;
  }
}

export const salesDeskClientService = new SalesDeskClientService();
