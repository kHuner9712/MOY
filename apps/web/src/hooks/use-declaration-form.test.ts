import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import type { PluginInputFormat, PluginInputSpec } from '@/lib/types';

import { collectFieldErrors, mergeMissingDefaults, validateField } from './use-declaration-form';

/** 工作台真实声明的一个最小子集（与 plg_dev_workbench 同形） */
const SPECS: PluginInputSpec[] = [
  { key: 'title', label: '需求标题', type: 'text', required: true, max_length: 80 },
  { key: 'requirement', label: '需求描述', type: 'textarea', required: true, max_length: 4000 },
  { key: 'repo', label: '目标仓库', type: 'select', required: true },
  {
    key: 'branch_policy',
    label: '分支策略',
    type: 'select',
    required: true,
    options: ['新建分支', '指定分支'],
  },
  { key: 'notify', label: '通知邮箱', type: 'email', required: false },
];

describe('mergeMissingDefaults', () => {
  it('声明异步到达后补齐缺失键，下拉取首项', () => {
    const merged = mergeMissingDefaults({}, SPECS);

    expect(merged.branch_policy).toBe('新建分支');
    expect(merged.title).toBe('');
    expect(merged.repo).toBe('');
  });

  it('用户已填的值一律不被覆盖，无变化时返回原对象', () => {
    const filled = {
      title: '把登录页品牌化',
      requirement: '换成麦子品牌',
      repo: 'moy/web',
      branch_policy: '指定分支',
      notify: 'dev@example.com',
    };

    const merged = mergeMissingDefaults(filled, SPECS);

    expect(merged).toBe(filled);
    expect(merged).toEqual(filled);
  });

  it('空串下拉在有候选值时补首项，用户选过的值保持不动', () => {
    const merged = mergeMissingDefaults({ branch_policy: '' }, SPECS);
    expect(merged.branch_policy).toBe('新建分支');

    const picked = mergeMissingDefaults({ branch_policy: '指定分支' }, SPECS);
    expect(picked.branch_policy).toBe('指定分支');
  });

  it('幂等：同一份声明重复合并不会产生新对象', () => {
    const first = mergeMissingDefaults({}, SPECS);
    const second = mergeMissingDefaults(first, SPECS);
    expect(second).toBe(first);
  });
});

describe('validateField', () => {
  it('必填为空时给出业务文案', () => {
    expect(validateField(SPECS[0]!, '')).toBe('请填写需求标题');
  });

  it('空候选下拉不做候选校验（降级为文本框后仍可提交）', () => {
    expect(validateField(SPECS[2]!, 'https://git.example.com/moy/web')).toBe('');
  });

  it('有候选值时拒绝不在候选内的值', () => {
    expect(validateField(SPECS[3]!, '不存在的分支')).toBe('请选择有效的分支策略');
  });

  it('邮箱格式错误时给出可读提示', () => {
    expect(validateField(SPECS[4]!, 'not-an-email')).toBe('请填写有效的邮箱地址');
  });
});

// ---------------------------------------------------------------------------
// W-06：提交前的统一校验 + 关掉浏览器原生校验
// ---------------------------------------------------------------------------

/** 必填邮箱的声明（真实插件里 email 多为选填，故单独构造以覆盖「空」这一态） */
const REQUIRED_EMAIL: PluginInputSpec = {
  key: 'notify',
  label: '通知邮箱',
  type: 'email',
  required: true,
};

describe('collectFieldErrors（提交前跑完全部字段）', () => {
  const values = (notify: string): Record<string, string> => ({
    title: '把登录页品牌化',
    requirement: '换成麦子品牌',
    repo: 'moy/web',
    branch_policy: '新建分支',
    notify,
  });

  it('必填邮箱为空 → 提示「请填写…」，提交被拦下', () => {
    const errors = collectFieldErrors([...SPECS.slice(0, 4), REQUIRED_EMAIL], values(''));

    expect(errors).toEqual({ notify: '请填写通知邮箱' });
  });

  it('邮箱格式非法 → 提示应用内文案（不是浏览器气泡）', () => {
    const errors = collectFieldErrors(
      [...SPECS.slice(0, 4), REQUIRED_EMAIL],
      values('not-an-email'),
    );

    expect(errors).toEqual({ notify: '请填写有效的邮箱地址' });
  });

  it('邮箱合法 → 无错误（此时才会组装提交载荷）', () => {
    const errors = collectFieldErrors(
      [...SPECS.slice(0, 4), REQUIRED_EMAIL],
      values('dev@example.com'),
    );

    expect(errors).toEqual({});
  });
});

