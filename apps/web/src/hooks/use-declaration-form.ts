'use client';

/**
 * 工作台声明表单 Hook（B24）。
 *
 * 核心约束：**校验规则全部来自插件声明**（`inputs[]`），宿主里不写任何与具体插件相关的规则，
 * 因此 6 个插件共用同一份表单逻辑；新增插件或改声明后表单自动变化，无需改前端代码。
 */

import { useCallback, useMemo, useState } from 'react';

import type { PluginInputSpec } from '@/lib/types';

/** 表单值形态：文本 / 多选 / 文件 */
export type DeclarationValue = string | string[] | File | null;

/** 提交给 A1 的 inputs（扁平标量 + 多选数组） */
export type DeclarationInputs = Record<string, string | string[] | number | boolean>;

/** `useDeclarationForm` 返回值 */
export interface UseDeclarationFormResult {
  /** 当前值 */
  values: Record<string, DeclarationValue>;
  /** 字段错误（key → 业务语言） */
  errors: Record<string, string>;
  /** 设置某个字段 */
  setValue: (key: string, value: DeclarationValue) => void;
  /** 全量校验；返回是否通过 */
  validate: () => boolean;
  /** 重置为初始值 */
  reset: () => void;
  /** 组装提交载荷 */
  buildInputs: () => DeclarationInputs;
  /** 是否有文件类输入（当前缺少上传通道，提交时只带文件名） */
  hasFileInput: boolean;
}

/** 邮箱格式（与声明里的 email 控件配套，规则写在前端一处） */
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** 文件大小上限（与知识库上传一致：50MB） */
const FILE_MAX_BYTES = 50 * 1024 * 1024;

/**
 * 生成初始值。
 *
 * @param inputs - 声明输入项
 * @returns 初始值表
 */
function buildInitialValues(inputs: PluginInputSpec[]): Record<string, DeclarationValue> {
  const initial: Record<string, DeclarationValue> = {};
  for (const spec of inputs) {
    if (spec.type === 'multi_select') initial[spec.key] = [];
    else if (spec.type === 'file') initial[spec.key] = null;
    else if (spec.type === 'select') initial[spec.key] = spec.options?.[0] ?? '';
    else initial[spec.key] = '';
  }
  return initial;
}

/**
 * 单个字段校验（规则全部来自声明）。
 *
 * @param spec - 声明项
 * @param value - 当前值
 * @returns 错误文案；通过返回空串
 */
export function validateField(spec: PluginInputSpec, value: DeclarationValue): string {
  const isFile = spec.type === 'file';
  const isEmpty = isFile
    ? value === null
    : Array.isArray(value)
      ? value.length === 0
      : String(value ?? '').trim().length === 0;

  if (spec.required && isEmpty) {
    return `请填写${spec.label}`;
  }
  if (isEmpty) return '';

  if (spec.type === 'email' && !EMAIL_PATTERN.test(String(value))) {
    return '请填写有效的邮箱地址';
  }
  if (
    (spec.type === 'text' || spec.type === 'textarea' || spec.type === 'email') &&
    typeof value === 'string' &&
    typeof spec.max_length === 'number' &&
    value.length > spec.max_length
  ) {
    return `${spec.label}最多 ${spec.max_length} 个字`;
  }
  if (spec.type === 'select' && spec.options && !spec.options.includes(String(value))) {
    return `请选择有效的${spec.label}`;
  }
  if (spec.type === 'multi_select' && spec.options) {
    const picked = Array.isArray(value) ? value : [];
    if (picked.some((item) => !spec.options?.includes(item))) {
      return `${spec.label}包含无效选项`;
    }
  }
  if (isFile && value instanceof File && value.size > FILE_MAX_BYTES) {
    return '文件超出大小限制（上限 50MB）';
  }
  return '';
}

/**
 * 声明驱动表单 Hook。
 *
 * @param inputs - 插件声明里的输入项
 * @returns 表单状态与操作
 */
export function useDeclarationForm(inputs: PluginInputSpec[]): UseDeclarationFormResult {
  const initialValues = useMemo(() => buildInitialValues(inputs), [inputs]);
  const [values, setValues] = useState<Record<string, DeclarationValue>>(initialValues);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const setValue = useCallback(
    (key: string, value: DeclarationValue): void => {
      setValues((prev) => ({ ...prev, [key]: value }));
      setErrors((prev) => {
        if (!prev[key]) return prev;
        const next = { ...prev };
        delete next[key];
        return next;
      });
    },
    [],
  );

  const validate = useCallback((): boolean => {
    const nextErrors: Record<string, string> = {};
    for (const spec of inputs) {
      const message = validateField(spec, values[spec.key] ?? null);
      if (message) nextErrors[spec.key] = message;
    }
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  }, [inputs, values]);

  const reset = useCallback((): void => {
    setValues(initialValues);
    setErrors({});
  }, [initialValues]);

  const buildInputs = useCallback((): DeclarationInputs => {
    const payload: DeclarationInputs = {};
    for (const spec of inputs) {
      const value = values[spec.key] ?? null;
      if (spec.type === 'file') {
        // 当前无文件上传通道：只带文件名，供后端登记与人工核对
        payload[spec.key] = value instanceof File ? value.name : '';
        continue;
      }
      if (spec.type === 'multi_select') {
        payload[spec.key] = Array.isArray(value) ? value : [];
        continue;
      }
      payload[spec.key] = typeof value === 'string' ? value.trim() : '';
    }
    return payload;
  }, [inputs, values]);

  const hasFileInput = useMemo(() => inputs.some((spec) => spec.type === 'file'), [inputs]);

  return { values, errors, setValue, validate, reset, buildInputs, hasFileInput };
}
