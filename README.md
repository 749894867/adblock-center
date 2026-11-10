# AdBlock Center / 墨鱼去广告规则中心

一个面向 PopCat 的广告拦截规则订阅。用户只需导入一次固定 HTTPS 地址，GitHub Actions 每天同步上游并重建该地址背后的规则文件。

## 固定订阅地址

`https://749894867.github.io/adblock-center/downloads/AdBlock-Center.sgmodule`

固定文件名和 URL 不变。每日更新时，服务器覆盖同一文件；客户端通过自己的订阅更新/刷新机制获取最新规则，不需要删除后重新导入。

## 更新机制

- 每天 20:00 UTC（新加坡时间次日 04:00）执行上游同步。
- `scripts/sync_sources.py` 抓取并归档上游文件。
- `scripts/build_subscription.py` 从原始文件中提取可安全表达的域名/URL 拦截规则，统一生成一个 Surge 风格模块。
- GitHub Actions 提交新内容并部署 GitHub Pages。
- 订阅地址长期固定：`/downloads/AdBlock-Center.sgmodule`。

## 规则转换范围

当前自动编译的规则类型为：

- `DOMAIN`
- `DOMAIN-SUFFIX`
- `DOMAIN-KEYWORD`
- `URL-REGEX`

上游列表中没有明确指定策略的广告规则会统一补上 `REJECT`；明确为 DIRECT/PROXY 等非拦截动作的规则不会并入订阅。QX 简单的 `host` / `host-suffix` / `url reject-200` 规则会尝试转换为对应路由规则。

## 暂不自动转换的内容

Quantumult X 的 `script-response-body`、`jsonjq-response-body`、脚本参数、MITM 相关配置不能直接等同于普通域名拦截规则，当前仅保留原始归档，等待单独适配和实测。
- 墨鱼 Rewrite 中的 `.conf` 多数是 Quantumult X 重写配置，不是可直接放进 Surge `[Script]` 的 JavaScript 文件；不能把它们作为 `script-path` 使用。需要先按客户端语法转换，并单独验证脚本兼容性。IP 规则及复杂 AND/OR 规则也暂不自动并入，以降低误拦风险。

## Apple Intelligence

当前 Blackmatrix7 主分支不存在旧地址 `rule/Surge/AppleIntelligence/AppleIntelligence.list`，因此不再依赖该失效地址；也不把覆盖范围很广的 Apple 全量规则误当成 AI 专用规则。

继续独立维护，不合并进广告订阅：

`https://749894867.github.io/adblock-center/downloads/Apple-Intelligence-Route.sgmodule`

策略组名称为「默认」，需与 PopCat 中实际存在的策略名称一致。

## 重要说明

- 同步成功不等于所有广告都能拦截，也不等于每个 APP 都已实测。
- 极限拦截可能影响登录、搜索、定位、播放或支付功能。
- 上游原始文件保留来源和 SHA256，便于审查及回滚。
- PopCat 是否自动刷新远程模块由客户端自身设置决定；服务器端固定地址会每日更新。