describe('候选值格式约束（W-08：空候选 + format）', () => {
  /** 目标仓库：`select` 且**没有候选值**（取值由平台按身份提供） */
  const REPO: PluginInputSpec = {
    key: 'repo',
    label: '目标仓库',
    type: 'select',
    required: true,
    format: 'repo',
  };

  /** 所属部门：同上，另有 32 字上限 */
  const DEPARTMENT: PluginInputSpec = {
    key: 'department',
    label: '所属部门',
    type: 'select',
    required: true,
    max_length: 32,
    format: 'department',
  };

  it('空候选择 + 仓库形状非法 → 提示「请填写有效的目标仓库」（不再放行任意文本）', () => {
    expect(validateField(REPO, 'abc')).toBe('请填写有效的目标仓库');
  });

  it('仓库四类合法形态全部通过（含 #branch / ?ref= 后缀）', () => {
    for (const value of [
      'https://github.com/moy/web',
      'ssh://git@git.example.com/moy/web.git',
      'git@github.com:moy/web.git',
      'moy/web',
      'moy/web#feature/login',
      'https://github.com/moy/web?ref=main',
    ]) {
      expect(validateField(REPO, value)).toBe('');
    }
  });

  it('仓库含空白时判非法（复制粘贴带空格是最常见的手误）', () => {
    expect(validateField(REPO, 'moy / web')).toBe('请填写有效的目标仓库');
  });

  it('部门 33 字 / 含换行 → 非法；正常部门名 → 通过', () => {
    expect(validateField(DEPARTMENT, '研'.repeat(33))).toBe('请填写有效的所属部门');
    expect(validateField(DEPARTMENT, '研发\n部')).toBe('请填写有效的所属部门');
    expect(validateField(DEPARTMENT, '研发部')).toBe('');
  });

  it('未知 format 直接跳过（后端新增 format 不得让旧前端全红）', () => {
    const future: PluginInputSpec = { ...REPO, format: 'bogus' as PluginInputFormat };

    expect(validateField(future, 'abc')).toBe('');
  });

  it('未声明 format 的空候选择保持原行为（降级文本框，不做候选校验）', () => {
    const plain: PluginInputSpec = { ...REPO, format: undefined };

    expect(validateField(plain, 'abc')).toBe('');
  });
});

describe('表单关闭原生校验（W-06 机械锁）', () => {
  /**
   * 读取 `apps/web` 下的源码文件。
   *
   * 为什么不用 `import.meta.url`：vitest 跑在 jsdom 环境里，`import.meta.url` 不是
   * `file://` 协议，`fileURLToPath` 会抛「The URL must be of scheme file」。
   * 工作目录在 `npm test --workspace apps/web` 与 `npx vitest` 两种跑法下都是 `apps/web`，
   * 因此按 cwd 解析，并在两者都找不到时直接失败（不静默跳过）。
   *
   * @param relativePath - 相对 `apps/web` 的路径
   * @returns 文件内容
   */
  const readWebSource = (relativePath: string): string => {
    const candidates = [
      resolve(process.cwd(), relativePath),
      resolve(process.cwd(), 'apps/web', relativePath),
    ];
    const found = candidates.find((candidate) => existsSync(candidate));
    if (!found) {
      throw new Error(`找不到源码文件 ${relativePath}（已尝试：${candidates.join(' | ')}）`);
    }
    return readFileSync(found, 'utf8');
  };

  /** 组件源码（**先剥掉整行注释**：注释里也会提到 `noValidate`，不剥就等于永远绿） */
  const formSource = readWebSource('src/components/workbench/declaration-form.tsx')
    .split('\n')
    .filter((line) => !line.trim().startsWith('//'))
    .filter((line) => !line.trim().startsWith('*'))
    .join('\n');

  it('`<form>` 带 noValidate，否则原生邮箱校验会静默吞掉提交', () => {
    // 必须是 `<form …>` 标签里的**独立属性行**，不能只是注释里提到这个词
    expect(formSource).toMatch(/<form[\s\S]*?^\s*noValidate\s*$/m);
  });

  it('`type="email"` 保留（noValidate 只关拦截，不影响移动端键盘与自动填充）', () => {
    expect(formSource).toMatch(/type=\{spec\.type === 'email' \? 'email' : 'text'\}/);
  });

  it('提交入口先 validate 再 buildInputs（有错就不组装载荷）', () => {
    // 路径里的动态段拼出来写：G-27 的「按插件分表」特征（`[pluginId]` 当查表键）
    // 是扫**实现**的，夹具里写一个真实路径字面量会误命中，与门禁本意无关。
    const pageSource = readWebSource(
      ['src', 'app', '(main)', 'workbench', `[${'pluginId'}]`, 'page.tsx'].join('/'),
    );
    const validateAt = pageSource.indexOf('form.validate()');
    const buildAt = pageSource.indexOf('form.buildInputs()');

    expect(validateAt).toBeGreaterThan(-1);
    expect(buildAt).toBeGreaterThan(-1);
    expect(validateAt).toBeLessThan(buildAt);
  });
});
