"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { customerStageLabel, riskLabel } from "@/lib/constants";
import type { CustomerStage, RiskLevel } from "@/types/customer";

export interface CustomerFilterState {
  search: string;
  ownerId: string;
  stage: CustomerStage | "all";
  riskLevel: RiskLevel | "all";
  sortBy: "updatedAt" | "nextFollowupAt" | "winProbability";
  sortOrder: "asc" | "desc";
}

interface CustomerFiltersProps {
  value: CustomerFilterState;
  onChange: (next: CustomerFilterState) => void;
  ownerOptions: Array<{ id: string; name: string }>;
}

export function CustomerFilters({ value, onChange, ownerOptions }: CustomerFiltersProps): JSX.Element {
  return (
    <div className="rounded-xl border bg-white p-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
        <div className="xl:col-span-2">
          <Label htmlFor="search">搜索客户</Label>
          <Input
            id="search"
            value={value.search}
            onChange={(e) => onChange({ ...value, search: e.target.value })}
            placeholder="客户名 / 公司 / 电话 / 邮箱"
            className="mt-1"
          />
        </div>

        <div>
          <Label>负责人</Label>
          <Select value={value.ownerId} onValueChange={(ownerId) => onChange({ ...value, ownerId })}>
            <SelectTrigger className="mt-1">
              <SelectValue placeholder="全部负责人" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部负责人</SelectItem>
              {ownerOptions.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div>
          <Label>阶段</Label>
          <Select value={value.stage} onValueChange={(stage: CustomerFilterState["stage"]) => onChange({ ...value, stage })}>
            <SelectTrigger className="mt-1">
              <SelectValue placeholder="全部阶段" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部阶段</SelectItem>
              {Object.entries(customerStageLabel).map(([stage, label]) => (
                <SelectItem key={stage} value={stage}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div>
          <Label>风险等级</Label>
          <Select value={value.riskLevel} onValueChange={(riskLevel: CustomerFilterState["riskLevel"]) => onChange({ ...value, riskLevel })}>
            <SelectTrigger className="mt-1">
              <SelectValue placeholder="全部风险" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部风险</SelectItem>
              {Object.entries(riskLabel).map(([risk, label]) => (
                <SelectItem key={risk} value={risk}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div>
          <Label>排序</Label>
          <Select value={`${value.sortBy}:${value.sortOrder}`} onValueChange={(next) => {
            const [sortBy, sortOrder] = next.split(":") as [CustomerFilterState["sortBy"], CustomerFilterState["sortOrder"]];
            onChange({ ...value, sortBy, sortOrder });
          }}>
            <SelectTrigger className="mt-1">
              <SelectValue placeholder="选择排序" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="updatedAt:desc">最近更新时间 ↓</SelectItem>
              <SelectItem value="updatedAt:asc">最近更新时间 ↑</SelectItem>
              <SelectItem value="nextFollowupAt:asc">下次跟进时间 ↑</SelectItem>
              <SelectItem value="nextFollowupAt:desc">下次跟进时间 ↓</SelectItem>
              <SelectItem value="winProbability:desc">成交概率 ↓</SelectItem>
              <SelectItem value="winProbability:asc">成交概率 ↑</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
    </div>
  );
}
