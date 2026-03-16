export function formatDateTime(isoText: string): string {
  return new Date(isoText).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export function formatDate(isoText: string): string {
  return new Date(isoText).toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  });
}

export function formatCurrency(value: number): string {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 0
  }).format(value);
}

export function getRelativeDaysLabel(days: number): string {
  if (days <= 0) return "今日有推进";
  if (days <= 3) return `${days} 天未推进`;
  if (days <= 7) return `${days} 天停滞`;
  return `${days} 天高风险停滞`;
}
