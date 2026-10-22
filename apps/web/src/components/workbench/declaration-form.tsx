'use client';

import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Field, Input, Textarea } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import type { DeclarationValue } from '@/hooks/use-declaration-form';
import type { PluginInputSpec } from '@/lib/types';

/** 声明表单属性 */
export interface DeclarationFormProps {
  /** 声明里的输入项（6 类控件按 `type` 渲染） */
  inputs: PluginInputSpec[];
  /** 当前值 */
  values: Record<string, DeclarationValue>;
  /** 字段错误 */
  errors: Record<string, string>;
  /** 值变更 */
  onChange: (key: string, value: DeclarationValue) => void;
  /** 是否禁用（插件停用 / 提交中） */
  disabled?: boolean;
  /** 表单提交（回车或点提交按钮） */
  onSubmit: () => void;
  /** 提交按钮文案 */
  submitLabel?: string;
}

/**
 * 声明驱动的输入表单。
 *
 * 6 类控件与声明的 `type` 一一对应：
 * `text` / `email` → 单行输入，`textarea` → 多行，`select` → 下拉，
 * `multi_select` → 复选组，`file` → 文件选择。
 * 控件顺序、标题、必填、长度上限全部来自声明，宿主不硬编码任何字段名。
 *
 * @param props - 组件属性
 * @returns 表单元素
 */
export function DeclarationForm({
  inputs,
  values,
  errors,
  onChange,
  disabled = false,
  onSubmit,
  submitLabel = '开始执行',
}: DeclarationFormProps): React.JSX.Element {
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      {inputs.map((spec) => {
        const fieldId = `declaration-${spec.key}`;
        const value = values[spec.key] ?? null;
        const hint =
          typeof spec.max_length === 'number'
            ? `最多 ${spec.max_length} 个字`
            : spec.required
              ? '必填'
              : undefined;

        if (spec.type === 'multi_select') {
          const picked = Array.isArray(value) ? value : [];
          return (
            <Field key={spec.key} label={spec.label} required={spec.required} error={errors[spec.key]} hint={hint}>
              <div className="flex flex-wrap gap-3" role="group" aria-label={spec.label}>
                {(spec.options ?? []).map((option) => {
                  const checked = picked.includes(option);
                  return (
                    <label key={option} className="flex items-center gap-1.5 text-sm">
                      <input
                        type="checkbox"
                        className="h-4 w-4 cursor-pointer accent-primary disabled:cursor-not-allowed disabled:opacity-60"
                        checked={checked}
                        disabled={disabled}
                        onChange={() => {
                          const next = checked
                            ? picked.filter((item) => item !== option)
                            : [...picked, option];
                          onChange(spec.key, next);
                        }}
                      />
                      {option}
                    </label>
                  );
                })}
              </div>
            </Field>
          );
        }

        if (spec.type === 'file') {
          const fileName = value instanceof File ? value.name : '';
          return (
            <Field
              key={spec.key}
              label={spec.label}
              htmlFor={fieldId}
              required={spec.required}
              error={errors[spec.key]}
              hint="单个文件不超过 50MB"
            >
              <Input
                id={fieldId}
                type="file"
                disabled={disabled}
                onChange={(event) => onChange(spec.key, event.target.files?.[0] ?? null)}
              />
              {fileName ? <p className="text-xs text-muted-foreground">已选择：{fileName}</p> : null}
            </Field>
          );
        }

        if (spec.type === 'select') {
          return (
            <Field
              key={spec.key}
              label={spec.label}
              htmlFor={fieldId}
              required={spec.required}
              error={errors[spec.key]}
              hint={hint}
            >
              <Select
                id={fieldId}
                value={String(value ?? '')}
                disabled={disabled}
                invalid={Boolean(errors[spec.key])}
                onChange={(event) => onChange(spec.key, event.target.value)}
                options={(spec.options ?? []).map((option) => ({ value: option, label: option }))}
              />
            </Field>
          );
        }

        if (spec.type === 'textarea') {
          return (
            <Field
              key={spec.key}
              label={spec.label}
              htmlFor={fieldId}
              required={spec.required}
              error={errors[spec.key]}
              hint={hint}
            >
              <Textarea
                id={fieldId}
                rows={4}
                value={String(value ?? '')}
                maxLength={spec.max_length}
                disabled={disabled}
                onChange={(event) => onChange(spec.key, event.target.value)}
                placeholder={`填写${spec.label}`}
              />
            </Field>
          );
        }

        return (
          <Field
            key={spec.key}
            label={spec.label}
            htmlFor={fieldId}
            required={spec.required}
            error={errors[spec.key]}
            hint={hint}
          >
            <Input
              id={fieldId}
              type={spec.type === 'email' ? 'email' : 'text'}
              value={String(value ?? '')}
              maxLength={spec.max_length}
              disabled={disabled}
              invalid={Boolean(errors[spec.key])}
              onChange={(event) => onChange(spec.key, event.target.value)}
              placeholder={`填写${spec.label}`}
            />
          </Field>
        );
      })}

      <div className="flex items-center gap-2 pt-1">
        <Button type="submit" disabled={disabled}>
          {submitLabel}
        </Button>
        <span className="text-xs text-muted-foreground">
          提交后会生成一次自动化任务，可在下方查看每一步进度
        </span>
      </div>
    </form>
  );
}
