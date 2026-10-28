import nextVitals from "eslint-config-next/core-web-vitals";

// Next 16 起 `next lint` 移除，改用扁平配置（2026-10-21 升级迁移；2026-10-28
// 实测口径修正：仓库现装 ESLint ^10.13.0——扁平配置自 9 起为默认，10 延续）。
// eslint-config-next 16 原生导出扁平配置数组（Linter.Config[]），直接展开。
//
// ENG-06（审计 2026-10-28）：本文件是前端 lint 豁免的**唯一注册处**——
// 当前全部豁免（rules 关闭项 + ignores 显式模式）的逐条理由与到期动作
// 见 docs/alignment-2026-10-23-tech-options.md §8「前端 Lint 豁免清单」。
// 新增/删除豁免必须两处同步；未登记的豁免一律视为违规。
const eslintConfig = [
  {
    ignores: [
      ".next/**",
      // 构建残留目录（Windows 上 .next 被占住时改名保留的副本）。
      // 与仓库根 `.gitignore` 保持**同一组显式模式**：
      // 刻意不用 `.next*` 通配——收得太宽会顺带忽略将来合法的源码目录；
      // 但 `.next-bak` 之后用前缀通配（`-` 可选），因为历史副本有带横线与不带横线两种命名。
      ".next-bak*/**",
      ".next.stale-*/**",
      "node_modules/**",
      "next-env.d.ts",
      "tsconfig.tsbuildinfo",
      "build.log",
    ],
  },
  ...nextVitals,
  {
    rules: {
      "no-console": ["warn", { allow: ["warn", "error", "info"] }],
      eqeqeq: ["error", "smart"],
      "prefer-const": "error",
      "no-var": "error",
      // React 19 新增的严格规则：拦截「effect 内同步 setState」。
      // 现有 6 处均为异步加载器的同步 loading 态，功能正确；
      // TODO(第二阶段 T03 前端收敛)：迁移至 react-query 后重新开启。
      "react-hooks/set-state-in-effect": "off",
    },
  },
];

export default eslintConfig;
