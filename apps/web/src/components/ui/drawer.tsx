'use client';

import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import * as React from 'react';

import { cn } from '@/lib/utils';

/** 抽屉根节点（控制开关） */
const Drawer = DialogPrimitive.Root;

/** 抽屉触发按钮 */
const DrawerTrigger = DialogPrimitive.Trigger;

/** 抽屉关闭按钮 */
const DrawerClose = DialogPrimitive.Close;

/** 抽屉宽度 */
export type DrawerSize = 'md' | 'lg' | 'xl';

/** 抽屉宽度映射：中号即设计约定的 480 */
const DRAWER_SIZE_CLASS: Record<DrawerSize, string> = {
  md: 'sm:max-w-[480px]',
  lg: 'sm:max-w-[720px]',
  xl: 'sm:max-w-[960px]',
};

/** 抽屉内容属性 */
export interface DrawerContentProps
  extends React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> {
  /** 宽度，默认 `md`（480） */
  size?: DrawerSize;
  /** 是否展示右上角关闭按钮，默认 true */
  showClose?: boolean;
}

/**
 * 抽屉内容：从右侧滑入，宽度默认 480。
 *
 * 用于「查看/编辑详情」等不打断当前列表上下文的操作。
 *
 * @param props - 组件属性
 * @returns 抽屉内容元素
 */
const DrawerContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  DrawerContentProps
>(({ className, children, size = 'md', showClose = true, ...props }, ref) => (
  <DialogPrimitive.Portal>
    <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/60 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        'fixed inset-y-0 right-0 z-50 flex h-full w-full flex-col border-l bg-background shadow-lg duration-200 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right',
        DRAWER_SIZE_CLASS[size],
        className,
      )}
      {...props}
    >
      {children}
      {showClose ? (
        <DialogPrimitive.Close className="absolute right-4 top-4 rounded-sm opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2">
          <X className="h-4 w-4" />
          <span className="sr-only">关闭</span>
        </DialogPrimitive.Close>
      ) : null}
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>
));
DrawerContent.displayName = 'DrawerContent';

/**
 * 抽屉头部（标题 + 说明，固定在顶部不随内容滚动）。
 *
 * @param props - 组件属性
 * @returns 头部元素
 */
const DrawerHeader = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element => (
  <div className={cn('space-y-1.5 border-b px-6 py-4 pr-12', className)} {...props} />
);
DrawerHeader.displayName = 'DrawerHeader';

/**
 * 抽屉主体（可滚动区域）。
 *
 * @param props - 组件属性
 * @returns 主体元素
 */
const DrawerBody = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element => (
  <div className={cn('flex-1 overflow-y-auto px-6 py-4', className)} {...props} />
);
DrawerBody.displayName = 'DrawerBody';

/**
 * 抽屉底部操作区。
 *
 * @param props - 组件属性
 * @returns 底部元素
 */
const DrawerFooter = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element => (
  <div
    className={cn(
      'flex flex-col-reverse gap-2 border-t px-6 py-4 sm:flex-row sm:justify-end',
      className,
    )}
    {...props}
  />
);
DrawerFooter.displayName = 'DrawerFooter';

/**
 * 抽屉标题（无障碍必填：抽屉打开时焦点会落到内容区，需要标题朗读）。
 *
 * @param props - 组件属性
 * @returns 标题元素
 */
const DrawerTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn('text-base font-semibold leading-none tracking-tight', className)}
    {...props}
  />
));
DrawerTitle.displayName = 'DrawerTitle';

/**
 * 抽屉说明文字。
 *
 * @param props - 组件属性
 * @returns 说明元素
 */
const DrawerDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn('text-sm text-muted-foreground', className)}
    {...props}
  />
));
DrawerDescription.displayName = 'DrawerDescription';

export {
  Drawer,
  DrawerBody,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
};
