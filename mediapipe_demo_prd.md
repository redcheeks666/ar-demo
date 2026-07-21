# MediaPipe 视觉模型功能演示 产品需求文档

> 版本：v0.2（新增 Pose Landmarker）
> 参考项目：`ar-solar-system-demo`（**只读**，仅作为交互设计与代码参考来源；本项目所有产出写入 `ar-demo`）

> **项目边界（恒定约束）**：`ar-demo` 与 `ar-solar-system-demo` 是两个**完全隔离**的项目。本项目**不部署上线、不迁回正式项目**，仅作为独立算法演示。`ar-solar-system-demo` 全程**只读**，不向其写入任何内容。

## 1. 项目概述

一个独立的 Web 演示项目，用于向观众介绍并实时演示 MediaPipe Tasks Vision 的 5 个视觉模型：Face Detector、Face Landmarker、Gesture Recognizer、Hand Landmarker、Pose Landmarker。全部 UI 交互复刻参考项目的 HUD 卡片"食指悬停蓄力"设计，演示者可全程徒手操作，不接触鼠标键盘。

## 2. 一句话定义

打开摄像头，用食指"隔空长按"科幻 HUD 卡片，在 5 个 MediaPipe 模型间切换并实时查看它们各自的捕捉结果。

## 3. 目标与非目标

### 3.1 MVP 目标

- 5 个模型可通过 UI 卡蓄力交互随时切换，切换有加载态反馈
- 每个模型有：实时可视化叠加层、模型介绍卡、实时数据面板
- 关键点/骨架叠加层可通过独立 UI 卡长按显示/隐藏（对应参考项目"长按显示手部模型"卡）
- 交互体验（卡片视觉、蓄力时长、音效、触发后防重复机制）与参考项目一致
- 运行时完全离线（wasm + 模型本地自托管）

### 3.2 非目标

- 不做 3D 场景（不引入 three.js）
- 不做移动端深度适配（以桌面 Chrome/Edge 现场演示为主，移动端可用即可）
- **不部署上线、不迁回 `ar-solar-system-demo`**；两项目完全隔离
- 不修改 / 不写入 `ar-solar-system-demo` 的任何文件（只读参考）
- 不做自定义模型训练、图片/视频文件输入（仅摄像头实时流）

## 4. 演示的 5 个模型

| 模型 | 输出 | 可视化叠加 | 介绍卡要点 |
|---|---|---|---|
| Face Detector | 人脸包围框 + 6 个关键点 + 置信度 | 人脸框（HUD 四角括号风格）、6 点、框上角置信度标签 | 轻量级人脸检测（BlazeFace），适合做"有没有脸/脸在哪"的前置判断 |
| Face Landmarker | 478 点面部网格 + 52 组 blendshapes | 面部网格连线 + 关键点；数据面板展示 top blendshapes（如 eyeBlink、jawOpen） | 稠密面网格，驱动 AR 贴纸/虚拟形象表情 |
| Gesture Recognizer | 手势分类 + 21 点手部关键点 + 左右手 | 21 点骨架 + 画面内大号手势标签（Open_Palm、Victory、Thumb_Up 等 7 类）+ 置信度 | 内置手部关键点检测之上的手势分类器 |
| Hand Landmarker | 21 点手部关键点 + 左右手 + 世界坐标 | 21 点骨架，左手青色 `#7dd3fc` / 右手金色 `#f6c177`（与参考项目一致） | 参考项目 AR 太阳系全部交互的底层模型，本演示的"主角" |
| Pose Landmarker | 33 点全身姿态（含逐点可见度）+ 世界坐标 | 33 点骨架（BlazePose 连线），按可见度 ≥0.5 过滤绘制；数据面板显示平均可见度 | 全身姿态估计，用于健身动作分析、体感交互、动作捕捉；lite 版实时运行 |

## 5. 核心交互：UI 卡蓄力（复刻参考项目规格）

### 5.1 卡片视觉结构

复刻 `planet-focus-action-card`：四角括号 + 边线 + 填充层 + 扫描线 + 反应堆圆环（含进度环）+ 图标 + 中文主标签 + 英文 HUD 副标签（如 `MODEL · FACE LANDMARKER`）+ 右侧读数（`T-0.5s`、`READY`/`CHARGING` 状态切换）。

### 5.2 交互规则（与参考项目 `indexFingerUiHold` 逻辑一致）

- 食指指尖（镜像映射后的舞台坐标）进入卡片矩形（含 hitSlop 容差）即开始蓄力，卡片进入 `is-charging` 态，进度写入 CSS 变量 `--focus-action-progress` 驱动圆环
- 蓄力时长默认 500ms，满格触发
- 触发后进入 needsClear 状态：指尖必须离开卡片后才能再次蓄力，防止误连触
- 换手或指尖离开则蓄力取消归零
- 音效：蓄力循环音 / 触发成功音 / 取消音（音效文件可从参考项目 `assets/solar/sfx` 复制到本项目）

