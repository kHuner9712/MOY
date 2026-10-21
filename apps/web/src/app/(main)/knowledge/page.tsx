'use client';

import { useEffect, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { Input } from '@/components/ui/input';
import { toast } from '@/components/ui/toast';
import { useUpload } from '@/hooks/use-upload';
import { knowledgeApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import type { KnowledgeBase } from '@/lib/types';

/** 索引状态展示映射（业务语言） */
const INDEX_STATUS_TEXT: Record<string, { label: string; variant: 'success' | 'warning' | 'destructive' }> = {
  completed: { label: '可用', variant: 'success' },
  processing: { label: '处理中', variant: 'warning' },
  failed: { label: '处理失败', variant: 'destructive' },
};

/**
 * 知识库页面：列表 + 创建 + 文档上传。
 *
 * @returns 知识库页面元素
 */
export default function KnowledgePage(): JSX.Element {
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState('');
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);
  const [activeBaseId, setActiveBaseId] = useState('');
  const upload = useUpload();

  /** 加载知识库列表 */
  const load = async (): Promise<void> => {
    setLoading(true);
    setErrorMessage('');
    try {
      const page = await knowledgeApi.listBases({ page: 1, page_size: 50 });
      setBases(page.items);
      if (page.items.length > 0 && !activeBaseId) {
        setActiveBaseId(page.items[0]?.base_id ?? '');
      }
    } catch (error) {
      setErrorMessage(toBusinessMessage(error));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** 创建知识库 */
  const handleCreate = async (): Promise<void> => {
    const trimmed = name.trim();
    if (!trimmed) {
      toast.error('请填写知识库名称');
      return;
    }
    setCreating(true);
    try {
      await knowledgeApi.createBase({ name: trimmed });
      setName('');
      toast.success('知识库已创建');
      await load();
    } catch (error) {
      toast.error(toBusinessMessage(error));
    } finally {
      setCreating(false);
    }
  };

  /** 选择文件并上传 */
  const handleFileChange = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ): Promise<void> => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file || !activeBaseId) {
      return;
    }
    await upload.upload(activeBaseId, file);
    if (upload.errorMessage) {
      toast.error(upload.errorMessage);
    } else if (upload.documentId) {
      toast.success('文档已上传，正在建立索引');
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>新建知识库</CardTitle>
          <CardDescription>
            知识库用于沉淀团队资料，回答时会自动标注引用来源
          </CardDescription>
        </CardHeader>
        <CardContent className="flex gap-2">
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：人事制度库"
            maxLength={128}
          />
          <Button onClick={() => void handleCreate()} disabled={creating}>
            创建
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>上传文档</CardTitle>
          <CardDescription>
            支持 PDF、Word、Excel、Markdown、TXT，单个文件不超过 50MB
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input
            type="file"
            accept=".pdf,.docx,.xlsx,.md,.txt"
            onChange={(event) => void handleFileChange(event)}
            disabled={!activeBaseId || upload.uploading}
          />
          {upload.fileName ? (
            <div className="space-y-1 text-sm">
              <p className="text-muted-foreground">{upload.fileName}</p>
              {upload.indexStatus === 'processing' ? (
                <>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full bg-primary transition-all"
                      style={{ width: `${upload.progress}%` }}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    正在建立索引，请稍候（{upload.progress}%）
                  </p>
                </>
              ) : (
                <Badge
                  variant={
                    INDEX_STATUS_TEXT[upload.indexStatus]?.variant ?? 'warning'
                  }
                >
                  {INDEX_STATUS_TEXT[upload.indexStatus]?.label ?? '处理中'}
                </Badge>
              )}
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>我的知识库</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <LoadingState />
          ) : errorMessage ? (
            <ErrorState message={errorMessage} onRetry={() => void load()} />
          ) : bases.length === 0 ? (
            <EmptyState
              title="还没有知识库"
              description="创建第一个知识库并上传资料，即可开始文档问答"
            />
          ) : (
            <ul className="divide-y">
              {bases.map((item) => (
                <li
                  key={item.base_id}
                  className="flex cursor-pointer items-center justify-between py-3"
                  onClick={() => setActiveBaseId(item.base_id)}
                >
                  <div>
                    <p className="text-sm font-medium">{item.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {item.description || '暂无描述'}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {item.base_id === activeBaseId ? (
                      <Badge>当前选中</Badge>
                    ) : null}
                    <span className="text-xs text-muted-foreground">
                      {item.document_count} 个文档
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
