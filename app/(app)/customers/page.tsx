"use client";

import { useAuth } from "@/components/auth/auth-provider";
import { CustomerFilters, type CustomerFilterState } from "@/components/customers/customer-filters";
import { CustomerTable } from "@/components/customers/customer-table";
import { useAppData } from "@/components/shared/app-data-provider";
import { PageHeader } from "@/components/shared/page-header";
import { StatCard } from "@/components/shared/stat-card";
import { applyCustomerQuery } from "@/services/customer-service";
import { AlertCircle, Layers3, UserRoundCheck, Users } from "lucide-react";
import { useMemo, useState } from "react";

export default function CustomersPage(): JSX.Element {
  const { user } = useAuth();
  const { customers, loading, error } = useAppData();

  const scopedCustomers = useMemo(() => {
    if (!user) return [];
    if (user.role === "manager") return customers;
    return customers.filter((item) => item.ownerId === user.id);
  }, [customers, user]);

  const ownerOptions = useMemo(
    () =>
      Array.from(new Map(customers.map((item) => [item.ownerId, { id: item.ownerId, name: item.ownerName }])).values()).sort((a, b) =>
        a.name.localeCompare(b.name, "zh-CN")
      ),
    [customers]
  );

  const [filters, setFilters] = useState<CustomerFilterState>({
    search: "",
    ownerId: user?.role === "manager" ? "all" : user?.id ?? "all",
    stage: "all",
    riskLevel: "all",
    sortBy: "updatedAt",
    sortOrder: "desc"
  });

  const filtered = useMemo(() => applyCustomerQuery(scopedCustomers, filters), [scopedCustomers, filters]);
  const highRiskCount = filtered.filter((item) => item.riskLevel === "high").length;
  const pendingCount = filtered.filter((item) => new Date(item.nextFollowupAt) <= new Date()).length;
  const activeCount = filtered.filter((item) => item.stage !== "won" && item.stage !== "lost").length;

  if (loading) {
    return <div className="text-sm text-muted-foreground">正在加载客户数据...</div>;
  }

  if (error) {
    return <div className="text-sm text-rose-600">客户数据加载失败：{error}</div>;
  }

  return (
    <div>
      <PageHeader title="客户管理" description="支持搜索、筛选和排序，快速定位客户推进状态。" />

      <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard title="客户总数" value={filtered.length} hint="当前筛选结果" icon={<Users className="h-4 w-4 text-sky-700" />} />
        <StatCard title="活跃推进中" value={activeCount} hint="未赢单/未丢单客户" icon={<UserRoundCheck className="h-4 w-4 text-sky-700" />} />
        <StatCard title="今日待跟进" value={pendingCount} hint="下次跟进时间已到" icon={<Layers3 className="h-4 w-4 text-amber-600" />} />
        <StatCard title="高风险客户" value={highRiskCount} hint="建议优先处理" icon={<AlertCircle className="h-4 w-4 text-rose-600" />} />
      </section>

      <div className="space-y-4">
        <CustomerFilters value={filters} onChange={setFilters} ownerOptions={ownerOptions} />
        <CustomerTable customers={filtered} />
      </div>
    </div>
  );
}
