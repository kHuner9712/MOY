'use client';

import { ShieldCheck } from 'lucide-react';
import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Drawer,
  DrawerBody,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/drawer';
import { Field, Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Tooltip } from '@/components/ui/tooltip';
import { capabilityLabel, isOrgCapability } from '@/lib/capability-labels';
import type { PluginDetail, PluginSettingValue } from '@/lib/types';

/** 抽屉模式：安装时勾选并保存配置；已安装后只改授权 */
export type GrantDrawerMode = 'install' | 'edit';

/** 授权抽屉属性 */
export interface GrantDrawerProps {
  /** 是否打开 */
  open: boolean;
  /** 开关回调 */
  onOpenChange: (open: boolean) => void;
  /** 插件详情（能力与配置声明都来自这里） */
  plugin: PluginDetail;
  /** 已授权能力 key（编辑模式使用；安装模式传空数组） */
  granted: string[];
  /** 模式 */
  mode: GrantDrawerMode;
  /** 提交中 */
  submitting?: boolean;
  /** 提交：安装传 grants + settings；编辑只传 grants */
  onSubmit: (payload: {
    grants: Record<string, boolean>;
    settings?: Record<string, PluginSettingValue>;
  }) => void | Promise<void>;
}

/**
 * 授权抽屉（520px，逐项能力 + 后果 + 配置）。
 *
 * 交互约定（PRD P0-P3-03 / 团队裁定）：
 * - 必需能力在**安装**模式下可取消勾选，但确认按钮会置灰并列出缺哪些（验收要求「置灰且显示原因」）；
 * - 必需能力在**编辑**模式下不可撤回，复选框置灰并提示「该权限为插件必需」；
 * - 企业能力开关关闭时无法授权，提示「需管理员在团队设置中开启」；
 * - `settings_schema` 一并接线：声明里有什么就渲染什么，前端不硬编码配置项。
 *
 * @param props - 组件属性
 * @returns 抽屉元素
 */
