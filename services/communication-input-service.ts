import { createSupabaseBrowserClient } from "@/lib/supabase/client";
import { mapCommunicationInputRow } from "@/services/mappers";
import type {
  CaptureConfirmInput,
  CaptureConfirmResult,
  CaptureExtractInput,
  CaptureExtractResult,
  CommunicationInputItem
} from "@/types/communication";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const communicationInputService = {
  async listAll(): Promise<CommunicationInputItem[]> {
    const supabase = createSupabaseBrowserClient();
    const { data, error } = await supabase
      .from("communication_inputs")
      .select(
        "*, owner:profiles!communication_inputs_owner_id_fkey(id, display_name), customer:customers!communication_inputs_customer_id_fkey(id, company_name)"
      )
      .order("created_at", { ascending: false })
      .limit(80);

    if (error) throw new Error(error.message);
    return (data ?? []).map((row) => mapCommunicationInputRow(row as never));
  },

  async listByCustomerId(customerId: string): Promise<CommunicationInputItem[]> {
    const supabase = createSupabaseBrowserClient();
    const { data, error } = await supabase
      .from("communication_inputs")
      .select(
        "*, owner:profiles!communication_inputs_owner_id_fkey(id, display_name), customer:customers!communication_inputs_customer_id_fkey(id, company_name)"
      )
      .eq("customer_id", customerId)
      .order("created_at", { ascending: false })
      .limit(40);

    if (error) throw new Error(error.message);
    return (data ?? []).map((row) => mapCommunicationInputRow(row as never));
  },

  async extract(input: CaptureExtractInput): Promise<CaptureExtractResult> {
    const response = await fetch("/api/capture/extract", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });

    const payload = (await response.json()) as ApiPayload<CaptureExtractResult>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Capture extraction failed");
    }
    return payload.data;
  },

  async confirm(input: CaptureConfirmInput): Promise<CaptureConfirmResult> {
    const response = await fetch("/api/capture/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });

    const payload = (await response.json()) as ApiPayload<CaptureConfirmResult>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Capture confirm failed");
    }
    return payload.data;
  }
};
