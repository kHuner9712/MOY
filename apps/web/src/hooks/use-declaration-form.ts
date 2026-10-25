'use client';

/**
 * 工作台声明表单 Hook（B24）。
 *
 * 核心约束：**校验规则全部来自插件声明**（`inputs[]`），宿主里不写任何与具体插件相关的规则，
 * 因此 6 个插件共用同一份表单逻辑；新增插件或改声明后表单自动变化，无需改前端代码。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { PluginInputSpec } from '@/lib/types';

/** 表单值形态：文本 / 多选 / 文件（文件为已上传文档的 `doc_` id 数组） */
export type DeclarationValue = string | string[] | null;

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

// ---------------------------------------------------------------------------
// 候选值格式约束（W-08）：空候选择「平台按身份给值」，前端只能用形状兜底
//
// 规则与后端 `app/plugins/schema.py` 的 `InputSpec.format` 注释同源，改一处要改两处：
// - repo：`https?://…` / `ssh://…` / `git@host:path` / `owner/repo`；容忍 `#branch`、
//   `?ref=` 后缀；不含空白与换行。
// - department：1–32 字（按 `Array.from` 计码点，避免代理对被算成 2）；不含换行。
// 位置说明：这两个约束**不**替平台判「这个仓库/部门存不存在」，只判形状——
// 存在性由平台按身份解析，前端拿不到数据源。
// ---------------------------------------------------------------------------

/** `repo` 的四类合法形态（`owner/repo` 那一支不含空白与冒号） */
const REPO_PATTERN = /^(?:https?:\/\/\S+|ssh:\/\/\S+|git@[^\s:]+:\S+|[\w.-]+\/[\w.-]+)$/;

/** `department` 的长度区间（按码点计） */
const DEPARTMENT_MIN_LENGTH = 1;

/** `department` 的长度上限（与 manifest 的 `max_length` 对齐） */
const DEPARTMENT_MAX_LENGTH = 32;

/**
 * 判断「目标仓库」形状是否合法。
 *
 * @param value - 用户填写的值
 * @returns 合法返回 true
 */
function isValidRepo(value: string): boolean {
  if (value.trim() === '' || /\s/.test(value)) return false;
  // 先剥掉 `#branch` / `?ref=…` 后缀，再匹配四类形态：
  // `owner/repo#dev` 这种写法在 `[\w.-]+/[\w.-]+` 那一支里会因 `#` 直接失败。
  const base = value.split('#')[0]?.split('?')[0] ?? '';
  return REPO_PATTERN.test(base);
}

/**
 * 判断「所属部门」形状是否合法。
 *
 * @param value - 用户填写的值
 * @returns 合法返回 true
 */
function isValidDepartment(value: string): boolean {
  if (/[\r\n]/.test(value)) return false;
  const length = Array.from(value).length;
  return length >= DEPARTMENT_MIN_LENGTH && length <= DEPARTMENT_MAX_LENGTH;
}

/**
 * 按声明的 `format` 校验形状。
 *
 * **未知 format 直接放行**：后端将来新增 format 时，旧前端不能因此把任何输入都判成
 * 不合法（那是"后端加字段、前端全红"的经典事故）。也不清空用户输入。
 *
 * @param spec - 声明项
 * @param value - 当前值
 * @returns 错误文案；通过或无需校验时返回空串
 */
function validateFormat(spec: PluginInputSpec, value: DeclarationValue): string {
  const format = spec.format;
  if (!format) return '';
  const text = String(value ?? '');

  if (format === 'repo') {
    return isValidRepo(text) ? '' : `请填写有效的${spec.label}`;
  }
  if (format === 'department') {
    return isValidDepartment(text) ? '' : `请填写有效的${spec.label}`;
  }
  return '';
}

/**
 * 单个声明项的默认值。
 *
 * 口径：多选与文件为 `[]`，下拉取首项（无候选时为空串），其余为空串。
 * 抽成函数而不是在 `buildInitialValues` 里内联，是因为「异步补默认值」要用同一份规则——
 * 两处各写一遍，改一处漏一处就会得到「首屏 A 值与补值后的 B 值」两套默认。
 *
 * @param spec - 声明项
 * @returns 默认值
 */
