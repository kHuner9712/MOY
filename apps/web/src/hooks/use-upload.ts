'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { knowledgeApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import type { IndexStatus } from '@/lib/types';

/** 文档上传状态 */
export interface UploadState {
  /** 是否正在上传 */
  uploading: boolean;
  /** 正在上传的文件名 */
  fileName: string;
  /** 索引进度（0-100） */
  progress: number;
  /** 索引状态 */
  indexStatus: IndexStatus;
  /** 业务语言错误提示 */
  errorMessage: string;
  /** 上传成功后的文档 ID */
  documentId: string;
}

/** 初始上传状态 */
const INITIAL_UPLOAD_STATE: UploadState = {
  uploading: false,
  fileName: '',
  progress: 0,
  indexStatus: 'processing',
  errorMessage: '',
  documentId: '',
};

/** 允许上传的扩展名（与适配层白名单一致） */
const ALLOWED_EXTENSIONS = ['pdf', 'docx', 'xlsx', 'md', 'txt'];

/** 单文件上限（50MB，与适配层一致） */
const MAX_FILE_SIZE = 50 * 1024 * 1024;

/** 轮询间隔（毫秒） */
const POLL_INTERVAL_MS = 2_000;

/** `useUpload` 返回值 */
export interface UseUploadResult extends UploadState {
  /** 上传文档并轮询索引进度 */
  upload: (baseId: string, file: File) => Promise<void>;
  /** 重置状态 */
  reset: () => void;
}

/**
 * 文档上传 Hook（上传 → 异步索引 → 轮询进度）。
 *
 * 设计要点：
 * - 上传前本地做格式与大小预校验，减少无效请求；
 * - 索引为异步过程，按固定间隔轮询 R4 进度接口；
 * - 所有提示使用业务语言文案。
 *
 * @returns 上传状态与操作方法
 */
export function useUpload(): UseUploadResult {
  const [state, setState] = useState<UploadState>(INITIAL_UPLOAD_STATE);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearPolling = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => clearPolling, [clearPolling]);

  const reset = useCallback(() => {
    clearPolling();
    setState(INITIAL_UPLOAD_STATE);
  }, [clearPolling]);

  const upload = useCallback<UseUploadResult['upload']>(
    async (baseId, file) => {
      clearPolling();
      setState({ ...INITIAL_UPLOAD_STATE, uploading: true, fileName: file.name });

      const extension = file.name.includes('.')
        ? (file.name.split('.').pop() ?? '').toLowerCase()
        : '';
      if (!ALLOWED_EXTENSIONS.includes(extension)) {
        setState({
          ...INITIAL_UPLOAD_STATE,
          fileName: file.name,
          errorMessage: '暂不支持该格式的文档',
        });
        return;
      }
      if (file.size > MAX_FILE_SIZE) {
        setState({
          ...INITIAL_UPLOAD_STATE,
          fileName: file.name,
          errorMessage: '文件超出大小限制（上限 50MB）',
        });
        return;
      }

      try {
        const created = await knowledgeApi.uploadDocument(baseId, file);
        setState((prev) => ({
          ...prev,
          documentId: created.document_id,
          progress: created.progress,
          indexStatus: created.index_status,
          uploading: false,
        }));

        timerRef.current = setInterval(() => {
          void (async () => {
            try {
              const status = await knowledgeApi.documentStatus(baseId, created.document_id);
              setState((prev) => ({
                ...prev,
                progress: status.progress,
                indexStatus: status.index_status,
                errorMessage: status.error_message ?? '',
              }));
              if (status.index_status !== 'processing') {
                clearPolling();
              }
            } catch (error) {
              clearPolling();
              setState((prev) => ({
                ...prev,
                errorMessage: toBusinessMessage(error),
              }));
            }
          })();
        }, POLL_INTERVAL_MS);
      } catch (error) {
        setState({
          ...INITIAL_UPLOAD_STATE,
          fileName: file.name,
          errorMessage: toBusinessMessage(error),
        });
      }
    },
    [clearPolling],
  );

  return { ...state, upload, reset };
}
