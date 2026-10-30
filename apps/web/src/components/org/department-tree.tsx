'use client';

import { ChevronRight, FolderTree, Plus } from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Tooltip } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { DEPARTMENT_MAX_LEVEL, type DepartmentNode } from '@/lib/types';

/** 层级上限提示（第 6 层不允许创建） */
const LEVEL_LIMIT_HINT = `组织层级最多 ${DEPARTMENT_MAX_LEVEL} 层，请调整后重试`;

/** 拖拽的数据格式标识 */
const DRAG_MIME = 'application/x-org-department';

/** 部门树属性 */
export interface DepartmentTreeProps {
  /** 部门树（ID1 返回，最多 5 层） */
  departments: DepartmentNode[];
  /** 当前选中部门 ID */
  selectedId: string;
  /** 选中回调 */
  onSelect: (department: DepartmentNode) => void;
  /** 新建子部门回调（父节点已满 5 层时不允许触发） */
  onCreateChild: (parent: DepartmentNode) => void;
  /** 拖拽移动回调：把 `departmentId` 挂到 `targetParentId` 下 */
  onMove: (departmentId: string, targetParentId: string) => void;
  /** 是否有组织架构管理权限 */
  canManage: boolean;
  /** 是否正在执行移动（期间禁用拖拽，避免连点产生并发移动） */
  moving?: boolean;
}

/** 单节点渲染属性 */
interface TreeNodeProps {
  node: DepartmentNode;
  depth: number;
  selectedId: string;
  expandedIds: ReadonlySet<string>;
  draggedId: string;
  dropTargetId: string;
  canManage: boolean;
  moving: boolean;
  onToggle: (departmentId: string) => void;
  onSelect: (department: DepartmentNode) => void;
  onCreateChild: (parent: DepartmentNode) => void;
  onDragStart: (departmentId: string) => void;
  onDragEnd: () => void;
  onDropOn: (targetParentId: string) => void;
  onDropTargetChange: (targetId: string) => void;
}

/**
 * 单个部门节点（递归渲染子树）。
 *
 * @param props - 节点属性
 * @returns 节点与子树
 */