### 5.3 交互引擎（已确认方案）

- **Hand Landmarker 常驻**作为交互引擎（Web Worker 内推理），保证演示人脸模型时卡片交互不失效
- **鼠标/触摸长按兜底**：按住卡片同样走 500ms 蓄力动画，保证无摄像头手部时可操作
- 性能优化：演示 Hand Landmarker 时交互引擎与演示层共用同一实例结果；演示 Gesture Recognizer 时直接用其输出的 21 点关键点喂交互引擎（该模型内置手部关键点，无需双跑手模型）；仅演示两个人脸模型时才真正双模型并行

## 6. 功能范围（MVP）

### 6.1 开始页

开启摄像头按钮 + 隐私说明（画面仅本地推理，不上传），复用参考项目 `intro-panel` 文案风格。

### 6.2 模型切换卡组

屏幕一侧纵向排列 4 张模型卡，蓄力任意一张即切换；当前激活卡常亮高亮态（新增 `is-active` 态），副标签显示 `ACTIVE`。切换期间显示加载态（复用参考项目 loading-panel 模式），加载完成前旧模型继续渲染。

### 6.3 关键点显示开关卡

独立一张卡（对应参考项目 `demo-hand-model-button`）：长按显示/隐藏当前模型的捕捉叠加层，副标签 `LANDMARKS · ON/OFF`。默认开启。

### 6.4 可视化叠加层

单个 2D canvas，镜像 + `object-fit: contain` 坐标映射（复用参考项目 `coordinateMapping` / `LandmarkOverlay` 方案），按当前模型绘制第 4 节所述内容。

### 6.5 模型介绍卡

固定信息面板显示当前模型：名称（中英）、用途、输出说明、一句话原理。随切换更新。

### 6.6 实时数据面板

HUD 风格小面板：推理耗时 ms、追踪频率 Hz、检测数量（脸数/手数）、最高置信度、delegate（GPU/CPU）。数据源对齐参考项目 `MediaPipePerformanceSnapshot` 的口径。

### 6.7 错误与降级

摄像头被拒 / wasm 加载失败 / 模型文件缺失时给出明确中文提示与重试入口；GPU delegate 失败自动回退 CPU。

## 7. 技术方案

- **工程**：Vite + TypeScript，目录结构对齐参考项目（`src/tracking`、`src/interaction`、`src/rendering`）便于阅读；但本项目独立维护，不迁回正式项目
- **依赖**：`@mediapipe/tasks-vision ^0.10.35`（与参考项目同版本）；无 three.js
- **资产自托管**：扩展参考项目 `sync-mediapipe-assets.mjs` 方案，predev/prebuild 时拷贝 wasm 并下载 5 个模型（合计约 30MB）到 `public/mediapipe/models/`：
  - `face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite`
  - `face_landmarker/face_landmarker/float16/1/face_landmarker.task`
  - `gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task`
  - `hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task`
  - `pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task`
- **推理架构**：交互引擎 Hand Landmarker 跑在 Worker（照搬参考项目 worker + ImageBitmap 方案）；演示模型主线程 VIDEO 模式运行，检测节流对齐参考项目（活跃 33ms / 空闲 120ms）
- **代码复用策略**：参考项目为只读，允许"读取并复制"其代码/样式/音效文件进入本项目，复制后独立维护

## 8. 视觉设计

复刻参考项目 HUD 语言：深空底色、青 `#7dd3fc` / 金 `#f6c177` 双色系、细线框、四角括号、扫描线动效；中文主文案 + 英文 HUD 点缀（`READY`、`CHARGING`、`MODEL · XXX`）。样式变量与关键动画从参考项目 `styles.css` 摘取移植。

## 9. 验收标准

- 徒手（不碰鼠标）完成：开摄像头后切换全部 5 个模型、开关关键点叠加
- 每个模型的叠加可视化、介绍卡、数据面板内容正确且随切换更新
- 断网环境下刷新页面可正常运行
- 蓄力交互手感与参考项目一致：500ms、进度环、防连触、音效齐全
- 桌面 Chrome / Edge 正常运行，30 FPS 摄像头下交互无明显卡顿

## 10. 里程碑

- **M1 工程骨架**：Vite 工程 + 摄像头 + 常驻手部交互引擎 + UI 卡蓄力交互（含鼠标兜底）
- **M2 四模型接入**：模型切换 + 各自可视化叠加 + 关键点开关卡
- **M3 打磨**：介绍卡 + 数据面板 + 音效 + 加载/错误态 + 验收走查
