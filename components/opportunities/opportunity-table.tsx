import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { opportunityStageLabel, riskTone } from "@/lib/constants";
import { formatCurrency, formatDate } from "@/lib/format";
import type { Opportunity } from "@/types/opportunity";

export function OpportunityTable({ items }: { items: Opportunity[] }): JSX.Element {
  return (
    <div className="rounded-xl border bg-white">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Opportunity</TableHead>
            <TableHead>Customer</TableHead>
            <TableHead>Amount</TableHead>
            <TableHead>Stage</TableHead>
            <TableHead>Owner</TableHead>
            <TableHead>Last Progress</TableHead>
            <TableHead>Risk</TableHead>
            <TableHead>Expected Close</TableHead>
            <TableHead>Deal Room</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow key={item.id}>
              <TableCell>
                <p className="font-semibold text-slate-900">{item.name}</p>
              </TableCell>
              <TableCell>{item.customerName}</TableCell>
              <TableCell>{formatCurrency(item.expectedAmount)}</TableCell>
              <TableCell>{opportunityStageLabel[item.stage]}</TableCell>
              <TableCell>{item.ownerName}</TableCell>
              <TableCell>{formatDate(item.lastProgressAt)}</TableCell>
              <TableCell>
                <Badge variant={riskTone[item.riskLevel]}>
                  {item.riskLevel === "high" ? "High" : item.riskLevel === "medium" ? "Medium" : "Low"}
                </Badge>
              </TableCell>
              <TableCell>{formatDate(item.closeDate)}</TableCell>
              <TableCell>
                <Button asChild size="sm" variant="outline">
                  <Link href={`/customers/${item.customerId}`}>Open</Link>
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

