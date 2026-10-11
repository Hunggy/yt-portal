# YT Portal

一个"设置一次就完事"的 YouTube 入口页。解决两件事：

1. **不用每次试哪个入口能用** —— 页面加载时自动探测一堆第三方前端，挑一个通的，并记住选择。下次直接进。
2. **不用打开 YouTube 也能看更新** —— GitHub Actions 在境外服务器上定时抓频道 RSS，生成静态页面。缩略图也存回仓库，所以连 `i.ytimg.com` 被拦也不影响看列表。

## 用起来

把 Pages 地址设为浏览器首页，之后就不需要记任何东西。

- 搜索框走的是当前选中的入口，不经过 `youtube.com` 首页。
- 视频链接指向第三方前端（`{入口}/watch?v=xxx`），不碰 `youtube.com`。
- 手动切入口：右上角下拉框，选择会存在 localStorage 里。

## 添加频道

点页面底部「＋ 添加频道」，会直接打开 `channels.json` 的编辑页。三种写法都认：

```json
{ "channels": [
  { "name": "可省略", "handle": "@某个频道" },
  { "id": "UCxxxxxxxxxxxxxxxxxxxxxx" },
  { "url": "https://www.youtube.com/@某个频道" }
] }
```

保存后 Actions 会在 6 小时内自动抓取；想立刻生效就去 **Actions → Update feed → Run workflow**。

## 备选入口

`instances.json` 里放的是常驻清单。每次运行时脚本还会自动去 Invidious 官方实例列表拉最新的合并进来，失效的会被探测剔除，不用手动维护。

## 直播板块

首页顶部的「直播」板块有两层：

1. **频道直播入口**（一直可用，不需要配置）：每个订阅频道一个 chip，点开就是该频道的直播页——在播就直接进直播，没播就落在频道主页。
2. **「正在直播」自动检测**（需要配置）：开赛时自动在首页列出正在直播的流。

第 2 项必须走官方接口，因为**云服务器 IP 做不了直播检测**：实测 YouTube 从 GitHub
runner 调 innertube player 一律返回 `LOGIN_REQUIRED`（拿不到 `isLiveNow`），
`hqdefault_live.jpg` 这个老判据也已经失效（普通视频同样返回 200、内容完全一样）。

启用方法（免费，两分钟）：

1. 到 [Google Cloud Console](https://console.cloud.google.com/) 建一个项目，启用 **YouTube Data API v3**。
2. 「凭据」→ 创建 **API 密钥**，复制。
3. 到仓库 `Settings → Secrets and variables → Actions → New repository secret`，
   名称填 `YT_API_KEY`，值粘贴密钥。
4. 下次运行（或手动 `Run workflow`）生效，首页 `data/meta.json` 里的 `ytApi` 会变成 `true`。

配额够用：`search.list` 每次 100 单位，免费额度每天 10000 单位，即每天 100 次查询。

## 平台直播源

`sources.json` 里放的是**跟 YouTube 无关**的直播入口，显示在首页「直播」板块最下面那一行。
给 YouTube 完全打不开的网络用——比如 KPL 的正规直播其实在虎牙 / 斗鱼 / 官网：

```json
{ "sources": [
  { "name": "虎牙 · KPL 官方间 660002", "url": "https://www.huya.com/660002", "tag": "虎牙" }
] }
```

想加什么就加什么。点「本机实测所有入口」时，这些源也会一起测可达性。

## 说明

- 仓库需要是 **public**，GitHub Free 才能开 Pages。有 Pro 的话可以转 private。
- 数据文件在 `data/`，缩略图在 `thumbs/`，都是 Actions 自动提交的，不用管。
- 如果页面上所有入口都显示不可用，说明是连接层硬拦（DNS / SNI / IP），换前端域名绕不过去，得改用 DoH 或换网络。
