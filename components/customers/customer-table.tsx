"use client";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { customerStageLabel, riskTone } from "@/lib/constants";
import { formatDateTime } from "@/lib/format";
import type { Customer } from "@/types/customer";
import Link from "next/link";

export function CustomerTable({ customers }: { customers: Customer[] }): JSX.Element {
  return (
    <div className="rounded-xl border bg-white">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>客户</TableHead>
            <TableHead>阶段</TableHead>
            <TableHead>负责人</TableHead>
            <TableHead>最近跟进</TableHead>
            <TableHead>下次跟进</TableHead>
            <TableHead>成交概率</TableHead>
            <TableHead>风险</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {customers.map((customer) => (
            <TableRow key={customer.id}>
              <TableCell>
                <Link href={`/customers/${customer.id}`} className="group block">
                  <p className="text-sm font-semibold text-slate-900 group-hover:text-sky-700">{customer.companyName}</p>
                  <p className="text-xs text-muted-foreground">
                    {customer.contactName} · {customer.phone}
                  </p>
                </Link>
              </TableCell>
              <TableCell>{customerStageLabel[customer.stage]}</TableCell>
              <TableCell>{customer.ownerName}</TableCell>
              <TableCell>{formatDateTime(customer.lastFollowupAt)}</TableCell>
              <TableCell>{formatDateTime(customer.nextFollowupAt)}</TableCell>
              <TableCell>{customer.winProbability}%</TableCell>
              <TableCell>
                <Badge variant={riskTone[customer.riskLevel]}>
                  {customer.riskLevel === "high" ? "高风险" : customer.riskLevel === "medium" ? "中风险" : "低风险"}
                </Badge>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
