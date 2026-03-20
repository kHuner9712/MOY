/**
 * Customer Timeline API
 * 客户统一时间线
 */

import { NextRequest, NextResponse } from 'next/server';
import { getServerAuthContext } from '@/lib/server-auth';
import { salesDeskService } from '@/services/sales-desk-service';
import { fail } from '@/lib/api-response';

export async function GET(
  request: NextRequest,
  { params }: { params: { customerId: string } }
) {
  try {
    const auth = await getServerAuthContext();
    if (!auth) return fail("Unauthorized", 401);

    const { customerId } = params;
    const searchParams = request.nextUrl.searchParams;
    const limit = parseInt(searchParams.get('limit') ?? '50', 10);

    if (!customerId) {
      return fail("customerId is required", 400);
    }

    const result = await salesDeskService.getCustomerTimeline({
      customerId,
      ownerId: auth.profile.id,
      limit
    });

    return NextResponse.json(result);
  } catch (error) {
    console.error('Customer timeline error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal server error' },
      { status: 500 }
    );
  }
}
