import type { PluginInstalledStatus } from './types';

/**
 * 判断插件对当前企业是否「已安装」。
 *
 * 为什么不能写成 `status !== 'none'`：后端 `installed_status` 是五值，
 * `uninstalled` 表示已卸载——它同样不等于 `none`，但语义是**未安装**。
 * 市场条目里的 `installed` 表示装过且当前可操作，`enabled` / `disabled`
 * 是它的启用/停用子态。把 `uninstalled` 算成已安装，会让「去启用 / 打开工作台」
 * 出现在一个已经卸载的插件上。
 *
 * @param status - 接口返回的安装态
 * @returns 已安装（含启用/停用）返回 true
 */
export function isPluginInstalled(status: PluginInstalledStatus): boolean {
  return status === 'enabled' || status === 'disabled' || status === 'installed';
}
