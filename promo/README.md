# 来信 · 网页与飞书

首页版长 32 秒。从在多个邮箱逐封寻找会议变更切入，呈现网页工作台与手机飞书两种入口。使用项目内置样例，网页镜头取自实际演示工作台，手机界面为流程示意。

| 时间 | 画面 | 内容 |
| --- | --- | --- |
| 0–5 秒 | QQ 与网易的样例收件箱切换 | 逐封打开，确认组会的更新 |
| 5–13 秒 | 网页邮件列表、优先事项、邮件原文 | 多邮箱集中查看，确认课程材料的行动与期限 |
| 13–19 秒 | 手机收到通知，打开飞书摘要 | 配对飞书并启用定时汇总后，在单聊查看优先事项和期限 |
| 19–27 秒 | 手机飞书单聊，连续问时间与地点 | 周五 16:00、B202，结果显示邮件来源 |
| 27–32 秒 | 网页与手机并排 | 网页看全局，手机随手查 |

配色延续工作台的米白和深橄榄。参考 Shotcraft 的 `radial-ripple-phone-chips` 错相波纹和手机入位，以及 `type-and-filter` 的输入节奏。手机摘要与追问的示例来自项目的[飞书说明](../docs/feishu.md)。两个入口的对话各自保留上下文。

## 编辑与渲染

在本目录运行 `npm ci`，再运行 `npm run dev` 进入 Remotion Studio。首页使用 `LaixinPromo`，此前 34 秒的桌面工作台概览保存在 `LaixinOverview` 合成中。首页分镜、文案和音效集中在 `src/timeline.tsx`，Shotcraft 动效工作台使用 `src/workbench.ts` 导入。

- `npm run render` 输出 1920 × 1080、30 fps 的视频。用 `npm run render:no-bgm` 输出仅保留拟音的版本。
- 更新首页时，将成片放到 `apps/web/src/assets/laixin-promo.mp4`，再重建 Vite 前端。封面和简体中文字幕也在该目录。
- 用 `npm run capture` 从 `http://127.0.0.1:8774` 的隔离 demo 进程重采网页素材。启动 demo 见 [演示说明](../docs/demo.md)。

配乐沿用原版原创音乐，并在 32 秒处淡出。先运行 `python3 scripts/score.py` 重建原曲，再用以下命令生成首页版配乐。

```sh
ffmpeg -i public/audio/laixin-score.mp3 -t 32 -af 'afade=t=out:st=29.8:d=2.2' -b:a 128k public/audio/laixin-dual-score.mp3
```

纸张拟音为两条 CC0 录音，见 [声音来源](public/audio/sfx/FREESOUND-CREDITS.md)和 [音频清单](public/audio/sfx/audio-manifest.json)。桌面概览中的页面相机改编自 Shotcraft，见 [Apache 2.0 许可](LICENSE-shotcraft)。
