# MediaPipe Vision Demo

MediaPipe 视觉模型功能演示：开摄像头后，用食指“隔空长按”HUD 卡片（0.5s 蓄力），实时切换 5 个模型并查看捕捉结果。UI 卡交互设计移植自 `ar-solar-system-demo`。

> 本项目与 `ar-solar-system-demo` 完全隔离：不部署、不迁回正式项目；后者仅只读参考。

## 演示的模型

| 卡片 | 模型 | 可视化 |
|---|---|---|
| 人脸检测 | Face Detector (BlazeFace) | HUD 人脸框 + 6 关键点 + 置信度 |
| 人脸网格 | Face Landmarker | 478 点面网格 + 轮廓 + blendshapes |
| 手势识别 | Gesture Recognizer | 21 点骨架 + 手势标签（7 类） |
| 手部关键点 | Hand Landmarker | 21 点骨架（左手青 / 右手金） |
| 人体姿态 | Pose Landmarker | 33 点全身骨架（按可见度过滤） |

另有一张独立卡片长按显示/隐藏关键点叠加层。左侧为模型介绍卡与实时数据面板（推理耗时 / 追踪频率 / 检测数 / 最高置信度 / GPU·CPU delegate）。

## 快速开始

```bash
npm install
npm run dev     # http://127.0.0.1:5174
```

首次 `dev`/`build` 会自动执行 `scripts/sync-mediapipe-assets.mjs`：从 node_modules 拷贝 wasm、从 Google 模型库下载 4 个模型（约 20MB）到 `public/mediapipe/`，之后运行时完全离线。若下载失败，按脚本提示手动下载放入 `public/mediapipe/models/`。

## 交互说明

- 常驻 Hand Landmarker（Web Worker）作为交互引擎——演示人脸模型时卡片蓄力照常可用
- 演示 Gesture Recognizer 时，交互层直接复用其输出的 21 点关键点，不双跑手部模型
- 所有卡片同时支持鼠标/触摸长按兜底（同样 0.5s 蓄力）
- 触发后指尖需离开卡片才能再次蓄力（防连触），配蓄力循环/成功/取消三段音效

## 与 ar-solar-system-demo 的关系

参考项目只读。本项目移植了它的：HUD 卡片全套样式与蓄力交互（`indexFingerUiHold`）、坐标映射（镜像 + contain）、手部平滑与左右手配对、HandLandmarker Worker 推理链路、音效资源。目录结构对齐，便于日后把演示代码迁回正式项目。
