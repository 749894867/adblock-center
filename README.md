# AdBlock Center / 墨鱼去广告规则中心

一个面向 PopCat 的广告拦截规则订阅。用户只需导入一次固定 HTTPS 地址，GitHub Actions 每天同步上游并重建该地址背后的规则文件。

## 固定订阅地址

`https://749894867.github.io/adblock-center/downloads/AdBlock-Center.sgmodule`

固定文件名和 URL 不变。每日更新时，服务器覆盖同一文件；客户端通过自己的订阅更新/刷新机制获取最新规则，不需要删除后重新导入。

## 更新机制

- 每天 20:00 UTC（新加坡时间次日 04:00）执行上游同步。
- `scripts/sync_sources.py` 抓取并归档上游文件。
- `scripts/build_subscription.py` 从原始文件中提取 PopCat 可表达的域名规则，生成远程分片，并将 Bilibili 响应清理脚本镜像到同一 GitHub Pages 域名。
- `scripts/vendor/` 保存 Bilibili 脚本的已验证回退副本；正常情况下每日优先同步 app2smile 上游最新版。
- GitHub Actions 提交新内容并部署 GitHub Pages。
- 订阅地址长期固定：`/downloads/AdBlock-Center.sgmodule`。

## 规则转换范围

当前自动编译进入 PopCat 外部 `RULE-SET` 的规则类型为：

- `DOMAIN`
- `DOMAIN-SUFFIX`
- `DOMAIN-KEYWORD`

每个远程规则分片控制在约 500 KB，并通过独立 `RULE-SET` 引用。上游列表中没有明确指定策略的广告规则会统一补上 `REJECT`；明确为 DIRECT/PROXY 等非拦截动作的规则不会并入订阅。QX 简单的 `host` / `host-suffix` 规则会尝试转换为对应域名规则。`URL-REGEX` 不属于 PopCat 外部规则列表支持的类型，因此不会放入远程分片；需要按 URL 路径拦截的 Bilibili 规则写在模块自身的 `[Rule]` 中，并由 MITM 主机范围支持。

## Bilibili 专项净化

广告模块已集成 app2smile 的 Surge 格式 Bilibili 规则和两份响应脚本，覆盖开屏页、推荐页、底部 Tab、视频详情页，以及 gRPC 动态 / UP 主推荐内容；另针对弹幕广告接口增加路径拦截。脚本文件由本仓库每日同步并通过固定 GitHub Pages 地址提供，避免模块依赖第三方脚本地址临时失效。

**Bilibili 响应净化必须启用 PopCat 的 HTTPS 解密**，并安装、完全信任 PopCat 根证书。模块仅把 `app.bilibili.com`、`grpc.biliapi.net`、`api.bilibili.com` 加入解密范围，不使用全域 `*` MITM。iOS 安装证书描述文件后，还要在「设置 → 通用 → 关于本机 → 证书信任设置」中手动打开完全信任。

在 PopCat 的「海外代理」模式下，所有启用模块都会生效；本模块不需要、也不应开启「全局模式下生效」。全局代理模式下只有手动打开该选项的模块才参与分流。HTTPS 解密对使用证书固定的 App 可能失败，解密脚本若报错可在 PopCat 连接日志中查看。

## 暂不自动转换的内容

除已单独适配的 Bilibili 专项外，Quantumult X 的 `script-response-body`、`jsonjq-response-body`、脚本参数、MITM 相关配置不能直接等同于普通域名拦截规则，仍保留原始归档，等待逐项适配和实测。
- 墨鱼 Rewrite 中的 `.conf` 多数是 Quantumult X 重写配置，不是可直接放进 Surge `[Script]` 的 JavaScript 文件；不能把它们作为 `script-path` 使用。需要先按客户端语法转换，并单独验证脚本兼容性。IP 规则、URL 路径正则及复杂 AND/OR 规则也暂不自动并入，以降低误拦风险。

## Apple Intelligence

Blackmatrix7 主分支已不存在旧地址 `rule/Surge/AppleIntelligence/AppleIntelligence.list`。现在由 `scripts/build_apple_intelligence.py` 每天读取 [Apple 官方企业网络支持文档](https://support.apple.com/en-us/101555) 中的 Apple Intelligence、Siri 与 Search 主机表，生成独立分流模块；页面结构临时变化或无法访问时，使用最后验证过的官方主机列表作为回退，并在 manifest 中标记来源模式。不会把范围过广的 Apple 全量规则误当成 AI 专用规则。

继续独立维护，不合并进广告订阅：

`https://749894867.github.io/adblock-center/downloads/Apple-Intelligence-Route.sgmodule`

策略组默认名称为「默认」，需与 PopCat 中实际存在的策略名称一致。若要改为「美国节点」，在 GitHub 仓库 Settings → Secrets and variables → Actions → Variables 中添加 `APPLE_INTELLIGENCE_POLICY`，值设为 `美国节点`。本模块不包含 MITM。

## 重要说明

- 同步成功不等于所有广告都能拦截，也不等于每个 APP 都已实测。
- 极限拦截可能影响登录、搜索、定位、播放或支付功能。
- 上游原始文件保留来源和 SHA256，便于审查及回滚。
- PopCat 是否自动刷新远程模块由客户端自身设置决定；服务器端固定地址会每日更新。
