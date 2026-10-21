'use client';

import { AlertTriangle } from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { cn } from '@/lib/utils';

/** 确认弹窗变体 */
export type ConfirmDialogVariant = 'default' | 'danger';

/** 二次确认弹窗属性 */
export interface ConfirmDialogProps {
  /** 是否打开 */
  open: boolean;
  /** 开关回调 */
  onOpenChange: (open: boolean) => void;
  /** 标题（业务语言，说清「要做什么」） */
  title: string;
  /** 说明（业务语言，说清「影响是什么」）；需要更丰富的内容时用 children */
  description?: string;
  /** 确认按钮文案，默认「确认」 */
  confirmLabel?: string;
  /** 取消按钮文案，默认「取消」 */
  cancelLabel?: string;
  /** 变体：`danger` 用于删除类不可逆操作 */
  variant?: ConfirmDialogVariant;
  /** 确认回调（支持异步，成功后自动关闭） */
  onConfirm: () => void | Promise<void>;
  /** 外部控制确认按钮的加载态 */
  loading?: boolean;
  /** 附加内容（如影响范围列表） */
  children?: React.ReactNode;
  className?: string;
}

/**
 * 二次确认弹窗：删除、停用、退出等不可逆操作统一走这里。
 *
 * 约定：`onConfirm` 内部自行用 toast 提示结果；抛错时弹窗保持打开，
 * 便于用户在同一上下文里重试。
 *
 * @param props - 组件属性
 * @returns 确认弹窗元素
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = '确认',
  cancelLabel = '取消',
  variant = 'default',
  onConfirm,
  loading = false,
  children,
  className,
}: ConfirmDialogProps): React.JSX.Element {
  const [pending, setPending] = React.useState<boolean>(false);
  const busy = pending || loading;
  const isDanger = variant === 'danger';

  /** 执行确认动作 */
  const handleConfirm = async (): Promise<void> => {
    if (busy) return;
    setPending(true);
    try {
      await onConfirm();
      onOpenChange(false);
    } catch (error) {
      // 具体原因由调用方通过 toast 以业务语言提示；这里只保证弹窗不被误关
      console.error('[confirm-dialog] 确认操作未完成', error);
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={busy ? undefined : onOpenChange}>
      <DialogContent className={cn('max-w-md', className)}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {isDanger ? (
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-destructive/10">
                <AlertTriangle aria-hidden="true" className="h-4 w-4 text-destructive" />
              </span>
            ) : null}
            <span>{title}</span>
          </DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>
        {children ? <div className="text-sm text-muted-foreground">{children}</div> : null}
        <DialogFooter className="gap-2 pt-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            {cancelLabel}
          </Button>
          <Button
            type="button"
            variant={isDanger ? 'destructive' : 'default'}
            onClick={() => {
              void handleConfirm();
            }}
            disabled={busy}
          >
            {busy ? '处理中…' : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
