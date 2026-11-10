# AdBlock Center / 墨鱼去广告规则中心

一个面向 PopCat 的规则汇集、审查、构建与更新站点。

## 项目目标

- 尽可能完整地归档上游通用广告规则和 APP 专项规则。
- 分开保存原始规则与经过 PopCat 兼容性审查的模块。
- Apple Intelligence 使用独立分流片段，默认策略名为「默认」。
- 每天自动检查上游变更，记录来源、文件、SHA 和更新时间。
- 不将 QX/Surge 原始指令误标为 PopCat 兼容规则。

## 状态定义

| 状态 | 含义 |
|---|---|
| 上游已同步 | 已从原始上游获取并归档 |
| 待转换 | 原始语法尚未适配 PopCat |
| 待实测 | 已完成转换但尚未在真实设备验证 |
| PopCat 已验证 | 已经在目标 PopCat 版本完成实际验证 |
| 已发布 | 已进入公开下载目录 |

同步成功不代表兼容性通过。


## 本地目录

- `index.html`：规则中心首页
- `downloads/`：公开下载模块
- `site/sources/`：上游原始文件归档
- `site/data/manifest.json`：每日更新清单
- `scripts/sync_sources.py`：上游同步脚本
- `.github/workflows/daily-sync-pages.yml`：每日同步和 Pages 部署

## 重要说明

1. 项目追求高拦截覆盖率，但无法保证拦截所有服务端动态广告。
2. 高风险/功能性请求阻断会单独标注，避免误将失效或过期规则作为已验证规则。
3. Apple Intelligence 分流片段不启用 HTTPS MITM；策略组名称必须与本地客户端配置一致。
4. 原始上游规则可能包含 Quantumult X 专有语法。未经转换和实测，不应直接导入 PopCat。

## 每日更新

GitHub Actions 每天 04:00 UTC（新加坡时间 12:00）检查上游资源并部署网站。也可在 Actions 页面手动运行。
