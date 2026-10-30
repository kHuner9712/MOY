'use client';

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
import { Field, Input, Textarea } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { toast } from '@/components/ui/toast';
import { QUERY_KEYS, useApiMutation } from '@/hooks/use-api';
import { knowledgeExtApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import type { ChunkPolicy, KnowledgeBaseDetail, Visibility } from '@/lib/types';

/** 可见范围选项（业务语言） */
const VISIBILITY_OPTIONS: Array<{ value: Visibility; label: string }> = [
  { value: 'organization', label: '企业可见（所有成员都能提问）' },
  { value: 'restricted', label: '指定成员可见（按授权名单）' },
  { value: 'private', label: '仅自己可见' },
];

/** 内容切分精细度选项（不暴露任何处理参数） */
const CHUNK_POLICY_OPTIONS: Array<{ value: ChunkPolicy; label: string }> = [
  { value: 'default', label: '标准（处理更快，适合手册与说明文档）' },
  { value: 'fine', label: '精细（条款定位更准，适合制度与合同）' },
];

/** 名称长度上限（与后端 1-128 一致） */
const NAME_MAX_LENGTH = 128;

/** 描述长度上限（与后端 ≤1024 一致） */
const DESCRIPTION_MAX_LENGTH = 1024;

/** 知识库设置弹窗属性 */
export interface BaseSettingsDialogProps {
  /** 是否打开 */
  open: boolean;
  /** 开关回调 */
  onOpenChange: (open: boolean) => void;
  /** 当前知识库信息 */
  base: KnowledgeBaseDetail;
  /** 是否有修改权限（无权限时按钮置灰并说明） */
  canEdit: boolean;
}

/**
 * 知识库设置弹窗（KB1）。
 *
 * 校验策略：名称为空在**前端先拦一次**（即时反馈，不发请求）；
 * 其余规则（长度、枚举）交给后端，`20001` 会以业务语言文案回显，
 * 前端不做重复的规则复制，避免前后端口径漂移。
 *
 * @param props - 组件属性
 * @returns 设置弹窗
 */
export function BaseSettingsDialog({
  open,
  onOpenChange,
  base,
  canEdit,
}: BaseSettingsDialogProps): React.JSX.Element {
  const [name, setName] = React.useState<string>(base.name);
  const [description, setDescription] = React.useState<string>(base.description);
  const [visibility, setVisibility] = React.useState<Visibility>(base.visibility);
  const [chunkPolicy, setChunkPolicy] = React.useState<ChunkPolicy>(base.chunk_policy ?? 'default');
  const [nameError, setNameError] = React.useState<string>('');
  /** 是否已用最新数据回填过（打开期间后台刷新不得覆盖用户正在编辑的内容） */
  const initializedRef = React.useRef<boolean>(false);

  React.useEffect(() => {
    if (!open) {
      initializedRef.current = false;
      return;
    }
    if (initializedRef.current) return;
    initializedRef.current = true;
    setName(base.name);
    setDescription(base.description);
    setVisibility(base.visibility);
    setChunkPolicy(base.chunk_policy ?? 'default');
    setNameError('');
  }, [open, base]);

  const saveMutation = useApiMutation(
    (variables: {
      name: string;
      description: string;
      visibility: Visibility;
      chunkPolicy: ChunkPolicy;
    }) =>
      knowledgeExtApi.updateBase(base.base_id, {
        name: variables.name,
        description: variables.description,
        visibility: variables.visibility,
        chunk_policy: variables.chunkPolicy,
      }),
    {
      invalidateKeys: [QUERY_KEYS.knowledgeBase(base.base_id), QUERY_KEYS.knowledgeBases()],
      onSuccess: () => {
        toast.success('知识库设置已保存');
        onOpenChange(false);
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  /** 提交保存 */
  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) {
      setNameError('请填写知识库名称');
      return;
    }
    setNameError('');
    saveMutation.mutate({
      name: trimmedName,
      description: description.trim(),
      visibility,
      chunkPolicy,
    });
  };

  return (
    <Dialog open={open} onOpenChange={saveMutation.isPending ? undefined : onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>知识库设置</DialogTitle>
          <DialogDescription>
            名称与可见范围保存后立即生效；内容切分精细度只影响之后上传的文档。
          </DialogDescription>
        </DialogHeader>

        <form className="space-y-4" onSubmit={handleSubmit}>
          <Field label="名称" htmlFor="base-name" required error={nameError}>
            <Input
              id="base-name"
              value={name}
              maxLength={NAME_MAX_LENGTH}
              disabled={!canEdit || saveMutation.isPending}
              invalid={Boolean(nameError)}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：人事制度库"
            />
          </Field>

          <Field
            label="描述"
            htmlFor="base-description"
            hint={`最多 ${DESCRIPTION_MAX_LENGTH} 字，用于在列表里说明这个库放什么资料`}
          >
            <Textarea
              id="base-description"
              value={description}
              rows={3}
              maxLength={DESCRIPTION_MAX_LENGTH}
              disabled={!canEdit || saveMutation.isPending}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="例如：员工手册、考勤与假期制度"
            />
          </Field>

          <Field label="可见范围" htmlFor="base-visibility">
            <Select
              id="base-visibility"
              value={visibility}
              disabled={!canEdit || saveMutation.isPending}
              onChange={(event) => setVisibility(event.target.value as Visibility)}
              options={VISIBILITY_OPTIONS}
            />
          </Field>

          <Field
            label="内容切分精细度"
            htmlFor="base-chunk-policy"
            hint="精细档位处理稍慢，但长文档里的条款定位更准"
          >
            <Select
              id="base-chunk-policy"
              value={chunkPolicy}
              disabled={!canEdit || saveMutation.isPending}
              onChange={(event) => setChunkPolicy(event.target.value as ChunkPolicy)}
              options={CHUNK_POLICY_OPTIONS}
            />
          </Field>

          <DialogFooter className="gap-2 pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={saveMutation.isPending}
            >
              取消
            </Button>
            <Button type="submit" disabled={!canEdit || saveMutation.isPending}>
              {saveMutation.isPending ? '保存中…' : '保存'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
