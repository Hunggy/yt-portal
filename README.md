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

## 说明

- 仓库需要是 **public**，GitHub Free 才能开 Pages。有 Pro 的话可以转 private。
- 数据文件在 `data/`，缩略图在 `thumbs/`，都是 Actions 自动提交的，不用管。
- 如果页面上所有入口都显示不可用，说明是连接层硬拦（DNS / SNI / IP），换前端域名绕不过去，得改用 DoH 或换网络。
