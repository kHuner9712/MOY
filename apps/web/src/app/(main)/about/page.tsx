'use client';

import * as React from 'react';

import { BRAND } from '@/lib/brand';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

import webPackage from '../../../../package.json';

/**
 * 开源组件清单（本页展示用的**人工同步副本**）。
 *
 * 为什么这里内嵌一份而不是运行时读文件：浏览器拿不到仓库里的 `sbom.cdx.json`，
 * 而为此起一个接口只为了列许可证，等于给本页加一次失败机会（还要三态）。
 * 因此本页只列**主要**组件，完整机器可读清单随交付包提供（`sbom.cdx.json`）。
 *
 * 维护约定（必须与 SBOM 同步）：新增/移除开源组件时，`deploy/sbom/generate.py`
 * 生成的清单与本表**同一次改动内**一起更新，否则"页面上说的"和"包里实际带的"
 * 会各说一套——这正是客户在合规检查时最先发现的那类矛盾。
 *
 * 文案口径（本项目铁律）：只写"它在我们产品里负责什么"，不写它的技术类别；
 * 用户看到的是产品组成，不是实现细节。
 */
const OPEN_SOURCE_COMPONENTS = [
  { name: 'React', license: 'MIT', role: '界面构建' },
  { name: 'Next.js', license: 'MIT', role: '页面与访问路径' },
  { name: 'Radix UI', license: 'MIT', role: '交互组件' },
  { name: 'TanStack Query', license: 'MIT', role: '数据加载与缓存' },
  { name: 'Tailwind CSS', license: 'MIT', role: '界面样式' },
  { name: 'lucide', license: 'ISC', role: '图标' },
  { name: 'FastAPI', license: 'MIT', role: '服务端接口框架' },
  { name: 'PostgreSQL', license: 'PostgreSQL License', role: '业务数据存储' },
  { name: 'pgvector', license: 'PostgreSQL License', role: '记忆检索所需的数据库扩展' },
  { name: 'Valkey', license: 'BSD-3-Clause', role: '缓存' },
  { name: 'Keycloak', license: 'Apache-2.0', role: '统一登录与成员身份管理' },
  { name: 'nginx', license: 'BSD-2-Clause', role: '入口转发' },
  { name: 'Dify', license: 'Apache-2.0（修改版，附使用限制）', role: '工作流底座' },
  { name: 'Prometheus', license: 'Apache-2.0', role: '运行状态观测' },
  { name: 'Python', license: 'PSF-2.0', role: '服务端运行时' },
  { name: 'Node.js', license: 'MIT', role: '前端运行时' },
] as const;

/**
 * 合规声明三件套的入口。
 *
 * 三份文档随交付包放在 `docs/compliance/` 下（不打包进前端产物）：
 *   1. `privacy-policy.md`    隐私政策
 *   2. `dpa-template.md`      数据处理协议模板
 *   3. `security-whitepaper.md` 安全白皮书
 *
 * 链接策略：`NEXT_PUBLIC_COMPLIANCE_BASE_URL` 有值时渲染成可点击链接
 * （客户把它挂到自己的门户上，例如 `/compliance`），没有值时**只显示交付路径**，
 * 不生成死链——运维在离线环境里点一个 404 比看不到入口更糟。
 */
const COMPLIANCE_DOCS = [
  {
    slug: 'privacy-policy',
    file: 'docs/compliance/privacy-policy.md',
    title: '隐私政策',
    description: '我们收集哪些信息、用于什么目的、保存多久，以及您如何行使自己的权利。',
  },
  {
    slug: 'dpa-template',
    file: 'docs/compliance/dpa-template.md',
    title: '数据处理协议模板',
    description: '企业与我们一起处理成员信息时可直接签署的协议模板（含数据出境与分包说明）。',
  },
  {
    slug: 'security-whitepaper',
    file: 'docs/compliance/security-whitepaper.md',
    title: '安全白皮书',
    description: '部署形态、数据隔离方式、访问控制与安全响应流程的完整说明。',
  },
] as const;

/** 交付形态（私有化部署的三种装箱方式，本页只说明当前这一个是哪一种） */
const DELIVERY_FORM = '私有化部署 · 轻量版';

/**
 * 关于页（P4-05.6）。
 *
 * 只回答三个问题：**这是什么版本、它由哪些开源组件构成、合规文件在哪里**。
 * 不展示任何内部实现口径——客户在这里要的是"可核对的事实"，不是架构说明。
 *
 * 本页为静态内容（无数据请求），因此没有加载/失败/空态三态：
 * 版本号与清单都随页面一起打包，页面能渲染出来，内容就是完整的。
 *
 * @returns 关于页元素
 */
export default function AboutPage(): React.JSX.Element {
  const version = webPackage.version;
  const complianceBase = (process.env.NEXT_PUBLIC_COMPLIANCE_BASE_URL ?? '').trim();

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>版本信息</CardTitle>
          <CardDescription>您正在使用的产品与版本</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <dl className="grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted-foreground">产品名称</dt>
              <dd className="text-sm font-medium">
                {BRAND.name}（{BRAND.latin}）
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">版本号</dt>
              <dd>
                <Badge variant="secondary">v{version}</Badge>
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">交付形态</dt>
              <dd className="text-sm font-medium">{DELIVERY_FORM}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">更新方式</dt>
              <dd className="text-sm font-medium">由您的运维人员在维护窗口内升级</dd>
            </div>
          </dl>
          <p className="text-sm text-muted-foreground">{BRAND.promise}。</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>开源组件与许可证</CardTitle>
          <CardDescription>
            本产品使用了下列开源组件，我们在此如实列明各自的许可证
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <ul className="divide-y">
            {OPEN_SOURCE_COMPONENTS.map((component) => (
              <li
                key={component.name}
                className="flex flex-wrap items-center justify-between gap-2 py-2"
              >
                <span className="text-sm font-medium">{component.name}</span>
                <span className="text-xs text-muted-foreground">{component.role}</span>
                <Badge variant="outline">{component.license}</Badge>
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">
            以上为主要组件；随交付包提供的{' '}
            <code className="rounded bg-muted px-1 py-0.5">sbom.cdx.json</code>{' '}
            是完整的机器可读清单，包含每个组件的名称、版本与许可证，可由您的合规人员直接核对。
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>合规声明</CardTitle>
          <CardDescription>以下三份文件随交付包提供，可交给您的法务与安全团队审阅</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ul className="space-y-3">
            {COMPLIANCE_DOCS.map((doc) => (
              <li key={doc.slug} className="rounded-md border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-medium">{doc.title}</span>
                  <Badge variant="outline">{doc.file}</Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{doc.description}</p>
                {complianceBase === '' ? (
                  <p className="mt-2 text-xs text-muted-foreground">
                    在交付包的 <code className="rounded bg-muted px-1 py-0.5">docs/compliance/</code>{' '}
                    目录下可直接打开该文件。
                  </p>
                ) : (
                  <p className="mt-2 text-xs">
                    <a
                      className="font-medium text-primary underline underline-offset-2"
                      href={`${complianceBase}/${doc.slug}`}
                    >
                      打开{doc.title}
                    </a>
                  </p>
                )}
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">
            需要签署数据处理协议或有安全评估需求时，请联系为您部署本产品的负责人。
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
