-- MOY phase-16.5 release-candidate audit fixes
-- P0-1: prevent suspended/removed membership from falling back to profile role
CREATE OR REPLACE FUNCTION public.current_user_membership_role()
RETURNS public.org_member_role
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT CASE
    WHEN EXISTS (
      SELECT 1
      FROM public.org_memberships m
      WHERE m.org_id = public.current_user_org_id()
        AND m.user_id = auth.uid()
    ) THEN (
      SELECT m.role
      FROM public.org_memberships m
      WHERE m.org_id = public.current_user_org_id()
        AND m.user_id = auth.uid()
        AND m.seat_status = 'active'
      LIMIT 1
    )
    ELSE (
      CASE
        WHEN public.current_user_role() = 'manager' THEN 'manager'::public.org_member_role
        ELSE 'sales'::public.org_member_role
      END
    )
  END
$$;

-- P1: improve public lead anti-abuse query performance (fingerprint in payload_snapshot)
CREATE INDEX IF NOT EXISTS idx_inbound_leads_request_fingerprint
ON public.inbound_leads ((payload_snapshot->>'request_fingerprint'));