function defaultValueOf(spec: PluginInputSpec): DeclarationValue {
  if (spec.type === 'multi_select') return [];
  if (spec.type === 'file') return [];
  if (spec.type === 'select') return spec.options?.[0] ?? '';
  return '';
}

/**
 * 生成初始值。
 *
 * @param inputs - 声明输入项
 * @returns 初始值表
 */
function buildInitialValues(inputs: PluginInputSpec[]): Record<string, DeclarationValue> {
  const initial: Record<string, DeclarationValue> = {};
  for (const spec of inputs) {
    initial[spec.key] = defaultValueOf(spec);
  }
  return initial;
}

/**
 * 判断一个值是否「等同于没填」。
 *
 * @param value - 声明值
 * @returns 空值返回 true
 */
function isBlankValue(value: DeclarationValue | undefined): boolean {
  if (value === null || value === undefined) return true;
  if (Array.isArray(value)) return value.length === 0;
  return value.trim() === '';
}

/**
 * 声明签名：只由「会影响默认值与校验的字段」拼成。
 *
 * 为什么需要它：`inputs` 是数组，父组件每次渲染都可能给出新引用（工作台页在详情
 * 到达前用的是 `?? []`），用引用当 effect 依赖会让合并逻辑随渲染反复执行。
 * 签名相同即视为「同一份声明」，合并只在该签名变化时发生。
 *
 * 分隔符用不可见控制字符，避免 `['a,b']` 与 `['a','b']` 拼出同一个签名。
 *
 * @param inputs - 声明输入项
 * @returns 稳定签名
 */
function declarationSignature(inputs: PluginInputSpec[]): string {
  return inputs
    .map((spec) =>
      [
        spec.key,
        spec.type,
        spec.required ? '1' : '0',
        typeof spec.max_length === 'number' ? String(spec.max_length) : '',
        (spec.options ?? []).join('\u0001'),
        spec.format ?? '',
      ].join('\u0002'),
    )
    .join('\u0003');
}

/**
 * 把异步到达的声明默认值合并进已有表单值。
 *
 * 只做两类写入：
 * 1. 字典里**不存在**的键；
 * 2. `select` 且有候选值、当前值为空串的槽位（原生 select 在 `value=""` 时
 *    视觉上会显示首项，但表单状态仍是空串，提交时被判「必填未填」）。
 *
 * 用户已填的任何值都不在写入范围内；无变化时返回原对象，让 React 跳过渲染。
 *
 * 抽成纯函数是为了能直接测「不覆盖用户输入」与「补齐默认值」两条规则，
 * 不必把 React 组件渲染器拖进单元测试（monorepo 根目录另有一份 React 18，
 * 渲染器会把两套 React 混在一起）。
 *
 * @param prev - 当前表单值
 * @param inputs - 声明输入项
 * @returns 合并后的表单值；无变化时返回 `prev` 本身
 */
export function mergeMissingDefaults(
  prev: Record<string, DeclarationValue>,
  inputs: PluginInputSpec[],
): Record<string, DeclarationValue> {
  const next: Record<string, DeclarationValue> = { ...prev };
  let changed = false;

  for (const spec of inputs) {
    const exists = Object.prototype.hasOwnProperty.call(prev, spec.key);
    if (!exists) {
      next[spec.key] = defaultValueOf(spec);
      changed = true;
      continue;
    }
    if (
      spec.type === 'select' &&
      (spec.options?.length ?? 0) > 0 &&
      isBlankValue(prev[spec.key])
    ) {
      next[spec.key] = defaultValueOf(spec);
      changed = true;
    }
  }

  return changed ? next : prev;
}

/**
 * 单个字段校验（规则全部来自声明）。
 *
 * @param spec - 声明项
 * @param value - 当前值
 * @returns 错误文案；通过返回空串
 */
