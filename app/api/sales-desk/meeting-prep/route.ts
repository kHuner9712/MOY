/**
 * Meeting Prep API
 * 会前准备卡片
 */

import { NextRequest, NextResponse } from 'next/server';
import { getServerAuthContext } from '@/lib/server-auth';
import { salesDeskService } from '@/services/sales-desk-service';
import { fail } from '@/lib/api-response';

export async function POST(request: NextRequest) {
  try {
    const auth = await getServerAuthContext();
    if (!auth) return fail("Unauthorized", 401);

    const body = await request.json();
    const { customerId, opportunityId } = body;

    if (!customerId) {
      return fail("customerId is required", 400);
    }

    const result = await salesDeskService.getMeetingPrep({
      customerId,
      opportunityId,
      ownerId: auth.profile.id
    });

    return NextResponse.json(result);
  } catch (error) {
    console.error('Meeting prep error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal server error' },
      { status: 500 }
    );
  }
}