export function GrantDrawer({
  open,
  onOpenChange,
  plugin,
  granted,
  mode,
  submitting = false,
  onSubmit,
}: GrantDrawerProps): React.JSX.Element {
  const [draft, setDraft] = React.useState<Record<string, boolean>>({});
  const [settings, setSettings] = React.useState<Record<string, PluginSettingValue>>({});

  // 打开或插件变化时重置草稿：必需能力默认勾选，其余按已授权状态回填
  React.useEffect(() => {
    if (!open) return;
    const nextDraft: Record<string, boolean> = {};
    for (const capability of plugin.capabilities) {
      nextDraft[capability.capability_key] =
        capability.required || granted.includes(capability.capability_key);
    }
    setDraft(nextDraft);

    const nextSettings: Record<string, PluginSettingValue> = {};
    for (const spec of plugin.settings_schema) {
      nextSettings[spec.key] = spec.type === 'select' ? (spec.options?.[0] ?? '') : '';
    }
    setSettings(nextSettings);
  }, [open, plugin, granted]);

  const capabilities = plugin.capabilities;
  const missingRequired = capabilities.filter(
    (capability) => capability.required && !draft[capability.capability_key],
  );
  const canSubmit = missingRequired.length === 0 && !submitting;

  /** 切换某项能力 */
  const toggle = (capabilityKey: string, orgSwitchEnabled: boolean): void => {
    const capability = capabilities.find((item) => item.capability_key === capabilityKey);
    if (!capability) return;
    // 编辑模式下必需能力不可撤回
    if (mode === 'edit' && capability.required) return;
    if (!orgSwitchEnabled) return;
    setDraft((prev) => ({ ...prev, [capabilityKey]: !prev[capabilityKey] }));
  };

  /** 提交 */
  const submit = (): void => {
    if (!canSubmit) return;
    void onSubmit({ grants: draft, settings: mode === 'install' ? settings : undefined });
  };

  return (
    <Drawer open={open} onOpenChange={submitting ? undefined : onOpenChange}>
      <DrawerContent size="md" className="sm:max-w-[520px]">
        <DrawerHeader>
          <DrawerTitle className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" className="h-4 w-4 text-primary" />
            {mode === 'install' ? `添加「${plugin.name}」` : '调整授权'}
          </DrawerTitle>
          <DrawerDescription>
            插件需要下面这些数据或操作权限；不同意的项会说明影响，你可以逐项决定。
          </DrawerDescription>
        </DrawerHeader>

        <DrawerBody className="space-y-4">
          <ul className="space-y-3">
            {capabilities.map((capability) => {
              const label = capabilityLabel(capability.capability_key, capability.display_name);
              const orgScoped = capability.category === 'org' || isOrgCapability(capability.capability_key);
              const orgBlocked = orgScoped && !capability.org_switch_enabled;
              const lockedByRequired = mode === 'edit' && capability.required;
              const disabled = orgBlocked || lockedByRequired || submitting;
              const checked = Boolean(draft[capability.capability_key]);

              return (
                <li key={capability.capability_key} className="rounded-md border p-3">
                  <div className="flex items-start gap-2">
                    <Tooltip
                      content={
                        lockedByRequired
                          ? '该权限为插件必需，无法撤回'
                          : orgBlocked
                            ? '需管理员在团队设置中开启该能力'
                            : ''
                      }
                      disabled={!disabled}
                    >
                      <span className="mt-0.5 inline-flex">
                        <input
                          type="checkbox"
                          className="h-4 w-4 cursor-pointer accent-primary disabled:cursor-not-allowed disabled:opacity-50"
                          aria-label={`授权：${label}`}
                          checked={checked || lockedByRequired}
                          disabled={disabled}
                          onChange={() => toggle(capability.capability_key, capability.org_switch_enabled)}
                        />
                      </span>
                    </Tooltip>

                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium">{label}</span>
                        {capability.required ? (
                          <Badge variant="secondary">必需</Badge>
                        ) : (
                          <Badge variant="outline">可选</Badge>
                        )}
                        {orgScoped ? <Badge variant="outline">企业级</Badge> : null}
                        {orgBlocked ? <Badge variant="warning">企业未开启</Badge> : null}
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        为什么需要：{capability.reason || '插件未说明用途'}
                      </p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        不同意的后果：{capability.denial_effect || '相关步骤将无法完成'}
                      </p>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>

          {mode === 'install' && plugin.settings_schema.length > 0 ? (
            <section className="space-y-3 rounded-md border p-3">
              <div>
                <h3 className="text-sm font-semibold">初始配置</h3>
                <p className="text-xs text-muted-foreground">
                  这些配置之后可以在「已添加」里随时修改，不填也能先添加。
                </p>
              </div>
              {plugin.settings_schema.map((spec) => (
                <Field key={spec.key} label={spec.label} htmlFor={`setting-${spec.key}`}>
                  {spec.type === 'select' ? (
                    <Select
                      id={`setting-${spec.key}`}
                      value={String(settings[spec.key] ?? '')}
                      disabled={submitting}
                      onChange={(event) =>
                        setSettings((prev) => ({ ...prev, [spec.key]: event.target.value }))
                      }
                      options={(spec.options ?? []).map((option) => ({
                        value: option,
                        label: option,
                      }))}
                    />
                  ) : (
                    <Input
                      id={`setting-${spec.key}`}
                      value={String(settings[spec.key] ?? '')}
                      disabled={submitting}
                      onChange={(event) =>
                        setSettings((prev) => ({ ...prev, [spec.key]: event.target.value }))
                      }
                      placeholder={`填写${spec.label}`}
                    />
                  )}
                </Field>
              ))}
            </section>
          ) : null}

          {missingRequired.length > 0 ? (
            <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              <p className="font-medium">还需要勾选这些必需权限才能继续：</p>
              <ul className="mt-1 list-inside list-disc">
                {missingRequired.map((capability) => (
                  <li key={capability.capability_key}>
                    {capabilityLabel(capability.capability_key, capability.display_name)}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </DrawerBody>

        <DrawerFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            取消
          </Button>
          <Button type="button" onClick={submit} disabled={!canSubmit}>
            {submitting
              ? '处理中…'
              : mode === 'install'
                ? '同意并添加'
                : '保存授权'}
          </Button>
        </DrawerFooter>
      </DrawerContent>
    </Drawer>
  );
}
