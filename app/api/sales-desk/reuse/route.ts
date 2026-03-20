/**
 * Communication Reuse API
 * 一次沟通，五处复用
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
    const { communicationInputId, customerId } = body;

    if (!communicationInputId) {
      return fail("communicationInputId is required", 400);
    }

    const result = await salesDeskService.generateCommunicationReuse({
      communicationInputId,
      customerId,
      ownerId: auth.profile.id
    });

    return NextResponse.json(result);
  } catch (error) {
    console.error('Communication reuse error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Internal server error' },
      { status: 500 }
    );
  }
}
