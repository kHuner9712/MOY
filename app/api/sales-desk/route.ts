/**
 * v1.1 Sales Desk API Routes
 * 今日作战台 API
 */

import { NextRequest, NextResponse } from 'next/server';
import { getServerAuthContext } from '@/lib/server-auth';
import { salesDeskService } from '@/services/sales-desk-service';
import { fail } from '@/lib/api-response';

export async function GET(request: NextRequest) {
  try {
    const auth = await getServerAuthContext();
    if (!auth) return fail("Unauthorized", 401);

    const queue = await salesDeskService.getSalesDeskQueue({
      orgId: auth.profile.org_id,
      ownerId: auth.profile.id
    });

    return NextResponse.json(queue);
  } catch (error) {
    console.error('Sales desk queue error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal server error' },
      { status: 500 }
    );
  }
}