export function validateField(spec: PluginInputSpec, value: DeclarationValue): string {
  // 文件类输入在 V1.0 是「选已上传的文件」，值是 doc_id 数组，与多选同样判空
  const isEmpty = Array.isArray(value)
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
  // 形状约束（`repo` / `department`）：空候选时前端没有候选表，只能靠它兜底。
  // 放在候选表校验**之前**：两者都声明时，形状错更具体（「填错了」而不是「不在候选内」）。
  const formatError = validateFormat(spec, value);
  if (formatError) return formatError;
  if (spec.type === 'select' && spec.options && spec.options.length > 0) {
    // 只在「确实有候选值」时判有效性：候选为空说明声明数据不全，此时界面会降级成
    // 文本输入（见 declaration-form.tsx），再按空候选表校验就等于任何输入都不合法。
    if (!spec.options.includes(String(value))) {
      return `请选择有效的${spec.label}`;
    }
  }
  if (spec.type === 'multi_select' && spec.options) {
    const picked = Array.isArray(value) ? value : [];
    if (picked.some((item) => !spec.options?.includes(item))) {
      return `${spec.label}包含无效选项`;
    }
  }
  return '';
}

/**
 * 全字段校验（纯函数，`validate()` 与测试共用同一份规则）。
 *
 * 抽成纯函数的原因：`validate()` 只是「跑一遍 + 写 state」，而「提交前必须跑完全部字段」
 * 这件事本身需要被机械守住（W-06：只校验失焦字段会漏，用户点提交时看到的是
 * 「点了没反应」）。把它做成可在无渲染器环境直接断言的函数，测试就不必拖进 React 渲染器。
 *
 * @param inputs - 声明输入项
 * @param values - 当前值
 * @returns 错误表（key → 业务语言）；全部通过时为空对象
 */
export function collectFieldErrors(
  inputs: PluginInputSpec[],
  values: Record<string, DeclarationValue>,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const spec of inputs) {
    const message = validateField(spec, values[spec.key] ?? null);
    if (message) errors[spec.key] = message;
  }
  return errors;
}

/**
 * 声明驱动表单 Hook。
 *
 * 目标预填（W1 切片 B）：`initial` 非空时，在 `inputs` 由空变非空后
 * **一次性**合入首个文本输入（text / textarea / email），不覆盖用户已填内容——
 * 从对话页「开始」带 `?goal=` 进入工作台即走这条路径。
 *
 * 异步补默认值（F-04 收尾）：`useState` 的初值只算一次，而工作台页在插件详情
 * 到达前是用空 `inputs` 挂载的，于是 `values` 停在 `{}`：下拉在界面上显示首项
 * （原生 select 在 `value=""` 且没有空选项时的视觉行为），状态却是空串，
 * 提交时被判「必填未填」。下面的签名 effect 负责在声明到达后补齐默认值。
 *
 * @param inputs - 插件声明里的输入项
 * @param initial - 预填值（目标原文）；为空时不预填
 * @returns 表单状态与操作
 */
export function useDeclarationForm(
  inputs: PluginInputSpec[],
  initial?: string,
): UseDeclarationFormResult {
  const initialValues = useMemo(() => buildInitialValues(inputs), [inputs]);
  const [values, setValues] = useState<Record<string, DeclarationValue>>(initialValues);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const signature = useMemo(() => declarationSignature(inputs), [inputs]);

  // 已补过默认值的声明签名（去重：同一份声明只补一次，声明变了才再补）
  const mergedSignatureRef = useRef<string>('');
  useEffect(() => {
    if (mergedSignatureRef.current === signature) return;
    mergedSignatureRef.current = signature;
    setValues((prev) => mergeMissingDefaults(prev, inputs));
  }, [inputs, signature]);

  // 已合入的预填值（去重：同一个 initial 只合入一次，换一个目标才再合入）
  const mergedInitialRef = useRef<string>('');
  useEffect(() => {
    if (!initial || mergedInitialRef.current === initial) return;
    const target = inputs.find(
      (spec) => spec.type === 'text' || spec.type === 'textarea' || spec.type === 'email',
    );
    if (!target) return;
    mergedInitialRef.current = initial;
    setValues((prev) => (prev[target.key] ? prev : { ...prev, [target.key]: initial }));
  }, [initial, inputs]);

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
    const nextErrors = collectFieldErrors(inputs, values);
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
        // 文件类输入：回传所选文档的 doc_id 数组（插件凭 personal.file.read 读取）
        payload[spec.key] = Array.isArray(value) ? value : [];
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
