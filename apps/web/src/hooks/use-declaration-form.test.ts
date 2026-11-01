import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { useEffect, useState } from 'react';
import { describe, expect, it } from 'vitest';

import type { PluginInputFormat, PluginInputSpec } from '@/lib/types';
import { act, renderHook } from '@/test/render-hook';

import {
  collectFieldErrors,
  mergeMissingDefaults,
  useDeclarationForm,
  validateField,
} from './use-declaration-form';

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

// ---------------------------------------------------------------------------
// 补测第三十三批（审计 2026-10-31 H1 续）：hook 状态机与剩余校验分支
// ---------------------------------------------------------------------------

/** 探针：用自带 state 模拟「声明异步到达 / 目标预填到达」两条时序 */
let setProbeInputs: (inputs: PluginInputSpec[]) => void = () => {};
let setProbeInitial: (initial: string | undefined) => void = () => {};

function useFormProbe(): ReturnType<typeof useDeclarationForm> {
  const [inputs, setInputs] = useState<PluginInputSpec[]>([]);
  const [initial, setInitial] = useState<string | undefined>(undefined);
  // 发布控制柄放 effect 里：渲染期不得改组件外变量（react-hooks/globals）；
  // setter 引用稳定，挂载时发布一次即可
  useEffect(() => {
    setProbeInputs = setInputs;
    setProbeInitial = setInitial;
  }, []);
  return useDeclarationForm(inputs, initial);
}

const FORM_SPECS: PluginInputSpec[] = [
  { key: 'goal', label: '目标', type: 'text', required: true, max_length: 20 },
  { key: 'tags', label: '标签', type: 'multi_select', required: true, options: ['a', 'b'] },
  { key: 'docs', label: '资料', type: 'file', required: true },
  { key: 'mode', label: '模式', type: 'select', required: true, options: ['简', '详'] },
  { key: 'bare', label: '裸下拉', type: 'select', required: false },
];

