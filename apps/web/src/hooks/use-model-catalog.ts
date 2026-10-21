'use client';

import { useCallback, useState } from 'react';

import { modelApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import type { ProfileItem } from '@/lib/types';

/** 能力档位状态 */
export interface CatalogState {
  /** 是否加载中 */
  loading: boolean;
  /** 档位列表 */
  profiles: ProfileItem[];
  /** 业务语言错误提示 */
  errorMessage: string;
  /** 重新加载 */
  reload: () => Promise<void>;
}

/**
 * 能力档位目录 Hook。
 *
 * 目录内容由适配层配置驱动，前端不硬编码档位列表，
 * 也不展示任何真实模型名称（架构文档 3.1.3 / 附录 A-1）。
 *
 * @returns 档位状态与操作方法
 */
export function useModelCatalog(): CatalogState {
  const [loading, setLoading] = useState(true);
  const [profiles, setProfiles] = useState<ProfileItem[]>([]);
  const [errorMessage, setErrorMessage] = useState('');

  const reload = useCallback(async () => {
    setLoading(true);
    setErrorMessage('');
    try {
      const data = await modelApi.catalog();
      setProfiles(data.profiles);
    } catch (error) {
      setErrorMessage(toBusinessMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  return { loading, profiles, errorMessage, reload };
}
