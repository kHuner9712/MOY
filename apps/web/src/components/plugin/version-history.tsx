'use client';

import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { EmptyState } from '@/components/ui/feedback';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tooltip } from '@/components/ui/tooltip';
import { formatDate, orPlaceholder } from '@/lib/format';
import type { PluginVersionBrief } from '@/lib/types';

/** 版本历史属性 */
export interface VersionHistoryProps {
  /** PL2 返回的版本列表（只保留最近 2 个） */
  versions: PluginVersionBrief[];
  /** 已安装版本；未安装为空串 */
  installedVersion: string;
  /** 是否有可用更新 */
  hasUpdate: boolean;
  /** 升级到指定版本 */
  onUpgrade: (version: string) => void;
  /** 回退到上一版本 */
  onRollback: () => void;
  /** 是否升级中 */
  upgrading?: boolean;
  /** 是否回退中 */
  rollingBack?: boolean;
  /** 是否有管理权限 */
  canManage?: boolean;
}

/**
 * 版本历史与升降级操作（PL8 / PL9）。
 *
 * 口径：
 * - 只保留最近 2 个版本，因此「回退」只允许退一步；无上一版本时按钮置灰并说明；
 * - 文案统一用「回退」，不使用内部术语；
 * - 回退走二次确认（会改变正在使用的行为，属于需要确认的操作）。
 *
 * @param props - 组件属性
 * @returns 版本历史区块
 */
export function VersionHistory({
  versions,
  installedVersion,
  hasUpdate,
  onUpgrade,
  onRollback,
  upgrading = false,
  rollingBack = false,
  canManage = true,
}: VersionHistoryProps): React.JSX.Element {
  const [rollbackOpen, setRollbackOpen] = React.useState<boolean>(false);
  const busy = upgrading || rollingBack;

  if (versions.length === 0) {
    return (
      <EmptyState
        title="还没有版本信息"
        description="插件发布版本后会在这里展示，可用于升级或回退"
      />
    );
  }

  const latest = versions[0]?.version ?? '';
  const canRollback = Boolean(installedVersion) && versions.length >= 2;

  return (
    <div className="space-y-3">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>版本</TableHead>
            <TableHead>发布时间</TableHead>
            <TableHead>状态</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {versions.map((item) => {
            const isCurrent = item.version === installedVersion;
            const isLatest = item.version === latest;
            return (
              <TableRow key={item.version}>
                <TableCell className="font-mono text-sm">{item.version}</TableCell>
                <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                  {formatDate(item.published_at)}
                </TableCell>
                <TableCell>
                  {isCurrent ? (
                    <Badge variant="success">正在使用</Badge>
                  ) : isLatest ? (
                    <Badge variant="secondary">最新版本</Badge>
                  ) : (
                    <Badge variant="outline">可回退</Badge>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  {isLatest && hasUpdate && !isCurrent ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={busy || !canManage}
                      onClick={() => onUpgrade(item.version)}
                    >
                      升级到该版本
                    </Button>
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      {isCurrent ? '已是最新' : '—'}
                    </span>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      <div className="flex flex-wrap items-center gap-2">
        <Tooltip
          content={canRollback ? '只能回退到上一个版本' : '还没有可回退的上一版本'}
          disabled={canRollback}
        >
          <span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={!canRollback || busy || !canManage}
              onClick={() => setRollbackOpen(true)}
            >
              回退到上一版本
            </Button>
          </span>
        </Tooltip>
        <span className="text-xs text-muted-foreground">
          当前版本：{orPlaceholder(installedVersion, '未安装')}
        </span>
      </div>

      <ConfirmDialog
        open={rollbackOpen}
        onOpenChange={setRollbackOpen}
        title="回退到上一版本？"
        description="回退后新发起的任务将按旧版本执行；已完成的历史任务展示不受影响。"
        confirmLabel="确认回退"
        loading={rollingBack}
        onConfirm={async () => {
          await Promise.resolve(onRollback());
          setRollbackOpen(false);
        }}
      >
        <p>当前版本 {orPlaceholder(installedVersion)} 将回退到上一版本。</p>
      </ConfirmDialog>
    </div>
  );
}