describe('useDeclarationForm 状态机', () => {
  it('初始值按声明分型，hasFileInput 只认文件类', () => {
    const { result, unmount } = renderHook(() => useFormProbe());
    act(() => setProbeInputs(FORM_SPECS));

    expect(result.current.values.tags).toEqual([]);
    expect(result.current.values.docs).toEqual([]);
    expect(result.current.values.mode).toBe('简');
    // 无候选下拉给空串（界面降级成文本框）
    expect(result.current.values.bare).toBe('');
    expect(result.current.hasFileInput).toBe(true);
    unmount();
  });

  it('setValue 写值并顺手清掉该字段旧错误', () => {
    const { result, unmount } = renderHook(() => useFormProbe());
    act(() => setProbeInputs(FORM_SPECS));

    act(() => {
      result.current.validate();
    });
    expect(result.current.errors.goal).toBeTruthy();

    act(() => {
      result.current.setValue('goal', '写完了');
    });
    expect(result.current.values.goal).toBe('写完了');
    expect(result.current.errors.goal).toBeUndefined();
    unmount();
  });

  it('validate 全量校验写错误表；改对后放行', () => {
    const { result, unmount } = renderHook(() => useFormProbe());
    act(() => setProbeInputs(FORM_SPECS));

    let passed = true;
    act(() => {
      passed = result.current.validate();
    });
    expect(passed).toBe(false);
    // 必填为空的字段都进错误表
    expect(Object.keys(result.current.errors).sort()).toEqual(['docs', 'goal', 'tags']);

    act(() => {
      result.current.setValue('goal', '整理周报');
      result.current.setValue('tags', ['a']);
      result.current.setValue('docs', ['doc_1']);
    });
    act(() => {
      passed = result.current.validate();
    });
    expect(passed).toBe(true);
    expect(result.current.errors).toEqual({});
    unmount();
  });

  it('reset 回到初始值并清空错误', () => {
    const { result, unmount } = renderHook(() => useFormProbe());
    act(() => setProbeInputs(FORM_SPECS));
    act(() => {
      result.current.validate();
      result.current.setValue('goal', '临时内容');
    });

    act(() => {
      result.current.reset();
    });
    expect(result.current.values.goal).toBe('');
    expect(result.current.values.mode).toBe('简');
    expect(result.current.errors).toEqual({});
    unmount();
  });

  it('buildInputs 组装口径：trim、数组原样、非字符串给空', () => {
    const { result, unmount } = renderHook(() => useFormProbe());
    act(() => setProbeInputs(FORM_SPECS));
    act(() => {
      result.current.setValue('goal', '  整理周报  ');
      result.current.setValue('tags', ['a', 'b']);
      result.current.setValue('docs', ['doc_1']);
    });

    let payload: Record<string, unknown> = {};
    act(() => {
      payload = result.current.buildInputs();
    });
    expect(payload).toEqual({
      goal: '整理周报',
      tags: ['a', 'b'],
      docs: ['doc_1'],
      mode: '简',
      bare: '',
    });
    unmount();
  });

  it('目标预填补进首个文本输入，且不覆盖用户已填', () => {
    const { result, unmount } = renderHook(() => useFormProbe());
    act(() => setProbeInputs(FORM_SPECS));
    act(() => setProbeInitial('整理本周周报'));
    expect(result.current.values.goal).toBe('整理本周周报');

    // 用户改过之后，同一目标不再合入
    act(() => {
      result.current.setValue('goal', '我自己的目标');
      result.current.setValue('goal', '');
    });
    act(() => setProbeInitial('整理本周周报'));
    expect(result.current.values.goal).toBe('');
    unmount();
  });

  it('目标先到声明后到也能合入；清空后换目标再合一次', () => {
    const { result, unmount } = renderHook(() => useFormProbe());
    act(() => setProbeInitial('第一个目标'));
    act(() => setProbeInputs(FORM_SPECS));
    expect(result.current.values.goal).toBe('第一个目标');

    // 用户清空后新的目标到达 → 合入新目标（每个目标只合一次）
    act(() => {
      result.current.setValue('goal', '');
    });
    act(() => setProbeInitial('第二个目标'));
    expect(result.current.values.goal).toBe('第二个目标');
    unmount();
  });

  it('声明异步到达补齐默认值（hook 路径）', () => {
    const { result, unmount } = renderHook(() => useFormProbe());
    expect(result.current.values).toEqual({});

    act(() => setProbeInputs(FORM_SPECS));
    expect(result.current.values.mode).toBe('简');
    expect(result.current.values.goal).toBe('');
    unmount();
  });
});

describe('validateField 剩余分支', () => {
  const text = (over: Partial<PluginInputSpec> = {}): PluginInputSpec => ({
    key: 'title',
    label: '需求标题',
    type: 'text',
    required: false,
    max_length: 5,
    ...over,
  });

  it('文本超长按声明上限提示', () => {
    expect(validateField(text(), '六个字的标题')).toBe('需求标题最多 5 个字');
    expect(validateField(text(), '五字内')).toBe('');
  });

  it('多选含无效选项提示，非数组值按空处理', () => {
    const spec: PluginInputSpec = {
      key: 'tags',
      label: '标签',
      type: 'multi_select',
      required: true,
      options: ['a', 'b'],
    };
    expect(validateField(spec, ['a', 'c'])).toBe('标签包含无效选项');
    // 非数组在候选检查里按空处理：不报「候选无效」（报错会指向不存在的选项）
    expect(validateField(spec, 'a' as never)).toBe('');
  });

  it('文件类必填按数组判空，有值即通过', () => {
    const spec: PluginInputSpec = { key: 'docs', label: '资料', type: 'file', required: true };
    expect(validateField(spec, [])).toBe('请填写资料');
    expect(validateField(spec, ['doc_1'])).toBe('');
  });

  it('collectFieldErrors 一次写全所有错误', () => {
    const errors = collectFieldErrors(FORM_SPECS, {
      goal: '',
      tags: ['zz'],
      docs: [],
      mode: '简',
      bare: '',
    });
    expect(errors.goal).toBe('请填写目标');
    expect(errors.tags).toBe('标签包含无效选项');
    expect(errors.docs).toBe('请填写资料');
    // 通过的字段不进错误表
    expect(errors.mode).toBeUndefined();
  });
});
