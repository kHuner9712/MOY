export type Json = string | number | boolean | null | { [key: string]: Json | undefined } | Json[];

export interface Database {
  public: {
    Tables: {
      organizations: {
        Row: {
          id: string;
          name: string;
          slug: string;
          created_at: string;
        };
        Insert: {
          id?: string;
          name: string;
          slug: string;
          created_at?: string;
        };
        Update: {
          id?: string;
          name?: string;
          slug?: string;
          created_at?: string;
        };
        Relationships: [];
      };
      profiles: {
        Row: {
          id: string;
          org_id: string;
          display_name: string;
          role: Database["public"]["Enums"]["app_role"];
          is_active: boolean;
          title: string | null;
          team_name: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id: string;
          org_id: string;
          display_name: string;
          role?: Database["public"]["Enums"]["app_role"];
          is_active?: boolean;
          title?: string | null;
          team_name?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          display_name?: string;
          role?: Database["public"]["Enums"]["app_role"];
          is_active?: boolean;
          title?: string | null;
          team_name?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      customers: {
        Row: {
          id: string;
          org_id: string;
          owner_id: string;
          name: string;
          company_name: string;
          contact_name: string;
          phone: string | null;
          email: string | null;
          source_channel: string | null;
          current_stage: Database["public"]["Enums"]["customer_stage"];
          last_followup_at: string | null;
          next_followup_at: string | null;
          win_probability: number;
          risk_level: Database["public"]["Enums"]["risk_level"];
          tags: string[];
          ai_summary: string | null;
          ai_suggestion: string | null;
          ai_risk_judgement: string | null;
          has_decision_maker: boolean;
          created_by: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          owner_id: string;
          name: string;
          company_name: string;
          contact_name: string;
          phone?: string | null;
          email?: string | null;
          source_channel?: string | null;
          current_stage?: Database["public"]["Enums"]["customer_stage"];
          last_followup_at?: string | null;
          next_followup_at?: string | null;
          win_probability?: number;
          risk_level?: Database["public"]["Enums"]["risk_level"];
          tags?: string[];
          ai_summary?: string | null;
          ai_suggestion?: string | null;
          ai_risk_judgement?: string | null;
          has_decision_maker?: boolean;
          created_by: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          owner_id?: string;
          name?: string;
          company_name?: string;
          contact_name?: string;
          phone?: string | null;
          email?: string | null;
          source_channel?: string | null;
          current_stage?: Database["public"]["Enums"]["customer_stage"];
          last_followup_at?: string | null;
          next_followup_at?: string | null;
          win_probability?: number;
          risk_level?: Database["public"]["Enums"]["risk_level"];
          tags?: string[];
          ai_summary?: string | null;
          ai_suggestion?: string | null;
          ai_risk_judgement?: string | null;
          has_decision_maker?: boolean;
          created_by?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      followups: {
        Row: {
          id: string;
          org_id: string;
          customer_id: string;
          owner_id: string;
          communication_type: Database["public"]["Enums"]["communication_type"];
          summary: string;
          customer_needs: string;
          objections: string | null;
          next_step: string;
          next_followup_at: string | null;
          needs_ai_analysis: boolean;
          source_input_id: string | null;
          draft_status: Database["public"]["Enums"]["followup_draft_status"];
          ai_summary: string | null;
          ai_suggestion: string | null;
          ai_risk_level: Database["public"]["Enums"]["risk_level"] | null;
          ai_leak_risk: boolean | null;
          created_by: string;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          customer_id: string;
          owner_id: string;
          communication_type: Database["public"]["Enums"]["communication_type"];
          summary: string;
          customer_needs: string;
          objections?: string | null;
          next_step: string;
          next_followup_at?: string | null;
          needs_ai_analysis?: boolean;
          source_input_id?: string | null;
          draft_status?: Database["public"]["Enums"]["followup_draft_status"];
          ai_summary?: string | null;
          ai_suggestion?: string | null;
          ai_risk_level?: Database["public"]["Enums"]["risk_level"] | null;
          ai_leak_risk?: boolean | null;
          created_by: string;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          customer_id?: string;
          owner_id?: string;
          communication_type?: Database["public"]["Enums"]["communication_type"];
          summary?: string;
          customer_needs?: string;
          objections?: string | null;
          next_step?: string;
          next_followup_at?: string | null;
          needs_ai_analysis?: boolean;
          source_input_id?: string | null;
          draft_status?: Database["public"]["Enums"]["followup_draft_status"];
          ai_summary?: string | null;
          ai_suggestion?: string | null;
          ai_risk_level?: Database["public"]["Enums"]["risk_level"] | null;
          ai_leak_risk?: boolean | null;
          created_by?: string;
          created_at?: string;
        };
        Relationships: [];
      };
      communication_inputs: {
        Row: {
          id: string;
          org_id: string;
          customer_id: string | null;
          owner_id: string;
          source_type: Database["public"]["Enums"]["communication_source_type"];
          title: string | null;
          raw_content: string;
          input_language: string;
          occurred_at: string;
          extracted_followup_id: string | null;
          extraction_status: Database["public"]["Enums"]["extraction_status"];
          extraction_error: string | null;
          extracted_data: Json;
          created_by: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          customer_id?: string | null;
          owner_id: string;
          source_type: Database["public"]["Enums"]["communication_source_type"];
          title?: string | null;
          raw_content: string;
          input_language?: string;
          occurred_at?: string;
          extracted_followup_id?: string | null;
          extraction_status?: Database["public"]["Enums"]["extraction_status"];
          extraction_error?: string | null;
          extracted_data?: Json;
          created_by: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          customer_id?: string | null;
          owner_id?: string;
          source_type?: Database["public"]["Enums"]["communication_source_type"];
          title?: string | null;
          raw_content?: string;
          input_language?: string;
          occurred_at?: string;
          extracted_followup_id?: string | null;
          extraction_status?: Database["public"]["Enums"]["extraction_status"];
          extraction_error?: string | null;
          extracted_data?: Json;
          created_by?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      generated_reports: {
        Row: {
          id: string;
          org_id: string;
          report_type: Database["public"]["Enums"]["report_type"];
          target_user_id: string | null;
          scope_type: Database["public"]["Enums"]["report_scope_type"];
          period_start: string;
          period_end: string;
          status: Database["public"]["Enums"]["report_status"];
          title: string | null;
          summary: string | null;
          content_markdown: string | null;
          metrics_snapshot: Json;
          source_snapshot: Json;
          generated_by: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          report_type: Database["public"]["Enums"]["report_type"];
          target_user_id?: string | null;
          scope_type?: Database["public"]["Enums"]["report_scope_type"];
          period_start: string;
          period_end: string;
          status?: Database["public"]["Enums"]["report_status"];
          title?: string | null;
          summary?: string | null;
          content_markdown?: string | null;
          metrics_snapshot?: Json;
          source_snapshot?: Json;
          generated_by: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          report_type?: Database["public"]["Enums"]["report_type"];
          target_user_id?: string | null;
          scope_type?: Database["public"]["Enums"]["report_scope_type"];
          period_start?: string;
          period_end?: string;
          status?: Database["public"]["Enums"]["report_status"];
          title?: string | null;
          summary?: string | null;
          content_markdown?: string | null;
          metrics_snapshot?: Json;
          source_snapshot?: Json;
          generated_by?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      opportunities: {
        Row: {
          id: string;
          org_id: string;
          customer_id: string;
          owner_id: string;
          title: string;
          amount: number;
          stage: Database["public"]["Enums"]["opportunity_stage"];
          risk_level: Database["public"]["Enums"]["risk_level"];
          expected_close_date: string | null;
          last_activity_at: string | null;
          created_by: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          customer_id: string;
          owner_id: string;
          title: string;
          amount?: number;
          stage?: Database["public"]["Enums"]["opportunity_stage"];
          risk_level?: Database["public"]["Enums"]["risk_level"];
          expected_close_date?: string | null;
          last_activity_at?: string | null;
          created_by: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          customer_id?: string;
          owner_id?: string;
          title?: string;
          amount?: number;
          stage?: Database["public"]["Enums"]["opportunity_stage"];
          risk_level?: Database["public"]["Enums"]["risk_level"];
          expected_close_date?: string | null;
          last_activity_at?: string | null;
          created_by?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      alerts: {
        Row: {
          id: string;
          org_id: string;
          customer_id: string | null;
          opportunity_id: string | null;
          owner_id: string | null;
          rule_type: Database["public"]["Enums"]["alert_rule_type"];
          source: Database["public"]["Enums"]["alert_source"];
          severity: Database["public"]["Enums"]["alert_severity"];
          status: Database["public"]["Enums"]["alert_status"];
          title: string;
          description: string | null;
          evidence: Json;
          suggested_owner_action: string[];
          ai_run_id: string | null;
          due_at: string | null;
          resolved_at: string | null;
          last_triggered_at: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          customer_id?: string | null;
          opportunity_id?: string | null;
          owner_id?: string | null;
          rule_type: Database["public"]["Enums"]["alert_rule_type"];
          source?: Database["public"]["Enums"]["alert_source"];
          severity?: Database["public"]["Enums"]["alert_severity"];
          status?: Database["public"]["Enums"]["alert_status"];
          title: string;
          description?: string | null;
          evidence?: Json;
          suggested_owner_action?: string[];
          ai_run_id?: string | null;
          due_at?: string | null;
          resolved_at?: string | null;
          last_triggered_at?: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          customer_id?: string | null;
          opportunity_id?: string | null;
          owner_id?: string | null;
          rule_type?: Database["public"]["Enums"]["alert_rule_type"];
          source?: Database["public"]["Enums"]["alert_source"];
          severity?: Database["public"]["Enums"]["alert_severity"];
          status?: Database["public"]["Enums"]["alert_status"];
          title?: string;
          description?: string | null;
          evidence?: Json;
          suggested_owner_action?: string[];
          ai_run_id?: string | null;
          due_at?: string | null;
          resolved_at?: string | null;
          last_triggered_at?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      ai_prompt_versions: {
        Row: {
          id: string;
          org_id: string;
          name: string;
          version: string;
          scenario: Database["public"]["Enums"]["ai_scenario"];
          provider_scope: Database["public"]["Enums"]["ai_provider_scope"];
          system_prompt: string;
          developer_prompt: string;
          output_schema: Json;
          is_active: boolean;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          name: string;
          version: string;
          scenario: Database["public"]["Enums"]["ai_scenario"];
          provider_scope?: Database["public"]["Enums"]["ai_provider_scope"];
          system_prompt: string;
          developer_prompt: string;
          output_schema?: Json;
          is_active?: boolean;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          name?: string;
          version?: string;
          scenario?: Database["public"]["Enums"]["ai_scenario"];
          provider_scope?: Database["public"]["Enums"]["ai_provider_scope"];
          system_prompt?: string;
          developer_prompt?: string;
          output_schema?: Json;
          is_active?: boolean;
          created_at?: string;
        };
        Relationships: [];
      };
      ai_runs: {
        Row: {
          id: string;
          org_id: string;
          customer_id: string | null;
          followup_id: string | null;
          triggered_by_user_id: string | null;
          trigger_source: Database["public"]["Enums"]["ai_trigger_source"];
          scenario: Database["public"]["Enums"]["ai_scenario"];
          provider: Database["public"]["Enums"]["ai_provider"];
          model: string;
          prompt_version: string;
          status: Database["public"]["Enums"]["ai_run_status"];
          input_snapshot: Json | null;
          output_snapshot: Json | null;
          parsed_result: Json | null;
          error_message: string | null;
          latency_ms: number | null;
          result_source: Database["public"]["Enums"]["ai_result_source"];
          fallback_reason: string | null;
          started_at: string | null;
          completed_at: string | null;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          customer_id?: string | null;
          followup_id?: string | null;
          triggered_by_user_id?: string | null;
          trigger_source?: Database["public"]["Enums"]["ai_trigger_source"];
          scenario: Database["public"]["Enums"]["ai_scenario"];
          provider?: Database["public"]["Enums"]["ai_provider"];
          model: string;
          prompt_version: string;
          status?: Database["public"]["Enums"]["ai_run_status"];
          input_snapshot?: Json | null;
          output_snapshot?: Json | null;
          parsed_result?: Json | null;
          error_message?: string | null;
          latency_ms?: number | null;
          result_source?: Database["public"]["Enums"]["ai_result_source"];
          fallback_reason?: string | null;
          started_at?: string | null;
          completed_at?: string | null;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          customer_id?: string | null;
          followup_id?: string | null;
          triggered_by_user_id?: string | null;
          trigger_source?: Database["public"]["Enums"]["ai_trigger_source"];
          scenario?: Database["public"]["Enums"]["ai_scenario"];
          provider?: Database["public"]["Enums"]["ai_provider"];
          model?: string;
          prompt_version?: string;
          status?: Database["public"]["Enums"]["ai_run_status"];
          input_snapshot?: Json | null;
          output_snapshot?: Json | null;
          parsed_result?: Json | null;
          error_message?: string | null;
          latency_ms?: number | null;
          result_source?: Database["public"]["Enums"]["ai_result_source"];
          fallback_reason?: string | null;
          started_at?: string | null;
          completed_at?: string | null;
          created_at?: string;
        };
        Relationships: [];
      };
      alert_rule_runs: {
        Row: {
          id: string;
          org_id: string;
          rule_name: string;
          status: Database["public"]["Enums"]["alert_rule_run_status"];
          scanned_count: number;
          created_alert_count: number;
          deduped_alert_count: number;
          resolved_alert_count: number;
          started_at: string;
          completed_at: string | null;
          error_message: string | null;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          rule_name: string;
          status?: Database["public"]["Enums"]["alert_rule_run_status"];
          scanned_count?: number;
          created_alert_count?: number;
          deduped_alert_count?: number;
          resolved_alert_count?: number;
          started_at?: string;
          completed_at?: string | null;
          error_message?: string | null;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          rule_name?: string;
          status?: Database["public"]["Enums"]["alert_rule_run_status"];
          scanned_count?: number;
          created_alert_count?: number;
          deduped_alert_count?: number;
          resolved_alert_count?: number;
          started_at?: string;
          completed_at?: string | null;
          error_message?: string | null;
          created_at?: string;
        };
        Relationships: [];
      };
      ai_feedback: {
        Row: {
          id: string;
          org_id: string;
          ai_run_id: string;
          user_id: string;
          rating: Database["public"]["Enums"]["ai_feedback_rating"];
          feedback_text: string | null;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          ai_run_id: string;
          user_id: string;
          rating: Database["public"]["Enums"]["ai_feedback_rating"];
          feedback_text?: string | null;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          ai_run_id?: string;
          user_id?: string;
          rating?: Database["public"]["Enums"]["ai_feedback_rating"];
          feedback_text?: string | null;
          created_at?: string;
        };
        Relationships: [];
      };
      user_memory_profiles: {
        Row: {
          id: string;
          org_id: string;
          user_id: string;
          memory_version: string;
          summary: string;
          preferred_customer_types: Json;
          preferred_communication_styles: Json;
          common_objections: Json;
          effective_tactics: Json;
          common_followup_rhythm: Json;
          quoting_style_notes: Json;
          risk_blind_spots: Json;
          manager_coaching_focus: Json;
          confidence_score: number;
          source_window_days: number;
          last_compiled_at: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id: string;
          memory_version?: string;
          summary?: string;
          preferred_customer_types?: Json;
          preferred_communication_styles?: Json;
          common_objections?: Json;
          effective_tactics?: Json;
          common_followup_rhythm?: Json;
          quoting_style_notes?: Json;
          risk_blind_spots?: Json;
          manager_coaching_focus?: Json;
          confidence_score?: number;
          source_window_days?: number;
          last_compiled_at?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string;
          memory_version?: string;
          summary?: string;
          preferred_customer_types?: Json;
          preferred_communication_styles?: Json;
          common_objections?: Json;
          effective_tactics?: Json;
          common_followup_rhythm?: Json;
          quoting_style_notes?: Json;
          risk_blind_spots?: Json;
          manager_coaching_focus?: Json;
          confidence_score?: number;
          source_window_days?: number;
          last_compiled_at?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      user_memory_items: {
        Row: {
          id: string;
          org_id: string;
          user_id: string;
          memory_type: Database["public"]["Enums"]["memory_item_type"];
          title: string;
          description: string;
          evidence_snapshot: Json;
          confidence_score: number;
          source_count: number;
          status: Database["public"]["Enums"]["memory_item_status"];
          created_by_system: boolean;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id: string;
          memory_type: Database["public"]["Enums"]["memory_item_type"];
          title: string;
          description: string;
          evidence_snapshot?: Json;
          confidence_score?: number;
          source_count?: number;
          status?: Database["public"]["Enums"]["memory_item_status"];
          created_by_system?: boolean;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string;
          memory_type?: Database["public"]["Enums"]["memory_item_type"];
          title?: string;
          description?: string;
          evidence_snapshot?: Json;
          confidence_score?: number;
          source_count?: number;
          status?: Database["public"]["Enums"]["memory_item_status"];
          created_by_system?: boolean;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      behavior_quality_snapshots: {
        Row: {
          id: string;
          org_id: string;
          user_id: string;
          snapshot_date: string;
          period_type: Database["public"]["Enums"]["quality_period_type"];
          assigned_customer_count: number;
          active_customer_count: number;
          followup_count: number;
          on_time_followup_rate: number;
          overdue_followup_rate: number;
          followup_completeness_score: number;
          stage_progression_score: number;
          risk_response_score: number;
          high_value_focus_score: number;
          activity_quality_score: number;
          shallow_activity_ratio: number;
          stalled_customer_count: number;
          high_risk_unhandled_count: number;
          summary: string;
          metrics_snapshot: Json;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id: string;
          snapshot_date: string;
          period_type: Database["public"]["Enums"]["quality_period_type"];
          assigned_customer_count?: number;
          active_customer_count?: number;
          followup_count?: number;
          on_time_followup_rate?: number;
          overdue_followup_rate?: number;
          followup_completeness_score?: number;
          stage_progression_score?: number;
          risk_response_score?: number;
          high_value_focus_score?: number;
          activity_quality_score?: number;
          shallow_activity_ratio?: number;
          stalled_customer_count?: number;
          high_risk_unhandled_count?: number;
          summary?: string;
          metrics_snapshot?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string;
          snapshot_date?: string;
          period_type?: Database["public"]["Enums"]["quality_period_type"];
          assigned_customer_count?: number;
          active_customer_count?: number;
          followup_count?: number;
          on_time_followup_rate?: number;
          overdue_followup_rate?: number;
          followup_completeness_score?: number;
          stage_progression_score?: number;
          risk_response_score?: number;
          high_value_focus_score?: number;
          activity_quality_score?: number;
          shallow_activity_ratio?: number;
          stalled_customer_count?: number;
          high_risk_unhandled_count?: number;
          summary?: string;
          metrics_snapshot?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      coaching_reports: {
        Row: {
          id: string;
          org_id: string;
          report_scope: Database["public"]["Enums"]["coaching_report_scope"];
          target_user_id: string | null;
          period_start: string;
          period_end: string;
          status: Database["public"]["Enums"]["coaching_report_status"];
          title: string | null;
          executive_summary: string | null;
          strengths: Json;
          weaknesses: Json;
          coaching_actions: Json;
          replicable_patterns: Json;
          risk_warnings: Json;
          content_markdown: string | null;
          source_snapshot: Json;
          generated_by: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          report_scope?: Database["public"]["Enums"]["coaching_report_scope"];
          target_user_id?: string | null;
          period_start: string;
          period_end: string;
          status?: Database["public"]["Enums"]["coaching_report_status"];
          title?: string | null;
          executive_summary?: string | null;
          strengths?: Json;
          weaknesses?: Json;
          coaching_actions?: Json;
          replicable_patterns?: Json;
          risk_warnings?: Json;
          content_markdown?: string | null;
          source_snapshot?: Json;
          generated_by: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          report_scope?: Database["public"]["Enums"]["coaching_report_scope"];
          target_user_id?: string | null;
          period_start?: string;
          period_end?: string;
          status?: Database["public"]["Enums"]["coaching_report_status"];
          title?: string | null;
          executive_summary?: string | null;
          strengths?: Json;
          weaknesses?: Json;
          coaching_actions?: Json;
          replicable_patterns?: Json;
          risk_warnings?: Json;
          content_markdown?: string | null;
          source_snapshot?: Json;
          generated_by?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      memory_feedback: {
        Row: {
          id: string;
          org_id: string;
          user_id: string;
          memory_item_id: string;
          feedback_type: Database["public"]["Enums"]["memory_feedback_type"];
          feedback_text: string | null;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id: string;
          memory_item_id: string;
          feedback_type: Database["public"]["Enums"]["memory_feedback_type"];
          feedback_text?: string | null;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string;
          memory_item_id?: string;
          feedback_type?: Database["public"]["Enums"]["memory_feedback_type"];
          feedback_text?: string | null;
          created_at?: string;
        };
        Relationships: [];
      };
      manager_desk_intervention_records: {
        Row: {
          id: string;
          org_id: string;
          intervention_key: string;
          resolution_status: string;
          resolved_by: string | null;
          resolved_at: string;
          outcome_note: string | null;
          customer_id: string | null;
          opportunity_id: string | null;
          deal_room_id: string | null;
          work_item_id: string | null;
          risk_item_id: string | null;
          risk_reason: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          intervention_key: string;
          resolution_status?: string;
          resolved_by?: string | null;
          resolved_at?: string;
          outcome_note?: string | null;
          customer_id?: string | null;
          opportunity_id?: string | null;
          deal_room_id?: string | null;
          work_item_id?: string | null;
          risk_item_id?: string | null;
          risk_reason?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          intervention_key?: string;
          resolution_status?: string;
          resolved_by?: string | null;
          resolved_at?: string;
          outcome_note?: string | null;
          customer_id?: string | null;
          opportunity_id?: string | null;
          deal_room_id?: string | null;
          work_item_id?: string | null;
          risk_item_id?: string | null;
          risk_reason?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      work_items: {
        Row: {
          id: string;
          org_id: string;
          owner_id: string;
          customer_id: string | null;
          opportunity_id: string | null;
          source_type: Database["public"]["Enums"]["work_item_source_type"];
          work_type: Database["public"]["Enums"]["work_item_type"];
          title: string;
          description: string;
          rationale: string;
          priority_score: number;
          priority_band: Database["public"]["Enums"]["work_priority_band"];
          status: Database["public"]["Enums"]["work_item_status"];
          scheduled_for: string | null;
          due_at: string | null;
          completed_at: string | null;
          snoozed_until: string | null;
          source_ref_type: string | null;
          source_ref_id: string | null;
          ai_generated: boolean;
          ai_run_id: string | null;
          created_by: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          owner_id: string;
          customer_id?: string | null;
          opportunity_id?: string | null;
          source_type: Database["public"]["Enums"]["work_item_source_type"];
          work_type: Database["public"]["Enums"]["work_item_type"];
          title: string;
          description?: string;
          rationale?: string;
          priority_score?: number;
          priority_band?: Database["public"]["Enums"]["work_priority_band"];
          status?: Database["public"]["Enums"]["work_item_status"];
          scheduled_for?: string | null;
          due_at?: string | null;
          completed_at?: string | null;
          snoozed_until?: string | null;
          source_ref_type?: string | null;
          source_ref_id?: string | null;
          ai_generated?: boolean;
          ai_run_id?: string | null;
          created_by: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          owner_id?: string;
          customer_id?: string | null;
          opportunity_id?: string | null;
          source_type?: Database["public"]["Enums"]["work_item_source_type"];
          work_type?: Database["public"]["Enums"]["work_item_type"];
          title?: string;
          description?: string;
          rationale?: string;
          priority_score?: number;
          priority_band?: Database["public"]["Enums"]["work_priority_band"];
          status?: Database["public"]["Enums"]["work_item_status"];
          scheduled_for?: string | null;
          due_at?: string | null;
          completed_at?: string | null;
          snoozed_until?: string | null;
          source_ref_type?: string | null;
          source_ref_id?: string | null;
          ai_generated?: boolean;
          ai_run_id?: string | null;
          created_by?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      daily_work_plans: {
        Row: {
          id: string;
          org_id: string;
          user_id: string;
          plan_date: string;
          status: Database["public"]["Enums"]["daily_plan_status"];
          summary: string | null;
          total_items: number;
          critical_items: number;
          focus_theme: string | null;
          source_snapshot: Json;
          generated_by: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id: string;
          plan_date: string;
          status?: Database["public"]["Enums"]["daily_plan_status"];
          summary?: string | null;
          total_items?: number;
          critical_items?: number;
          focus_theme?: string | null;
          source_snapshot?: Json;
          generated_by: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string;
          plan_date?: string;
          status?: Database["public"]["Enums"]["daily_plan_status"];
          summary?: string | null;
          total_items?: number;
          critical_items?: number;
          focus_theme?: string | null;
          source_snapshot?: Json;
          generated_by?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      daily_work_plan_items: {
        Row: {
          id: string;
          org_id: string;
          plan_id: string;
          work_item_id: string;
          sequence_no: number;
          planned_time_block: Database["public"]["Enums"]["plan_time_block"] | null;
          recommendation_reason: string;
          must_do: boolean;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          plan_id: string;
          work_item_id: string;
          sequence_no: number;
          planned_time_block?: Database["public"]["Enums"]["plan_time_block"] | null;
          recommendation_reason?: string;
          must_do?: boolean;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          plan_id?: string;
          work_item_id?: string;
          sequence_no?: number;
          planned_time_block?: Database["public"]["Enums"]["plan_time_block"] | null;
          recommendation_reason?: string;
          must_do?: boolean;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      task_execution_logs: {
        Row: {
          id: string;
          org_id: string;
          work_item_id: string;
          user_id: string;
          action_type: Database["public"]["Enums"]["task_action_type"];
          action_note: string | null;
          before_snapshot: Json;
          after_snapshot: Json;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          work_item_id: string;
          user_id: string;
          action_type: Database["public"]["Enums"]["task_action_type"];
          action_note?: string | null;
          before_snapshot?: Json;
          after_snapshot?: Json;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          work_item_id?: string;
          user_id?: string;
          action_type?: Database["public"]["Enums"]["task_action_type"];
          action_note?: string | null;
          before_snapshot?: Json;
          after_snapshot?: Json;
          created_at?: string;
        };
        Relationships: [];
      };
      work_agent_runs: {
        Row: {
          id: string;
          org_id: string;
          user_id: string | null;
          run_scope: Database["public"]["Enums"]["work_agent_run_scope"];
          status: Database["public"]["Enums"]["work_agent_run_status"];
          input_snapshot: Json;
          output_snapshot: Json;
          parsed_result: Json;
          provider: Database["public"]["Enums"]["ai_provider"] | null;
          model: string | null;
          result_source: Database["public"]["Enums"]["ai_result_source"];
          fallback_reason: string | null;
          error_message: string | null;
          started_at: string | null;
          completed_at: string | null;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id?: string | null;
          run_scope: Database["public"]["Enums"]["work_agent_run_scope"];
          status?: Database["public"]["Enums"]["work_agent_run_status"];
          input_snapshot?: Json;
          output_snapshot?: Json;
          parsed_result?: Json;
          provider?: Database["public"]["Enums"]["ai_provider"] | null;
          model?: string | null;
          result_source?: Database["public"]["Enums"]["ai_result_source"];
          fallback_reason?: string | null;
          error_message?: string | null;
          started_at?: string | null;
          completed_at?: string | null;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string | null;
          run_scope?: Database["public"]["Enums"]["work_agent_run_scope"];
          status?: Database["public"]["Enums"]["work_agent_run_status"];
          input_snapshot?: Json;
          output_snapshot?: Json;
          parsed_result?: Json;
          provider?: Database["public"]["Enums"]["ai_provider"] | null;
          model?: string | null;
          result_source?: Database["public"]["Enums"]["ai_result_source"];
          fallback_reason?: string | null;
          error_message?: string | null;
          started_at?: string | null;
          completed_at?: string | null;
          created_at?: string;
        };
        Relationships: [];
      };
      prep_cards: {
        Row: {
          id: string;
          org_id: string;
          owner_id: string | null;
          customer_id: string | null;
          opportunity_id: string | null;
          work_item_id: string | null;
          card_type: Database["public"]["Enums"]["prep_card_type"];
          status: Database["public"]["Enums"]["prep_card_status"];
          title: string;
          summary: string;
          card_payload: Json;
          source_snapshot: Json;
          generated_by: string;
          ai_run_id: string | null;
          valid_until: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          owner_id?: string | null;
          customer_id?: string | null;
          opportunity_id?: string | null;
          work_item_id?: string | null;
          card_type: Database["public"]["Enums"]["prep_card_type"];
          status?: Database["public"]["Enums"]["prep_card_status"];
          title: string;
          summary?: string;
          card_payload?: Json;
          source_snapshot?: Json;
          generated_by: string;
          ai_run_id?: string | null;
          valid_until?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          owner_id?: string | null;
          customer_id?: string | null;
          opportunity_id?: string | null;
          work_item_id?: string | null;
          card_type?: Database["public"]["Enums"]["prep_card_type"];
          status?: Database["public"]["Enums"]["prep_card_status"];
          title?: string;
          summary?: string;
          card_payload?: Json;
          source_snapshot?: Json;
          generated_by?: string;
          ai_run_id?: string | null;
          valid_until?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      morning_briefs: {
        Row: {
          id: string;
          org_id: string;
          target_user_id: string | null;
          brief_type: Database["public"]["Enums"]["morning_brief_type"];
          brief_date: string;
          status: Database["public"]["Enums"]["morning_brief_status"];
          headline: string | null;
          executive_summary: string | null;
          brief_payload: Json;
          source_snapshot: Json;
          generated_by: string;
          ai_run_id: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          target_user_id?: string | null;
          brief_type: Database["public"]["Enums"]["morning_brief_type"];
          brief_date: string;
          status?: Database["public"]["Enums"]["morning_brief_status"];
          headline?: string | null;
          executive_summary?: string | null;
          brief_payload?: Json;
          source_snapshot?: Json;
          generated_by: string;
          ai_run_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          target_user_id?: string | null;
          brief_type?: Database["public"]["Enums"]["morning_brief_type"];
          brief_date?: string;
          status?: Database["public"]["Enums"]["morning_brief_status"];
          headline?: string | null;
          executive_summary?: string | null;
          brief_payload?: Json;
          source_snapshot?: Json;
          generated_by?: string;
          ai_run_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      content_drafts: {
        Row: {
          id: string;
          org_id: string;
          owner_id: string;
          customer_id: string | null;
          opportunity_id: string | null;
          prep_card_id: string | null;
          work_item_id: string | null;
          draft_type: Database["public"]["Enums"]["content_draft_type"];
          status: Database["public"]["Enums"]["content_draft_status"];
          title: string;
          content_markdown: string;
          content_text: string;
          rationale: string;
          source_snapshot: Json;
          generated_by: string;
          ai_run_id: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          owner_id: string;
          customer_id?: string | null;
          opportunity_id?: string | null;
          prep_card_id?: string | null;
          work_item_id?: string | null;
          draft_type: Database["public"]["Enums"]["content_draft_type"];
          status?: Database["public"]["Enums"]["content_draft_status"];
          title: string;
          content_markdown?: string;
          content_text?: string;
          rationale?: string;
          source_snapshot?: Json;
          generated_by: string;
          ai_run_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          owner_id?: string;
          customer_id?: string | null;
          opportunity_id?: string | null;
          prep_card_id?: string | null;
          work_item_id?: string | null;
          draft_type?: Database["public"]["Enums"]["content_draft_type"];
          status?: Database["public"]["Enums"]["content_draft_status"];
          title?: string;
          content_markdown?: string;
          content_text?: string;
          rationale?: string;
          source_snapshot?: Json;
          generated_by?: string;
          ai_run_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      prep_feedback: {
        Row: {
          id: string;
          org_id: string;
          user_id: string;
          target_type: Database["public"]["Enums"]["prep_feedback_target_type"];
          target_id: string;
          feedback_type: Database["public"]["Enums"]["prep_feedback_type"];
          feedback_text: string | null;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id: string;
          target_type: Database["public"]["Enums"]["prep_feedback_target_type"];
          target_id: string;
          feedback_type: Database["public"]["Enums"]["prep_feedback_type"];
          feedback_text?: string | null;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string;
          target_type?: Database["public"]["Enums"]["prep_feedback_target_type"];
          target_id?: string;
          feedback_type?: Database["public"]["Enums"]["prep_feedback_type"];
          feedback_text?: string | null;
          created_at?: string;
        };
        Relationships: [];
      };
      action_outcomes: {
        Row: {
          id: string;
          org_id: string;
          owner_id: string;
          customer_id: string | null;
          opportunity_id: string | null;
          work_item_id: string | null;
          followup_id: string | null;
          communication_input_id: string | null;
          prep_card_id: string | null;
          content_draft_id: string | null;
          outcome_type: Database["public"]["Enums"]["action_outcome_type"];
          result_status: Database["public"]["Enums"]["action_outcome_status"];
          stage_changed: boolean;
          old_stage: Database["public"]["Enums"]["customer_stage"] | null;
          new_stage: Database["public"]["Enums"]["customer_stage"] | null;
          customer_sentiment_shift: Database["public"]["Enums"]["action_outcome_sentiment_shift"];
          key_outcome_summary: string;
          new_objections: Json;
          new_risks: Json;
          next_step_defined: boolean;
          next_step_text: string | null;
          followup_due_at: string | null;
          used_prep_card: boolean;
          used_draft: boolean;
          usefulness_rating: Database["public"]["Enums"]["action_outcome_usefulness_rating"];
          notes: string | null;
          created_by: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          owner_id: string;
          customer_id?: string | null;
          opportunity_id?: string | null;
          work_item_id?: string | null;
          followup_id?: string | null;
          communication_input_id?: string | null;
          prep_card_id?: string | null;
          content_draft_id?: string | null;
          outcome_type: Database["public"]["Enums"]["action_outcome_type"];
          result_status?: Database["public"]["Enums"]["action_outcome_status"];
          stage_changed?: boolean;
          old_stage?: Database["public"]["Enums"]["customer_stage"] | null;
          new_stage?: Database["public"]["Enums"]["customer_stage"] | null;
          customer_sentiment_shift?: Database["public"]["Enums"]["action_outcome_sentiment_shift"];
          key_outcome_summary?: string;
          new_objections?: Json;
          new_risks?: Json;
          next_step_defined?: boolean;
          next_step_text?: string | null;
          followup_due_at?: string | null;
          used_prep_card?: boolean;
          used_draft?: boolean;
          usefulness_rating?: Database["public"]["Enums"]["action_outcome_usefulness_rating"];
          notes?: string | null;
          created_by: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          owner_id?: string;
          customer_id?: string | null;
          opportunity_id?: string | null;
          work_item_id?: string | null;
          followup_id?: string | null;
          communication_input_id?: string | null;
          prep_card_id?: string | null;
          content_draft_id?: string | null;
          outcome_type?: Database["public"]["Enums"]["action_outcome_type"];
          result_status?: Database["public"]["Enums"]["action_outcome_status"];
          stage_changed?: boolean;
          old_stage?: Database["public"]["Enums"]["customer_stage"] | null;
          new_stage?: Database["public"]["Enums"]["customer_stage"] | null;
          customer_sentiment_shift?: Database["public"]["Enums"]["action_outcome_sentiment_shift"];
          key_outcome_summary?: string;
          new_objections?: Json;
          new_risks?: Json;
          next_step_defined?: boolean;
          next_step_text?: string | null;
          followup_due_at?: string | null;
          used_prep_card?: boolean;
          used_draft?: boolean;
          usefulness_rating?: Database["public"]["Enums"]["action_outcome_usefulness_rating"];
          notes?: string | null;
          created_by?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      suggestion_adoptions: {
        Row: {
          id: string;
          org_id: string;
          user_id: string;
          target_type: Database["public"]["Enums"]["suggestion_target_type"];
          target_id: string;
          adoption_type: Database["public"]["Enums"]["suggestion_adoption_type"];
          edit_distance_hint: number | null;
          adoption_context: Database["public"]["Enums"]["suggestion_adoption_context"];
          linked_outcome_id: string | null;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id: string;
          target_type: Database["public"]["Enums"]["suggestion_target_type"];
          target_id: string;
          adoption_type: Database["public"]["Enums"]["suggestion_adoption_type"];
          edit_distance_hint?: number | null;
          adoption_context: Database["public"]["Enums"]["suggestion_adoption_context"];
          linked_outcome_id?: string | null;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string;
          target_type?: Database["public"]["Enums"]["suggestion_target_type"];
          target_id?: string;
          adoption_type?: Database["public"]["Enums"]["suggestion_adoption_type"];
          edit_distance_hint?: number | null;
          adoption_context?: Database["public"]["Enums"]["suggestion_adoption_context"];
          linked_outcome_id?: string | null;
          created_at?: string;
        };
        Relationships: [];
      };
      playbooks: {
        Row: {
          id: string;
          org_id: string;
          scope_type: Database["public"]["Enums"]["playbook_scope_type"];
          owner_user_id: string | null;
          playbook_type: Database["public"]["Enums"]["playbook_type"];
          title: string;
          summary: string;
          status: Database["public"]["Enums"]["playbook_status"];
          confidence_score: number;
          applicability_notes: string;
          source_snapshot: Json;
          generated_by: string;
          ai_run_id: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          scope_type?: Database["public"]["Enums"]["playbook_scope_type"];
          owner_user_id?: string | null;
          playbook_type: Database["public"]["Enums"]["playbook_type"];
          title: string;
          summary?: string;
          status?: Database["public"]["Enums"]["playbook_status"];
          confidence_score?: number;
          applicability_notes?: string;
          source_snapshot?: Json;
          generated_by: string;
          ai_run_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          scope_type?: Database["public"]["Enums"]["playbook_scope_type"];
          owner_user_id?: string | null;
          playbook_type?: Database["public"]["Enums"]["playbook_type"];
          title?: string;
          summary?: string;
          status?: Database["public"]["Enums"]["playbook_status"];
          confidence_score?: number;
          applicability_notes?: string;
          source_snapshot?: Json;
          generated_by?: string;
          ai_run_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      playbook_entries: {
        Row: {
          id: string;
          org_id: string;
          playbook_id: string;
          entry_title: string;
          entry_summary: string;
          conditions: Json;
          recommended_actions: Json;
          caution_notes: Json;
          evidence_snapshot: Json;
          success_signal: Json;
          failure_modes: Json;
          confidence_score: number;
          sort_order: number;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          playbook_id: string;
          entry_title: string;
          entry_summary?: string;
          conditions?: Json;
          recommended_actions?: Json;
          caution_notes?: Json;
          evidence_snapshot?: Json;
          success_signal?: Json;
          failure_modes?: Json;
          confidence_score?: number;
          sort_order?: number;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          playbook_id?: string;
          entry_title?: string;
          entry_summary?: string;
          conditions?: Json;
          recommended_actions?: Json;
          caution_notes?: Json;
          evidence_snapshot?: Json;
          success_signal?: Json;
          failure_modes?: Json;
          confidence_score?: number;
          sort_order?: number;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      outcome_reviews: {
        Row: {
          id: string;
          org_id: string;
          review_scope: Database["public"]["Enums"]["outcome_review_scope"];
          target_user_id: string | null;
          period_start: string;
          period_end: string;
          status: Database["public"]["Enums"]["outcome_review_status"];
          title: string | null;
          executive_summary: string | null;
          effective_patterns: Json;
          ineffective_patterns: Json;
          repeated_failures: Json;
          coaching_actions: Json;
          playbook_candidates: Json;
          source_snapshot: Json;
          generated_by: string;
          ai_run_id: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          review_scope?: Database["public"]["Enums"]["outcome_review_scope"];
          target_user_id?: string | null;
          period_start: string;
          period_end: string;
          status?: Database["public"]["Enums"]["outcome_review_status"];
          title?: string | null;
          executive_summary?: string | null;
          effective_patterns?: Json;
          ineffective_patterns?: Json;
          repeated_failures?: Json;
          coaching_actions?: Json;
          playbook_candidates?: Json;
          source_snapshot?: Json;
          generated_by: string;
          ai_run_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          review_scope?: Database["public"]["Enums"]["outcome_review_scope"];
          target_user_id?: string | null;
          period_start?: string;
          period_end?: string;
          status?: Database["public"]["Enums"]["outcome_review_status"];
          title?: string | null;
          executive_summary?: string | null;
          effective_patterns?: Json;
          ineffective_patterns?: Json;
          repeated_failures?: Json;
          coaching_actions?: Json;
          playbook_candidates?: Json;
          source_snapshot?: Json;
          generated_by?: string;
          ai_run_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      playbook_feedback: {
        Row: {
          id: string;
          org_id: string;
          user_id: string;
          playbook_id: string;
          playbook_entry_id: string | null;
          feedback_type: Database["public"]["Enums"]["playbook_feedback_type"];
          feedback_text: string | null;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id: string;
          playbook_id: string;
          playbook_entry_id?: string | null;
          feedback_type: Database["public"]["Enums"]["playbook_feedback_type"];
          feedback_text?: string | null;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string;
          playbook_id?: string;
          playbook_entry_id?: string | null;
          feedback_type?: Database["public"]["Enums"]["playbook_feedback_type"];
          feedback_text?: string | null;
          created_at?: string;
        };
        Relationships: [];
      };
      deal_rooms: {
        Row: {
          id: string;
          org_id: string;
          customer_id: string;
          opportunity_id: string | null;
          owner_id: string;
          room_status: Database["public"]["Enums"]["deal_room_status"];
          priority_band: Database["public"]["Enums"]["deal_room_priority_band"];
          title: string;
          command_summary: string;
          current_goal: string;
          current_blockers: Json;
          next_milestone: string | null;
          next_milestone_due_at: string | null;
          manager_attention_needed: boolean;
          source_snapshot: Json;
          created_by: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          customer_id: string;
          opportunity_id?: string | null;
          owner_id: string;
          room_status?: Database["public"]["Enums"]["deal_room_status"];
          priority_band?: Database["public"]["Enums"]["deal_room_priority_band"];
          title: string;
          command_summary?: string;
          current_goal?: string;
          current_blockers?: Json;
          next_milestone?: string | null;
          next_milestone_due_at?: string | null;
          manager_attention_needed?: boolean;
          source_snapshot?: Json;
          created_by: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          customer_id?: string;
          opportunity_id?: string | null;
          owner_id?: string;
          room_status?: Database["public"]["Enums"]["deal_room_status"];
          priority_band?: Database["public"]["Enums"]["deal_room_priority_band"];
          title?: string;
          command_summary?: string;
          current_goal?: string;
          current_blockers?: Json;
          next_milestone?: string | null;
          next_milestone_due_at?: string | null;
          manager_attention_needed?: boolean;
          source_snapshot?: Json;
          created_by?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      collaboration_threads: {
        Row: {
          id: string;
          org_id: string;
          deal_room_id: string;
          thread_type: Database["public"]["Enums"]["collaboration_thread_type"];
          title: string;
          status: Database["public"]["Enums"]["collaboration_thread_status"];
          summary: string;
          created_by: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          deal_room_id: string;
          thread_type: Database["public"]["Enums"]["collaboration_thread_type"];
          title: string;
          status?: Database["public"]["Enums"]["collaboration_thread_status"];
          summary?: string;
          created_by: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          deal_room_id?: string;
          thread_type?: Database["public"]["Enums"]["collaboration_thread_type"];
          title?: string;
          status?: Database["public"]["Enums"]["collaboration_thread_status"];
          summary?: string;
          created_by?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      collaboration_messages: {
        Row: {
          id: string;
          org_id: string;
          thread_id: string;
          author_user_id: string;
          message_type: Database["public"]["Enums"]["collaboration_message_type"];
          body_markdown: string;
          mentions: Json;
          source_ref_type: string | null;
          source_ref_id: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          thread_id: string;
          author_user_id: string;
          message_type?: Database["public"]["Enums"]["collaboration_message_type"];
          body_markdown: string;
          mentions?: Json;
          source_ref_type?: string | null;
          source_ref_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          thread_id?: string;
          author_user_id?: string;
          message_type?: Database["public"]["Enums"]["collaboration_message_type"];
          body_markdown?: string;
          mentions?: Json;
          source_ref_type?: string | null;
          source_ref_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      decision_records: {
        Row: {
          id: string;
          org_id: string;
          deal_room_id: string;
          customer_id: string;
          opportunity_id: string | null;
          decision_type: Database["public"]["Enums"]["decision_type"];
          status: Database["public"]["Enums"]["decision_status"];
          title: string;
          context_summary: string;
          options_considered: Json;
          recommended_option: string | null;
          decision_reason: string | null;
          decided_by: string | null;
          requested_by: string;
          due_at: string | null;
          completed_at: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          deal_room_id: string;
          customer_id: string;
          opportunity_id?: string | null;
          decision_type: Database["public"]["Enums"]["decision_type"];
          status?: Database["public"]["Enums"]["decision_status"];
          title: string;
          context_summary?: string;
          options_considered?: Json;
          recommended_option?: string | null;
          decision_reason?: string | null;
          decided_by?: string | null;
          requested_by: string;
          due_at?: string | null;
          completed_at?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          deal_room_id?: string;
          customer_id?: string;
          opportunity_id?: string | null;
          decision_type?: Database["public"]["Enums"]["decision_type"];
          status?: Database["public"]["Enums"]["decision_status"];
          title?: string;
          context_summary?: string;
          options_considered?: Json;
          recommended_option?: string | null;
          decision_reason?: string | null;
          decided_by?: string | null;
          requested_by?: string;
          due_at?: string | null;
          completed_at?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      deal_participants: {
        Row: {
          id: string;
          org_id: string;
          deal_room_id: string;
          user_id: string;
          role_in_room: Database["public"]["Enums"]["deal_participant_role"];
          is_active: boolean;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          deal_room_id: string;
          user_id: string;
          role_in_room?: Database["public"]["Enums"]["deal_participant_role"];
          is_active?: boolean;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          deal_room_id?: string;
          user_id?: string;
          role_in_room?: Database["public"]["Enums"]["deal_participant_role"];
          is_active?: boolean;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      deal_checkpoints: {
        Row: {
          id: string;
          org_id: string;
          deal_room_id: string;
          checkpoint_type: Database["public"]["Enums"]["deal_checkpoint_type"];
          status: Database["public"]["Enums"]["deal_checkpoint_status"];
          title: string;
          description: string;
          due_at: string | null;
          completed_at: string | null;
          owner_id: string | null;
          evidence_snapshot: Json;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          deal_room_id: string;
          checkpoint_type: Database["public"]["Enums"]["deal_checkpoint_type"];
          status?: Database["public"]["Enums"]["deal_checkpoint_status"];
          title: string;
          description?: string;
          due_at?: string | null;
          completed_at?: string | null;
          owner_id?: string | null;
          evidence_snapshot?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          deal_room_id?: string;
          checkpoint_type?: Database["public"]["Enums"]["deal_checkpoint_type"];
          status?: Database["public"]["Enums"]["deal_checkpoint_status"];
          title?: string;
          description?: string;
          due_at?: string | null;
          completed_at?: string | null;
          owner_id?: string | null;
          evidence_snapshot?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      intervention_requests: {
        Row: {
          id: string;
          org_id: string;
          deal_room_id: string;
          requested_by: string;
          target_user_id: string | null;
          request_type: Database["public"]["Enums"]["intervention_request_type"];
          priority_band: Database["public"]["Enums"]["intervention_priority_band"];
          status: Database["public"]["Enums"]["intervention_request_status"];
          request_summary: string;
          context_snapshot: Json;
          due_at: string | null;
          completed_at: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          deal_room_id: string;
          requested_by: string;
          target_user_id?: string | null;
          request_type: Database["public"]["Enums"]["intervention_request_type"];
          priority_band?: Database["public"]["Enums"]["intervention_priority_band"];
          status?: Database["public"]["Enums"]["intervention_request_status"];
          request_summary: string;
          context_snapshot?: Json;
          due_at?: string | null;
          completed_at?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          deal_room_id?: string;
          requested_by?: string;
          target_user_id?: string | null;
          request_type?: Database["public"]["Enums"]["intervention_request_type"];
          priority_band?: Database["public"]["Enums"]["intervention_priority_band"];
          status?: Database["public"]["Enums"]["intervention_request_status"];
          request_summary?: string;
          context_snapshot?: Json;
          due_at?: string | null;
          completed_at?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      external_accounts: {
        Row: {
          id: string;
          org_id: string;
          user_id: string;
          provider_type: Database["public"]["Enums"]["external_provider_type"];
          provider_name: Database["public"]["Enums"]["external_provider_name"];
          account_label: string;
          connection_status: Database["public"]["Enums"]["external_connection_status"];
          metadata: Json;
          connected_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id: string;
          provider_type: Database["public"]["Enums"]["external_provider_type"];
          provider_name: Database["public"]["Enums"]["external_provider_name"];
          account_label: string;
          connection_status?: Database["public"]["Enums"]["external_connection_status"];
          metadata?: Json;
          connected_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string;
          provider_type?: Database["public"]["Enums"]["external_provider_type"];
          provider_name?: Database["public"]["Enums"]["external_provider_name"];
          account_label?: string;
          connection_status?: Database["public"]["Enums"]["external_connection_status"];
          metadata?: Json;
          connected_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      email_threads: {
        Row: {
          id: string;
          org_id: string;
          owner_id: string;
          customer_id: string | null;
          opportunity_id: string | null;
          deal_room_id: string | null;
          external_account_id: string | null;
          external_thread_ref: string | null;
          subject: string;
          participants: Json;
          latest_message_at: string | null;
          thread_status: Database["public"]["Enums"]["email_thread_status"];
          sentiment_hint: Database["public"]["Enums"]["email_sentiment_hint"];
          summary: string;
          source_snapshot: Json;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          owner_id: string;
          customer_id?: string | null;
          opportunity_id?: string | null;
          deal_room_id?: string | null;
          external_account_id?: string | null;
          external_thread_ref?: string | null;
          subject: string;
          participants?: Json;
          latest_message_at?: string | null;
          thread_status?: Database["public"]["Enums"]["email_thread_status"];
          sentiment_hint?: Database["public"]["Enums"]["email_sentiment_hint"];
          summary?: string;
          source_snapshot?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          owner_id?: string;
          customer_id?: string | null;
          opportunity_id?: string | null;
          deal_room_id?: string | null;
          external_account_id?: string | null;
          external_thread_ref?: string | null;
          subject?: string;
          participants?: Json;
          latest_message_at?: string | null;
          thread_status?: Database["public"]["Enums"]["email_thread_status"];
          sentiment_hint?: Database["public"]["Enums"]["email_sentiment_hint"];
          summary?: string;
          source_snapshot?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      email_messages: {
        Row: {
          id: string;
          org_id: string;
          thread_id: string;
          sender_user_id: string | null;
          direction: Database["public"]["Enums"]["email_message_direction"];
          external_message_ref: string | null;
          message_subject: string;
          message_body_text: string;
          message_body_markdown: string;
          sent_at: string | null;
          received_at: string | null;
          status: Database["public"]["Enums"]["email_message_status"];
          source_type: Database["public"]["Enums"]["email_message_source_type"];
          ai_run_id: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          thread_id: string;
          sender_user_id?: string | null;
          direction: Database["public"]["Enums"]["email_message_direction"];
          external_message_ref?: string | null;
          message_subject?: string;
          message_body_text?: string;
          message_body_markdown?: string;
          sent_at?: string | null;
          received_at?: string | null;
          status?: Database["public"]["Enums"]["email_message_status"];
          source_type?: Database["public"]["Enums"]["email_message_source_type"];
          ai_run_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          thread_id?: string;
          sender_user_id?: string | null;
          direction?: Database["public"]["Enums"]["email_message_direction"];
          external_message_ref?: string | null;
          message_subject?: string;
          message_body_text?: string;
          message_body_markdown?: string;
          sent_at?: string | null;
          received_at?: string | null;
          status?: Database["public"]["Enums"]["email_message_status"];
          source_type?: Database["public"]["Enums"]["email_message_source_type"];
          ai_run_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      calendar_events: {
        Row: {
          id: string;
          org_id: string;
          owner_id: string;
          customer_id: string | null;
          opportunity_id: string | null;
          deal_room_id: string | null;
          external_account_id: string | null;
          external_event_ref: string | null;
          event_type: Database["public"]["Enums"]["calendar_event_type"];
          title: string;
          description: string;
          attendees: Json;
          start_at: string;
          end_at: string;
          meeting_status: Database["public"]["Enums"]["calendar_meeting_status"];
          agenda_summary: string;
          notes_summary: string;
          source_snapshot: Json;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          owner_id: string;
          customer_id?: string | null;
          opportunity_id?: string | null;
          deal_room_id?: string | null;
          external_account_id?: string | null;
          external_event_ref?: string | null;
          event_type: Database["public"]["Enums"]["calendar_event_type"];
          title: string;
          description?: string;
          attendees?: Json;
          start_at: string;
          end_at: string;
          meeting_status?: Database["public"]["Enums"]["calendar_meeting_status"];
          agenda_summary?: string;
          notes_summary?: string;
          source_snapshot?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          owner_id?: string;
          customer_id?: string | null;
          opportunity_id?: string | null;
          deal_room_id?: string | null;
          external_account_id?: string | null;
          external_event_ref?: string | null;
          event_type?: Database["public"]["Enums"]["calendar_event_type"];
          title?: string;
          description?: string;
          attendees?: Json;
          start_at?: string;
          end_at?: string;
          meeting_status?: Database["public"]["Enums"]["calendar_meeting_status"];
          agenda_summary?: string;
          notes_summary?: string;
          source_snapshot?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      document_assets: {
        Row: {
          id: string;
          org_id: string;
          owner_id: string;
          customer_id: string | null;
          opportunity_id: string | null;
          deal_room_id: string | null;
          source_type: Database["public"]["Enums"]["document_asset_source_type"];
          document_type: Database["public"]["Enums"]["document_asset_type"];
          title: string;
          file_name: string;
          mime_type: string;
          storage_path: string | null;
          extracted_text: string;
          summary: string;
          tags: Json;
          linked_prep_card_id: string | null;
          linked_draft_id: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          owner_id: string;
          customer_id?: string | null;
          opportunity_id?: string | null;
          deal_room_id?: string | null;
          source_type?: Database["public"]["Enums"]["document_asset_source_type"];
          document_type?: Database["public"]["Enums"]["document_asset_type"];
          title: string;
          file_name: string;
          mime_type?: string;
          storage_path?: string | null;
          extracted_text?: string;
          summary?: string;
          tags?: Json;
          linked_prep_card_id?: string | null;
          linked_draft_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          owner_id?: string;
          customer_id?: string | null;
          opportunity_id?: string | null;
          deal_room_id?: string | null;
          source_type?: Database["public"]["Enums"]["document_asset_source_type"];
          document_type?: Database["public"]["Enums"]["document_asset_type"];
          title?: string;
          file_name?: string;
          mime_type?: string;
          storage_path?: string | null;
          extracted_text?: string;
          summary?: string;
          tags?: Json;
          linked_prep_card_id?: string | null;
          linked_draft_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      external_touchpoint_events: {
        Row: {
          id: string;
          org_id: string;
          owner_id: string | null;
          customer_id: string | null;
          opportunity_id: string | null;
          deal_room_id: string | null;
          touchpoint_type: Database["public"]["Enums"]["touchpoint_type"];
          event_type: Database["public"]["Enums"]["touchpoint_event_type"];
          related_ref_type: string | null;
          related_ref_id: string | null;
          event_summary: string;
          event_payload: Json;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          owner_id?: string | null;
          customer_id?: string | null;
          opportunity_id?: string | null;
          deal_room_id?: string | null;
          touchpoint_type: Database["public"]["Enums"]["touchpoint_type"];
          event_type: Database["public"]["Enums"]["touchpoint_event_type"];
          related_ref_type?: string | null;
          related_ref_id?: string | null;
          event_summary?: string;
          event_payload?: Json;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          owner_id?: string | null;
          customer_id?: string | null;
          opportunity_id?: string | null;
          deal_room_id?: string | null;
          touchpoint_type?: Database["public"]["Enums"]["touchpoint_type"];
          event_type?: Database["public"]["Enums"]["touchpoint_event_type"];
          related_ref_type?: string | null;
          related_ref_id?: string | null;
          event_summary?: string;
          event_payload?: Json;
          created_at?: string;
        };
        Relationships: [];
      };
      org_settings: {
        Row: {
          id: string;
          org_id: string;
          org_display_name: string;
          brand_name: string;
          industry_hint: string | null;
          timezone: string;
          locale: string;
          default_customer_stages: Json;
          default_opportunity_stages: Json;
          default_alert_rules: Json;
          default_followup_sla_days: number;
          onboarding_completed: boolean;
          onboarding_step_state: Json;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          org_display_name: string;
          brand_name?: string;
          industry_hint?: string | null;
          timezone?: string;
          locale?: string;
          default_customer_stages?: Json;
          default_opportunity_stages?: Json;
          default_alert_rules?: Json;
          default_followup_sla_days?: number;
          onboarding_completed?: boolean;
          onboarding_step_state?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          org_display_name?: string;
          brand_name?: string;
          industry_hint?: string | null;
          timezone?: string;
          locale?: string;
          default_customer_stages?: Json;
          default_opportunity_stages?: Json;
          default_alert_rules?: Json;
          default_followup_sla_days?: number;
          onboarding_completed?: boolean;
          onboarding_step_state?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      org_feature_flags: {
        Row: {
          id: string;
          org_id: string;
          feature_key: Database["public"]["Enums"]["org_feature_key"];
          is_enabled: boolean;
          config_json: Json;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          feature_key: Database["public"]["Enums"]["org_feature_key"];
          is_enabled?: boolean;
          config_json?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          feature_key?: Database["public"]["Enums"]["org_feature_key"];
          is_enabled?: boolean;
          config_json?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      org_ai_settings: {
        Row: {
          id: string;
          org_id: string;
          provider: Database["public"]["Enums"]["ai_provider"];
          model_default: string;
          model_reasoning: string;
          fallback_mode: Database["public"]["Enums"]["org_ai_fallback_mode"];
          auto_analysis_enabled: boolean;
          auto_plan_enabled: boolean;
          auto_brief_enabled: boolean;
          auto_touchpoint_review_enabled: boolean;
          human_review_required_for_sensitive_actions: boolean;
          max_daily_ai_runs: number | null;
          max_monthly_ai_runs: number | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          provider?: Database["public"]["Enums"]["ai_provider"];
          model_default?: string;
          model_reasoning?: string;
          fallback_mode?: Database["public"]["Enums"]["org_ai_fallback_mode"];
          auto_analysis_enabled?: boolean;
          auto_plan_enabled?: boolean;
          auto_brief_enabled?: boolean;
          auto_touchpoint_review_enabled?: boolean;
          human_review_required_for_sensitive_actions?: boolean;
          max_daily_ai_runs?: number | null;
          max_monthly_ai_runs?: number | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          provider?: Database["public"]["Enums"]["ai_provider"];
          model_default?: string;
          model_reasoning?: string;
          fallback_mode?: Database["public"]["Enums"]["org_ai_fallback_mode"];
          auto_analysis_enabled?: boolean;
          auto_plan_enabled?: boolean;
          auto_brief_enabled?: boolean;
          auto_touchpoint_review_enabled?: boolean;
          human_review_required_for_sensitive_actions?: boolean;
          max_daily_ai_runs?: number | null;
          max_monthly_ai_runs?: number | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      org_memberships: {
        Row: {
          id: string;
          org_id: string;
          user_id: string;
          role: Database["public"]["Enums"]["org_member_role"];
          seat_status: Database["public"]["Enums"]["org_seat_status"];
          invited_by: string | null;
          invited_at: string | null;
          joined_at: string | null;
          last_active_at: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id: string;
          role?: Database["public"]["Enums"]["org_member_role"];
          seat_status?: Database["public"]["Enums"]["org_seat_status"];
          invited_by?: string | null;
          invited_at?: string | null;
          joined_at?: string | null;
          last_active_at?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string;
          role?: Database["public"]["Enums"]["org_member_role"];
          seat_status?: Database["public"]["Enums"]["org_seat_status"];
          invited_by?: string | null;
          invited_at?: string | null;
          joined_at?: string | null;
          last_active_at?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      org_invites: {
        Row: {
          id: string;
          org_id: string;
          email: string;
          intended_role: Database["public"]["Enums"]["org_member_role"];
          invite_status: Database["public"]["Enums"]["org_invite_status"];
          invite_token: string;
          invited_by: string | null;
          expires_at: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          email: string;
          intended_role: Database["public"]["Enums"]["org_member_role"];
          invite_status?: Database["public"]["Enums"]["org_invite_status"];
          invite_token?: string;
          invited_by?: string | null;
          expires_at?: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          email?: string;
          intended_role?: Database["public"]["Enums"]["org_member_role"];
          invite_status?: Database["public"]["Enums"]["org_invite_status"];
          invite_token?: string;
          invited_by?: string | null;
          expires_at?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      org_usage_counters: {
        Row: {
          id: string;
          org_id: string;
          usage_date: string;
          usage_scope: Database["public"]["Enums"]["org_usage_scope"];
          ai_runs_count: number;
          prep_cards_count: number;
          drafts_count: number;
          reports_count: number;
          touchpoint_events_count: number;
          document_processed_count: number;
          work_plan_generations_count: number;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          usage_date: string;
          usage_scope: Database["public"]["Enums"]["org_usage_scope"];
          ai_runs_count?: number;
          prep_cards_count?: number;
          drafts_count?: number;
          reports_count?: number;
          touchpoint_events_count?: number;
          document_processed_count?: number;
          work_plan_generations_count?: number;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          usage_date?: string;
          usage_scope?: Database["public"]["Enums"]["org_usage_scope"];
          ai_runs_count?: number;
          prep_cards_count?: number;
          drafts_count?: number;
          reports_count?: number;
          touchpoint_events_count?: number;
          document_processed_count?: number;
          work_plan_generations_count?: number;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      user_usage_counters: {
        Row: {
          id: string;
          org_id: string;
          user_id: string;
          usage_date: string;
          usage_scope: Database["public"]["Enums"]["org_usage_scope"];
          ai_runs_count: number;
          prep_cards_count: number;
          drafts_count: number;
          reports_count: number;
          touchpoint_events_count: number;
          document_processed_count: number;
          work_plan_generations_count: number;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id: string;
          usage_date: string;
          usage_scope: Database["public"]["Enums"]["org_usage_scope"];
          ai_runs_count?: number;
          prep_cards_count?: number;
          drafts_count?: number;
          reports_count?: number;
          touchpoint_events_count?: number;
          document_processed_count?: number;
          work_plan_generations_count?: number;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string;
          usage_date?: string;
          usage_scope?: Database["public"]["Enums"]["org_usage_scope"];
          ai_runs_count?: number;
          prep_cards_count?: number;
          drafts_count?: number;
          reports_count?: number;
          touchpoint_events_count?: number;
          document_processed_count?: number;
          work_plan_generations_count?: number;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      org_plan_profiles: {
        Row: {
          id: string;
          org_id: string;
          plan_tier: Database["public"]["Enums"]["org_plan_tier"];
          seat_limit: number;
          ai_run_limit_monthly: number;
          document_limit_monthly: number;
          touchpoint_limit_monthly: number;
          advanced_features_enabled: boolean;
          expires_at: string | null;
          status: Database["public"]["Enums"]["org_plan_status"];
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          plan_tier?: Database["public"]["Enums"]["org_plan_tier"];
          seat_limit?: number;
          ai_run_limit_monthly?: number;
          document_limit_monthly?: number;
          touchpoint_limit_monthly?: number;
          advanced_features_enabled?: boolean;
          expires_at?: string | null;
          status?: Database["public"]["Enums"]["org_plan_status"];
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          plan_tier?: Database["public"]["Enums"]["org_plan_tier"];
          seat_limit?: number;
          ai_run_limit_monthly?: number;
          document_limit_monthly?: number;
          touchpoint_limit_monthly?: number;
          advanced_features_enabled?: boolean;
          expires_at?: string | null;
          status?: Database["public"]["Enums"]["org_plan_status"];
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      onboarding_runs: {
        Row: {
          id: string;
          org_id: string;
          initiated_by: string;
          run_type: Database["public"]["Enums"]["onboarding_run_type"];
          status: Database["public"]["Enums"]["onboarding_run_status"];
          summary: string;
          detail_snapshot: Json;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          initiated_by: string;
          run_type: Database["public"]["Enums"]["onboarding_run_type"];
          status?: Database["public"]["Enums"]["onboarding_run_status"];
          summary?: string;
          detail_snapshot?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          initiated_by?: string;
          run_type?: Database["public"]["Enums"]["onboarding_run_type"];
          status?: Database["public"]["Enums"]["onboarding_run_status"];
          summary?: string;
          detail_snapshot?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      import_jobs: {
        Row: {
          id: string;
          org_id: string;
          initiated_by: string;
          import_type: Database["public"]["Enums"]["import_type"];
          source_type: Database["public"]["Enums"]["import_source_type"];
          file_name: string;
          storage_path: string | null;
          job_status: Database["public"]["Enums"]["import_job_status"];
          total_rows: number;
          valid_rows: number;
          invalid_rows: number;
          duplicate_rows: number;
          imported_rows: number;
          skipped_rows: number;
          merged_rows: number;
          error_rows: number;
          summary: string | null;
          detail_snapshot: Json;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          initiated_by: string;
          import_type: Database["public"]["Enums"]["import_type"];
          source_type: Database["public"]["Enums"]["import_source_type"];
          file_name: string;
          storage_path?: string | null;
          job_status?: Database["public"]["Enums"]["import_job_status"];
          total_rows?: number;
          valid_rows?: number;
          invalid_rows?: number;
          duplicate_rows?: number;
          imported_rows?: number;
          skipped_rows?: number;
          merged_rows?: number;
          error_rows?: number;
          summary?: string | null;
          detail_snapshot?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          initiated_by?: string;
          import_type?: Database["public"]["Enums"]["import_type"];
          source_type?: Database["public"]["Enums"]["import_source_type"];
          file_name?: string;
          storage_path?: string | null;
          job_status?: Database["public"]["Enums"]["import_job_status"];
          total_rows?: number;
          valid_rows?: number;
          invalid_rows?: number;
          duplicate_rows?: number;
          imported_rows?: number;
          skipped_rows?: number;
          merged_rows?: number;
          error_rows?: number;
          summary?: string | null;
          detail_snapshot?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      import_job_columns: {
        Row: {
          id: string;
          org_id: string;
          import_job_id: string;
          source_column_name: string;
          source_column_index: number;
          detected_type: string | null;
          mapped_target_entity: Database["public"]["Enums"]["import_entity_type"] | null;
          mapped_target_field: string | null;
          mapping_confidence: number | null;
          normalization_rule: Json;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          import_job_id: string;
          source_column_name: string;
          source_column_index: number;
          detected_type?: string | null;
          mapped_target_entity?: Database["public"]["Enums"]["import_entity_type"] | null;
          mapped_target_field?: string | null;
          mapping_confidence?: number | null;
          normalization_rule?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          import_job_id?: string;
          source_column_name?: string;
          source_column_index?: number;
          detected_type?: string | null;
          mapped_target_entity?: Database["public"]["Enums"]["import_entity_type"] | null;
          mapped_target_field?: string | null;
          mapping_confidence?: number | null;
          normalization_rule?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      import_job_rows: {
        Row: {
          id: string;
          org_id: string;
          import_job_id: string;
          source_row_no: number;
          raw_payload: Json;
          normalized_payload: Json;
          row_status: Database["public"]["Enums"]["import_row_status"];
          validation_errors: Json;
          duplicate_candidates: Json;
          merge_resolution: Database["public"]["Enums"]["import_merge_resolution"] | null;
          imported_entity_type: Database["public"]["Enums"]["import_entity_type"] | null;
          imported_entity_id: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          import_job_id: string;
          source_row_no: number;
          raw_payload?: Json;
          normalized_payload?: Json;
          row_status?: Database["public"]["Enums"]["import_row_status"];
          validation_errors?: Json;
          duplicate_candidates?: Json;
          merge_resolution?: Database["public"]["Enums"]["import_merge_resolution"] | null;
          imported_entity_type?: Database["public"]["Enums"]["import_entity_type"] | null;
          imported_entity_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          import_job_id?: string;
          source_row_no?: number;
          raw_payload?: Json;
          normalized_payload?: Json;
          row_status?: Database["public"]["Enums"]["import_row_status"];
          validation_errors?: Json;
          duplicate_candidates?: Json;
          merge_resolution?: Database["public"]["Enums"]["import_merge_resolution"] | null;
          imported_entity_type?: Database["public"]["Enums"]["import_entity_type"] | null;
          imported_entity_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      import_templates: {
        Row: {
          id: string;
          org_id: string;
          template_name: string;
          import_type: Database["public"]["Enums"]["import_type"];
          column_mapping: Json;
          normalization_config: Json;
          is_default: boolean;
          created_by: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          template_name: string;
          import_type: Database["public"]["Enums"]["import_type"];
          column_mapping?: Json;
          normalization_config?: Json;
          is_default?: boolean;
          created_by: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          template_name?: string;
          import_type?: Database["public"]["Enums"]["import_type"];
          column_mapping?: Json;
          normalization_config?: Json;
          is_default?: boolean;
          created_by?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      dedupe_match_groups: {
        Row: {
          id: string;
          org_id: string;
          import_job_id: string;
          entity_type: Database["public"]["Enums"]["import_entity_type"];
          source_row_ids: Json;
          existing_entity_ids: Json;
          match_reason: string;
          confidence_score: number;
          resolution_status: Database["public"]["Enums"]["dedupe_resolution_status"];
          resolution_action: Database["public"]["Enums"]["dedupe_resolution_action"] | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          import_job_id: string;
          entity_type: Database["public"]["Enums"]["import_entity_type"];
          source_row_ids?: Json;
          existing_entity_ids?: Json;
          match_reason: string;
          confidence_score?: number;
          resolution_status?: Database["public"]["Enums"]["dedupe_resolution_status"];
          resolution_action?: Database["public"]["Enums"]["dedupe_resolution_action"] | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          import_job_id?: string;
          entity_type?: Database["public"]["Enums"]["import_entity_type"];
          source_row_ids?: Json;
          existing_entity_ids?: Json;
          match_reason?: string;
          confidence_score?: number;
          resolution_status?: Database["public"]["Enums"]["dedupe_resolution_status"];
          resolution_action?: Database["public"]["Enums"]["dedupe_resolution_action"] | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      import_audit_events: {
        Row: {
          id: string;
          org_id: string;
          import_job_id: string;
          actor_user_id: string | null;
          event_type: Database["public"]["Enums"]["import_audit_event_type"];
          event_summary: string;
          event_payload: Json;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          import_job_id: string;
          actor_user_id?: string | null;
          event_type: Database["public"]["Enums"]["import_audit_event_type"];
          event_summary: string;
          event_payload?: Json;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          import_job_id?: string;
          actor_user_id?: string | null;
          event_type?: Database["public"]["Enums"]["import_audit_event_type"];
          event_summary?: string;
          event_payload?: Json;
          created_at?: string;
        };
        Relationships: [];
      };
      mobile_draft_sync_jobs: {
        Row: {
          id: string;
          org_id: string;
          user_id: string;
          draft_type: Database["public"]["Enums"]["mobile_draft_type"];
          local_draft_id: string;
          sync_status: Database["public"]["Enums"]["mobile_draft_sync_status"];
          target_entity_type: string | null;
          target_entity_id: string | null;
          summary: string | null;
          payload_snapshot: Json;
          error_message: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id: string;
          draft_type: Database["public"]["Enums"]["mobile_draft_type"];
          local_draft_id: string;
          sync_status?: Database["public"]["Enums"]["mobile_draft_sync_status"];
          target_entity_type?: string | null;
          target_entity_id?: string | null;
          summary?: string | null;
          payload_snapshot?: Json;
          error_message?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string;
          draft_type?: Database["public"]["Enums"]["mobile_draft_type"];
          local_draft_id?: string;
          sync_status?: Database["public"]["Enums"]["mobile_draft_sync_status"];
          target_entity_type?: string | null;
          target_entity_id?: string | null;
          summary?: string | null;
          payload_snapshot?: Json;
          error_message?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      mobile_device_sessions: {
        Row: {
          id: string;
          org_id: string;
          user_id: string;
          device_label: string;
          install_type: Database["public"]["Enums"]["mobile_install_type"];
          last_seen_at: string;
          app_version: string | null;
          push_capable: boolean;
          metadata: Json;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id: string;
          device_label: string;
          install_type?: Database["public"]["Enums"]["mobile_install_type"];
          last_seen_at?: string;
          app_version?: string | null;
          push_capable?: boolean;
          metadata?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string;
          device_label?: string;
          install_type?: Database["public"]["Enums"]["mobile_install_type"];
          last_seen_at?: string;
          app_version?: string | null;
          push_capable?: boolean;
          metadata?: Json;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      offline_action_queue: {
        Row: {
          id: string;
          org_id: string;
          user_id: string;
          action_type: Database["public"]["Enums"]["offline_action_type"];
          action_payload: Json;
          queue_status: Database["public"]["Enums"]["offline_action_queue_status"];
          target_entity_type: string | null;
          target_entity_id: string | null;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id: string;
          action_type: Database["public"]["Enums"]["offline_action_type"];
          action_payload?: Json;
          queue_status?: Database["public"]["Enums"]["offline_action_queue_status"];
          target_entity_type?: string | null;
          target_entity_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string;
          action_type?: Database["public"]["Enums"]["offline_action_type"];
          action_payload?: Json;
          queue_status?: Database["public"]["Enums"]["offline_action_queue_status"];
          target_entity_type?: string | null;
          target_entity_id?: string | null;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      business_events: {
        Row: {
          id: string;
          org_id: string;
          user_id: string | null;
          event_type: Database["public"]["Enums"]["business_event_type"];
          entity_type: Database["public"]["Enums"]["business_event_entity"];
          entity_id: string | null;
          severity: Database["public"]["Enums"]["business_event_severity"];
          status: Database["public"]["Enums"]["business_event_status"];
          summary: string;
          detail: string | null;
          happened_at: string;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id?: string | null;
          event_type: Database["public"]["Enums"]["business_event_type"];
          entity_type: Database["public"]["Enums"]["business_event_entity"];
          entity_id?: string | null;
          severity?: Database["public"]["Enums"]["business_event_severity"];
          status?: Database["public"]["Enums"]["business_event_status"];
          summary: string;
          detail?: string | null;
          happened_at: string;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string | null;
          event_type?: Database["public"]["Enums"]["business_event_type"];
          entity_type?: Database["public"]["Enums"]["business_event_entity"];
          entity_id?: string | null;
          severity?: Database["public"]["Enums"]["business_event_severity"];
          status?: Database["public"]["Enums"]["business_event_status"];
          summary?: string;
          detail?: string | null;
          happened_at?: string;
          created_at?: string;
        };
        Relationships: [];
      };
      customer_health_snapshots: {
        Row: {
          id: string;
          org_id: string;
          customer_id: string;
          health_score: number | null;
          risk_level: Database["public"]["Enums"]["risk_level"];
          lifecycle_stage: Database["public"]["Enums"]["customer_lifecycle"];
          health_band: Database["public"]["Enums"]["customer_health_band"];
          snapshot_date: string;
          snapshot_week: string | null;
          snapshot_month: string | null;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          customer_id: string;
          health_score?: number | null;
          risk_level?: Database["public"]["Enums"]["risk_level"];
          lifecycle_stage?: Database["public"]["Enums"]["customer_lifecycle"];
          health_band?: Database["public"]["Enums"]["customer_health_band"];
          snapshot_date: string;
          snapshot_week?: string | null;
          snapshot_month?: string | null;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          customer_id?: string;
          health_score?: number | null;
          risk_level?: Database["public"]["Enums"]["risk_level"];
          lifecycle_stage?: Database["public"]["Enums"]["customer_lifecycle"];
          health_band?: Database["public"]["Enums"]["customer_health_band"];
          snapshot_date?: string;
          snapshot_week?: string | null;
          snapshot_month?: string | null;
          created_at?: string;
        };
        Relationships: [];
      };
      automation_rules: {
        Row: {
          id: string;
          org_id: string;
          name: string;
          description: string | null;
          scope: Database["public"]["Enums"]["automation_rule_scope"];
          trigger_type: Database["public"]["Enums"]["automation_trigger_type"];
          trigger_config: Json;
          action_type: string;
          action_config: Json;
          severity: Database["public"]["Enums"]["automation_rule_severity"];
          is_active: boolean;
          created_by: string;
          created_at: string;
          updated_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          name: string;
          description?: string | null;
          scope?: Database["public"]["Enums"]["automation_rule_scope"];
          trigger_type: Database["public"]["Enums"]["automation_trigger_type"];
          trigger_config?: Json;
          action_type: string;
          action_config?: Json;
          severity?: Database["public"]["Enums"]["automation_rule_severity"];
          is_active?: boolean;
          created_by: string;
          created_at?: string;
          updated_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          name?: string;
          description?: string | null;
          scope?: Database["public"]["Enums"]["automation_rule_scope"];
          trigger_type?: Database["public"]["Enums"]["automation_trigger_type"];
          trigger_config?: Json;
          action_type?: string;
          action_config?: Json;
          severity?: Database["public"]["Enums"]["automation_rule_severity"];
          is_active?: boolean;
          created_by?: string;
          created_at?: string;
          updated_at?: string;
        };
        Relationships: [];
      };
      automation_rule_runs: {
        Row: {
          id: string;
          org_id: string;
          rule_id: string;
          run_status: Database["public"]["Enums"]["alert_rule_run_status"];
          triggered_count: number;
          action_count: number;
          error_message: string | null;
          started_at: string;
          completed_at: string | null;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          rule_id: string;
          run_status?: Database["public"]["Enums"]["alert_rule_run_status"];
          triggered_count?: number;
          action_count?: number;
          error_message?: string | null;
          started_at: string;
          completed_at?: string | null;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          rule_id?: string;
          run_status?: Database["public"]["Enums"]["alert_rule_run_status"];
          triggered_count?: number;
          action_count?: number;
          error_message?: string | null;
          started_at?: string;
          completed_at?: string | null;
          created_at?: string;
        };
        Relationships: [];
      };
      executive_briefs: {
        Row: {
          id: string;
          org_id: string;
          user_id: string | null;
          brief_type: Database["public"]["Enums"]["executive_brief_type"];
          scope: Database["public"]["Enums"]["report_scope_type"];
          status: Database["public"]["Enums"]["executive_brief_status"];
          period_start: string;
          period_end: string;
          summary: string | null;
          content: Json | null;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          user_id?: string | null;
          brief_type: Database["public"]["Enums"]["executive_brief_type"];
          scope?: Database["public"]["Enums"]["report_scope_type"];
          status?: Database["public"]["Enums"]["executive_brief_status"];
          period_start: string;
          period_end: string;
          summary?: string | null;
          content?: Json | null;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          user_id?: string | null;
          brief_type?: Database["public"]["Enums"]["executive_brief_type"];
          scope?: Database["public"]["Enums"]["report_scope_type"];
          status?: Database["public"]["Enums"]["executive_brief_status"];
          period_start?: string;
          period_end?: string;
          summary?: string | null;
          content?: Json | null;
          created_at?: string;
        };
        Relationships: [];
      };
      manager_insights_snapshots: {
        Row: {
          id: string;
          org_id: string;
          period_start: string;
          period_end: string;
          snapshot_type: string;
          truth_band_distribution: unknown;
          intervention_stats: unknown;
          risk_signals: unknown;
          risk_improvement: unknown;
          notes: string | null;
          created_at: string;
        };
        Insert: {
          id?: string;
          org_id: string;
          period_start: string;
          period_end: string;
          snapshot_type?: string;
          truth_band_distribution?: unknown;
          intervention_stats?: unknown;
          risk_signals?: unknown;
          risk_improvement?: unknown;
          notes?: string | null;
          created_at?: string;
        };
        Update: {
          id?: string;
          org_id?: string;
          period_start?: string;
          period_end?: string;
          snapshot_type?: string;
          truth_band_distribution?: unknown;
          intervention_stats?: unknown;
          risk_signals?: unknown;
          risk_improvement?: unknown;
          notes?: string | null;
          created_at?: string;
        };
        Relationships: [];
      };
    };
    Views: Record<string, never>;
    Functions: Record<string, never>;
    Enums: {
      app_role: "sales" | "manager";
      customer_stage: "lead" | "initial_contact" | "needs_confirmed" | "proposal" | "negotiation" | "won" | "lost";
      opportunity_stage: "discovery" | "qualification" | "proposal" | "business_review" | "negotiation" | "won" | "lost";
      risk_level: "low" | "medium" | "high";
      alert_severity: "info" | "warning" | "critical";
      alert_status: "open" | "watching" | "resolved";
      communication_type: "phone" | "wechat" | "email" | "meeting" | "other";
      communication_source_type: "manual_note" | "pasted_chat" | "call_summary" | "meeting_note" | "voice_transcript" | "imported_text";
      extraction_status: "pending" | "processing" | "completed" | "failed";
      followup_draft_status: "draft" | "confirmed";
      alert_rule_type:
        | "no_followup_overdue"
        | "active_response_no_quote"
        | "quoted_but_stalled"
        | "missing_decision_maker"
        | "high_probability_stalled"
        | "no_followup_timeout"
        | "positive_reply_but_no_progress"
        | "no_decision_maker"
        | "ai_detected";
      alert_source: "rule" | "ai" | "hybrid" | "fallback";
      ai_run_status: "queued" | "running" | "completed" | "failed";
      ai_trigger_source: "manual" | "followup_submit" | "nightly_scan" | "alert_regen" | "manager_review";
      ai_scenario:
        | "followup_analysis"
        | "customer_health"
        | "leak_risk"
        | "leak_risk_inference"
        | "manager_summary"
        | "communication_extraction"
        | "sales_daily_report"
        | "sales_weekly_report"
        | "manager_daily_report"
        | "manager_weekly_report"
        | "sales_memory_compile"
        | "manager_quality_insight"
        | "user_coaching_report"
        | "daily_work_plan_generation"
        | "task_action_suggestion"
        | "manager_team_rhythm_insight"
        | "weekly_task_review"
        | "followup_prep_card"
        | "quote_prep_card"
        | "meeting_prep_card"
        | "task_brief_card"
        | "manager_attention_card"
        | "sales_morning_brief"
        | "manager_morning_brief"
        | "action_draft_generation"
        | "action_outcome_capture_assist"
        | "playbook_compile"
        | "outcome_effectiveness_review"
        | "personal_effectiveness_update"
        | "deal_room_command_summary"
        | "thread_summary"
        | "decision_support"
        | "intervention_recommendation"
        | "deal_playbook_mapping"
        | "email_draft_generation"
        | "meeting_agenda_generation"
        | "meeting_followup_summary"
        | "document_asset_summary"
        | "external_touchpoint_review"
        | "onboarding_recommendation"
        | "usage_health_summary"
        | "import_column_mapping_assist"
        | "import_review_summary"
        | "mobile_quick_capture_refine"
        | "mobile_brief_compact_summary"
        | "template_fit_recommendation"
        | "template_application_summary"
        | "industry_seed_customization"
        | "lead_qualification_assist"
        | "trial_conversion_review"
        | "growth_pipeline_summary"
        | "executive_brief_summary"
        | "customer_health_summary"
        | "automation_action_recommendation"
        | "retention_watch_review";
      ai_provider: "deepseek" | "openai" | "qwen" | "zhipu";
      ai_provider_scope: "deepseek" | "universal";
      ai_result_source: "provider" | "fallback";
      alert_rule_run_status: "running" | "completed" | "failed";
      ai_feedback_rating: "helpful" | "not_helpful" | "partially_helpful";
      report_type: "sales_daily" | "sales_weekly" | "manager_daily" | "manager_weekly";
      report_scope_type: "self" | "team" | "org";
      report_status: "generating" | "completed" | "failed";
      memory_item_type:
        | "customer_preference"
        | "communication_pattern"
        | "objection_pattern"
        | "tactic_pattern"
        | "followup_rhythm"
        | "risk_pattern"
        | "coaching_hint";
      memory_item_status: "active" | "hidden" | "rejected";
      quality_period_type: "daily" | "weekly" | "monthly";
      coaching_report_scope: "user" | "team";
      coaching_report_status: "generating" | "completed" | "failed";
      memory_feedback_type: "accurate" | "inaccurate" | "outdated" | "useful" | "not_useful";
      work_item_source_type: "alert" | "followup_due" | "ai_suggested" | "manager_assigned" | "report_generated" | "draft_confirmation" | "manual";
      work_item_type:
        | "followup_call"
        | "send_quote"
        | "confirm_decision_maker"
        | "schedule_demo"
        | "prepare_proposal"
        | "revive_stalled_deal"
        | "resolve_alert"
        | "confirm_capture_draft"
        | "review_customer"
        | "manager_checkin";
      work_priority_band: "low" | "medium" | "high" | "critical";
      work_item_status: "todo" | "in_progress" | "done" | "snoozed" | "cancelled";
      daily_plan_status: "draft" | "active" | "completed" | "archived";
      plan_time_block: "early_morning" | "morning" | "noon" | "afternoon" | "evening";
      task_action_type:
        | "created"
        | "reprioritized"
        | "started"
        | "completed"
        | "snoozed"
        | "cancelled"
        | "converted_to_followup"
        | "converted_to_alert_resolution"
        | "marked_blocked";
      work_agent_run_scope: "user_daily_plan" | "manager_team_plan" | "alert_reprioritization" | "weekly_task_review";
      work_agent_run_status: "queued" | "running" | "completed" | "failed";
      prep_card_type: "followup_prep" | "quote_prep" | "meeting_prep" | "task_brief" | "manager_attention";
      prep_card_status: "draft" | "ready" | "stale" | "archived";
      morning_brief_type: "sales_morning" | "manager_morning";
      morning_brief_status: "generating" | "completed" | "failed";
      content_draft_type:
        | "followup_message"
        | "quote_explanation"
        | "meeting_opening"
        | "meeting_summary"
        | "manager_checkin_note"
        | "internal_update";
      content_draft_status: "draft" | "adopted" | "discarded" | "archived";
      prep_feedback_target_type: "prep_card" | "content_draft" | "morning_brief";
      prep_feedback_type: "useful" | "not_useful" | "inaccurate" | "outdated" | "adopted";
      action_outcome_type: "followup_result" | "quote_result" | "meeting_result" | "task_result" | "manager_intervention_result";
      action_outcome_status: "positive_progress" | "neutral" | "stalled" | "risk_increased" | "closed_won" | "closed_lost";
      action_outcome_sentiment_shift: "improved" | "unchanged" | "worsened" | "unknown";
      action_outcome_usefulness_rating: "helpful" | "somewhat_helpful" | "not_helpful" | "unknown";
      suggestion_target_type: "prep_card" | "content_draft" | "task_action_suggestion" | "morning_brief";
      suggestion_adoption_type: "viewed" | "copied" | "edited" | "adopted" | "dismissed" | "partially_used";
      suggestion_adoption_context: "before_followup" | "before_quote" | "before_meeting" | "during_task_execution" | "after_review";
      playbook_scope_type: "org" | "team" | "user";
      playbook_type: "objection_handling" | "customer_segment" | "quote_strategy" | "meeting_strategy" | "followup_rhythm" | "risk_recovery";
      playbook_status: "active" | "draft" | "archived";
      outcome_review_scope: "user" | "team" | "org";
      outcome_review_status: "generating" | "completed" | "failed";
      playbook_feedback_type: "useful" | "not_useful" | "outdated" | "inaccurate" | "adopted";
      deal_room_status: "active" | "watchlist" | "escalated" | "blocked" | "won" | "lost" | "archived";
      deal_room_priority_band: "normal" | "important" | "strategic" | "critical";
      collaboration_thread_type: "strategy" | "blocker" | "quote_review" | "next_step" | "risk_discussion" | "manager_intervention" | "playbook_application";
      collaboration_thread_status: "open" | "resolved" | "archived";
      collaboration_message_type: "comment" | "decision_note" | "ai_summary" | "system_event";
      decision_type: "quote_strategy" | "discount_exception" | "trial_offer" | "manager_intervention" | "resource_support" | "contract_risk" | "stage_commitment";
      decision_status: "proposed" | "approved" | "rejected" | "superseded" | "completed";
      deal_participant_role: "owner" | "collaborator" | "manager" | "reviewer" | "observer";
      deal_checkpoint_type: "qualification" | "need_confirmed" | "proposal_sent" | "quote_sent" | "decision_maker_confirmed" | "budget_confirmed" | "trial_started" | "contract_review" | "closing";
      deal_checkpoint_status: "pending" | "completed" | "blocked" | "skipped";
      intervention_request_type: "manager_join_call" | "pricing_support" | "proposal_review" | "objection_help" | "contract_support" | "executive_escalation";
      intervention_priority_band: "low" | "medium" | "high" | "critical";
      intervention_request_status: "open" | "accepted" | "declined" | "completed" | "expired";
      external_provider_type: "email" | "calendar" | "storage";
      external_provider_name: "gmail" | "outlook" | "google_calendar" | "google_drive" | "dropbox" | "manual_upload";
      external_connection_status: "connected" | "disconnected" | "error";
      email_thread_status: "open" | "waiting_reply" | "replied" | "archived";
      email_sentiment_hint: "positive" | "neutral" | "negative" | "unknown";
      email_message_direction: "inbound" | "outbound" | "draft";
      email_message_status: "draft" | "sent" | "received" | "failed";
      email_message_source_type: "imported" | "manual" | "ai_generated";
      calendar_event_type: "customer_meeting" | "demo" | "proposal_review" | "internal_strategy" | "manager_intervention";
      calendar_meeting_status: "scheduled" | "completed" | "cancelled" | "no_show";
      document_asset_source_type: "upload" | "email_attachment" | "generated" | "imported";
      document_asset_type: "proposal" | "quote" | "contract_draft" | "meeting_note" | "case_study" | "product_material" | "other";
      touchpoint_type: "email" | "meeting" | "document";
      touchpoint_event_type:
        | "email_received"
        | "email_sent"
        | "draft_created"
        | "meeting_scheduled"
        | "meeting_completed"
        | "document_uploaded"
        | "document_reviewed"
        | "attachment_extracted";
      org_member_role: "owner" | "admin" | "manager" | "sales" | "viewer";
      org_seat_status: "invited" | "active" | "suspended" | "removed";
      org_invite_status: "pending" | "accepted" | "expired" | "revoked";
      org_feature_key:
        | "ai_auto_analysis"
        | "ai_auto_planning"
        | "ai_morning_brief"
        | "ai_deal_command"
        | "external_touchpoints"
        | "prep_cards"
        | "playbooks"
        | "manager_quality_view"
        | "outcome_learning"
        | "demo_seed_tools";
      org_ai_fallback_mode: "strict_provider_first" | "provider_then_rules" | "rules_only";
      org_usage_scope: "daily" | "monthly";
      org_plan_tier: "demo" | "trial" | "starter" | "growth" | "enterprise";
      org_plan_status: "active" | "paused" | "expired";
      onboarding_run_type: "first_time_setup" | "demo_seed" | "trial_bootstrap" | "reinitialize_demo";
      onboarding_run_status: "queued" | "running" | "completed" | "failed";
      import_type: "customers" | "opportunities" | "followups" | "mixed";
      import_source_type: "csv" | "xlsx" | "manual_table" | "demo_bootstrap";
      import_job_status: "uploaded" | "parsing" | "mapping" | "validating" | "preview_ready" | "importing" | "completed" | "failed" | "cancelled";
      import_row_status: "pending" | "valid" | "invalid" | "duplicate_candidate" | "merge_candidate" | "imported" | "skipped" | "failed";
      import_merge_resolution: "create_new" | "merge_existing" | "skip";
      import_entity_type: "customer" | "opportunity" | "followup" | "mixed";
      dedupe_resolution_status: "pending" | "confirmed" | "ignored";
      dedupe_resolution_action: "create_new" | "merge" | "skip";
      import_audit_event_type:
        | "uploaded"
        | "parsed"
        | "mapping_saved"
        | "validation_run"
        | "dedupe_reviewed"
        | "import_started"
        | "row_imported"
        | "row_failed"
        | "completed"
        | "cancelled";
      mobile_draft_type: "capture" | "outcome" | "email_draft" | "touchpoint_note";
      mobile_draft_sync_status: "pending" | "synced" | "failed" | "discarded";
      mobile_install_type: "browser" | "pwa";
      offline_action_type: "create_capture_draft" | "create_outcome_draft" | "save_email_draft" | "quick_complete_task" | "snooze_task";
      offline_action_queue_status: "queued" | "processing" | "done" | "failed";
      business_event_type: "followup_completed" | "deal_created" | "deal_won" | "deal_lost" | "stage_changed" | "risk_increased" | "health_declined" | "alert_triggered" | "task_completed";
      business_event_entity: "customer" | "opportunity" | "deal_room" | "followup" | "alert" | "work_item";
      business_event_severity: "low" | "medium" | "high";
      business_event_status: "recorded" | "processed" | "ignored";
      customer_lifecycle: "onboarding" | "active" | "at_risk" | "churned";
      customer_health_band: "healthy" | "watch" | "at_risk" | "critical";
      automation_rule_scope: "org" | "team" | "user";
      automation_trigger_type: "followup_overdue" | "deal_stalled" | "health_declined" | "no_activity" | "schedule";
      automation_rule_severity: "low" | "medium" | "high" | "critical";
      executive_brief_type: "sales_performance" | "team_performance" | "pipeline_review" | "retention_watch" | "growth_opportunity";
      executive_brief_status: "draft" | "published" | "archived";
    };
  };
}