function DepartmentTreeNode({
  node,
  depth,
  selectedId,
  expandedIds,
  draggedId,
  dropTargetId,
  canManage,
  moving,
  onToggle,
  onSelect,
  onCreateChild,
  onDragStart,
  onDragEnd,
  onDropOn,
  onDropTargetChange,
}: TreeNodeProps): React.JSX.Element {
  const hasChildren = node.children.length > 0;
  const expanded = expandedIds.has(node.department_id);
  const selected = node.department_id === selectedId;
  const dropTarget = node.department_id === dropTargetId && draggedId !== node.department_id;
  const reachedLimit = node.level >= DEPARTMENT_MAX_LEVEL;

  return (
    <li>
      <div
        role="treeitem"
        aria-selected={selected}
        aria-expanded={hasChildren ? expanded : undefined}
        aria-level={node.level}
        tabIndex={0}
        draggable={canManage && !moving}
        onDragStart={(event) => {
          event.dataTransfer.setData(DRAG_MIME, node.department_id);
          event.dataTransfer.effectAllowed = 'move';
          onDragStart(node.department_id);
        }}
        onDragEnd={onDragEnd}
        onDragOver={(event) => {
          if (!canManage || moving) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = 'move';
          onDropTargetChange(node.department_id);
        }}
        onDragLeave={() => onDropTargetChange('')}
        onDrop={(event) => {
          if (!canManage || moving) return;
          event.preventDefault();
          onDropOn(node.department_id);
        }}
        onClick={() => onSelect(node)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onSelect(node);
          }
        }}
        style={{ paddingLeft: `${depth * 16}px` }}
        className={cn(
          'group flex cursor-pointer items-center gap-1 rounded-md py-1.5 pr-2 text-sm transition-colors',
          selected ? 'bg-primary/10 font-medium text-primary' : 'hover:bg-accent',
          dropTarget && 'ring-2 ring-primary ring-inset',
        )}
      >
        <button
          type="button"
          aria-label={expanded ? '收起下级部门' : '展开下级部门'}
          aria-hidden={!hasChildren}
          tabIndex={hasChildren ? 0 : -1}
          onClick={(event) => {
            event.stopPropagation();
            if (hasChildren) onToggle(node.department_id);
          }}
          className={cn(
            'flex h-5 w-5 items-center justify-center rounded text-muted-foreground',
            hasChildren ? 'hover:bg-background' : 'invisible',
          )}
        >
          <ChevronRight
            className={cn('h-3.5 w-3.5 transition-transform', expanded && 'rotate-90')}
          />
        </button>

        <FolderTree aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate">{node.name}</span>
        <span className="ml-1 shrink-0 text-xs text-muted-foreground tabular-nums">
          {node.member_count}
        </span>

        <Tooltip content={LEVEL_LIMIT_HINT} disabled={!reachedLimit}>
          <span className="ml-auto">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`在 ${node.name} 下新建部门`}
              disabled={!canManage || reachedLimit}
              onClick={(event) => {
                event.stopPropagation();
                onCreateChild(node);
              }}
              className="h-6 w-6 text-muted-foreground"
            >
              <Plus className="h-3.5 w-3.5" />
            </Button>
          </span>
        </Tooltip>
      </div>

      {hasChildren && expanded ? (
        <ul role="group">
          {node.children.map((child) => (
            <DepartmentTreeNode
              key={child.department_id}
              node={child}
              depth={depth + 1}
              selectedId={selectedId}
              expandedIds={expandedIds}
              draggedId={draggedId}
              dropTargetId={dropTargetId}
              canManage={canManage}
              moving={moving}
              onToggle={onToggle}
              onSelect={onSelect}
              onCreateChild={onCreateChild}
              onDragStart={onDragStart}
              onDragEnd={onDragEnd}
              onDropOn={onDropOn}
              onDropTargetChange={onDropTargetChange}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** 部门树节点（递归渲染）
 *
 * 说明：节点标识统一用 `department_id`，不额外引入内部键，
 * 这样同一份数据结构既能渲染树、也能直接回传给接口。
 */

/**
 * 部门层级树（ID1）。
 *
 * 交互约定：
 * - 最多 5 层；已到第 5 层的节点「新建」按钮置灰，hover 说明原因；
 * - 支持**拖拽移动**：把节点拖到目标部门上即成为其子部门，由页面调用 ID3 落库；
 *   移动成功后页面失效 `['identity','departments',orgId]`，刷新后层级依然生效；
 * - 键盘可达：节点可聚焦，Enter/空格选中，展开按钮独立可点。
 *
 * @param props - 组件属性
 * @returns 部门树
 */
export function DepartmentTree({
  departments,
  selectedId,
  onSelect,
  onCreateChild,
  onMove,
  canManage,
  moving = false,
}: DepartmentTreeProps): React.JSX.Element {
  const [expandedIds, setExpandedIds] = React.useState<ReadonlySet<string>>(new Set());
  const [draggedId, setDraggedId] = React.useState<string>('');
  const [dropTargetId, setDropTargetId] = React.useState<string>('');

  /** 首次拿到数据时展开第一层，避免用户以为树是空的 */
  React.useEffect(() => {
    setExpandedIds((prev) => {
      if (prev.size > 0) return prev;
      const next = new Set<string>();
      for (const node of departments) {
        if (node.children.length > 0) next.add(node.department_id);
      }
      return next;
    });
  }, [departments]);

  const toggle = React.useCallback((departmentId: string): void => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(departmentId)) next.delete(departmentId);
      else next.add(departmentId);
      return next;
    });
  }, []);

  const finishDrag = React.useCallback((): void => {
    setDraggedId('');
    setDropTargetId('');
  }, []);

  const handleDropOn = React.useCallback(
    (targetParentId: string): void => {
      if (!draggedId || draggedId === targetParentId) {
        finishDrag();
        return;
      }
      onMove(draggedId, targetParentId);
      finishDrag();
    },
    [draggedId, finishDrag, onMove],
  );

  return (
    <ul role="tree" aria-label="部门层级" className="space-y-0.5">
      {departments.map((node) => (
        <DepartmentTreeNode
          key={node.department_id}
          node={node}
          depth={0}
          selectedId={selectedId}
          expandedIds={expandedIds}
          draggedId={draggedId}
          dropTargetId={dropTargetId}
          canManage={canManage}
          moving={moving}
          onToggle={toggle}
          onSelect={onSelect}
          onCreateChild={onCreateChild}
          onDragStart={setDraggedId}
          onDragEnd={finishDrag}
          onDropOn={handleDropOn}
          onDropTargetChange={setDropTargetId}
        />
      ))}
    </ul>
  );
}
