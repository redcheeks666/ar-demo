# MediaPipe 视觉模型功能演示 产品需求文档

> 版本：v0.3（新增 Face Landmarker 3D 科幻头盔第一阶段验证）
> 参考项目：`ar-solar-system-demo`（**只读**，仅作为交互设计与代码参考来源；本项目所有产出写入 `ar-demo`）

> **项目边界（恒定约束）**：`ar-demo` 与 `ar-solar-system-demo` 是两个**完全隔离**的项目。本项目**不部署上线、不迁回正式项目**，仅作为独立算法演示。`ar-solar-system-demo` 全程**只读**，不向其写入任何内容。

## 1. 项目概述

一个独立的 Web 演示项目，用于向观众介绍并实时演示 MediaPipe Tasks Vision 的 5 个视觉模型：Face Detector、Face Landmarker、Gesture Recognizer、Hand Landmarker、Pose Landmarker。全部 UI 交互延续参考项目的 HUD 卡片"食指悬停蓄力"设计，并在 Face Landmarker 模式增加受控的 Three.js 透明叠加层，用程序生成的原创科幻头盔验证 3D 人脸跟随链路。该扩展是第一阶段 POC，不代表项目全面转型为 3D 内容平台。

## 2. 一句话定义

打开摄像头，用食指"隔空长按"科幻 HUD 卡片，在 5 个 MediaPipe 模型间切换并实时查看捕捉结果；进入 Face Landmarker 后可用显式按钮装备程序生成的 3D 头盔原型。

## 3. 目标与非目标

### 3.1 MVP 目标

- 5 个模型可通过 UI 卡蓄力交互随时切换，切换有加载态反馈
- 每个模型有：实时可视化叠加层、模型介绍卡
- 关键点/骨架叠加层可通过独立 UI 卡长按显示/隐藏（对应参考项目"长按显示手部模型"卡）
- 交互体验（卡片视觉、蓄力时长、音效、触发后防重复机制）与参考项目一致
- 运行时完全离线（wasm + 模型本地自托管）
- 验证 `Face Landmarker -> 面部姿态 -> 镜像/contain 坐标适配 -> Three.js 头盔根节点 -> 装配动画` 的完整链路
- 头盔随第一张脸的位置、远近尺度和三轴旋转平滑跟随，并可通过页面按钮确定性地装备或卸下

### 3.2 非目标

- Three.js 只允许用于 Face Landmarker 头盔跟随 POC，不扩展为通用 3D 场景或内容平台
- 第一阶段只使用程序生成的临时几何头盔，不接入正式 GLB/FBX，不追求电影级画质
- 不做纳米粒子生长、复杂 Shader、后处理 Bloom、环境反射、真实环境光照、完整脸部深度遮挡或正式装甲素材
- 不为头盔新增音效设计，不使用张嘴、眨眼、点头或语义手势/面部动作触发；只使用明确的页面按钮
- 不做移动端深度适配（以桌面 Chrome/Edge 现场演示为主，移动端可用即可）
- **不部署上线、不迁回 `ar-solar-system-demo`**；两项目完全隔离
- 不修改 / 不写入 `ar-solar-system-demo` 的任何文件（只读参考）
- 不做自定义模型训练、图片/视频文件输入（仅摄像头实时流）

## 4. 演示的 5 个模型

| 模型 | 输出 | 可视化叠加 | 介绍卡要点 |
|---|---|---|---|
| Face Detector | 人脸包围框 + 6 个关键点 + 置信度 | 人脸框（HUD 四角括号风格）、6 点、框上角置信度标签 | 轻量级人脸检测（BlazeFace），适合做"有没有脸/脸在哪"的前置判断 |
| Face Landmarker | 478 点面部网格 + 52 组 blendshapes + 可选面部变换矩阵 | 面部网格连线 + 关键点；头盔开启时叠加独立 3D canvas | 稠密面网格与变换矩阵可驱动 AR 贴纸、头戴物和虚拟形象表情 |
| Gesture Recognizer | 手势分类 + 21 点手部关键点 + 左右手 | 21 点骨架 + 画面内大号手势标签（Open_Palm、Victory、Thumb_Up 等 7 类）+ 置信度 | 内置手部关键点检测之上的手势分类器 |
| Hand Landmarker | 21 点手部关键点 + 左右手 + 世界坐标 | 21 点骨架，左手青色 `#7dd3fc` / 右手金色 `#f6c177`（与参考项目一致） | 参考项目 AR 太阳系全部交互的底层模型，本演示的"主角" |
| Pose Landmarker | 33 点全身姿态（含逐点可见度）+ 世界坐标 | 33 点骨架（BlazePose 连线），按可见度 ≥0.5 过滤绘制 | 全身姿态估计，用于健身动作分析、体感交互、动作捕捉；lite 版实时运行 |

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

### 6.6 界面折叠

独立 HUD 卡可折叠/恢复模型卡、介绍卡和关键点开关，折叠开关自身保持可用；折叠只影响界面与卡片命中，不停止摄像头、模型推理或已装备的 3D 头盔。

### 6.7 错误与降级

摄像头被拒 / wasm 加载失败 / 模型文件缺失时给出明确中文提示与重试入口；GPU delegate 失败自动回退 CPU。

### 6.8 Face Landmarker 3D 科幻头盔第一阶段 POC

- 头盔控制只在 Face Landmarker 模式显示，使用普通可访问按钮支持鼠标、触摸与键盘点击；不复用面部动作或语义手势触发
- 开启后按时间驱动约 1.15 秒装配：左右太阳穴/侧甲与面颊从两侧进入，额冠从上方进入，下巴从下方闭合，面罩与眼灯最后到位；关闭时按同一进度反向拆卸
- 快速重复点击通过连续进度正反播放，不产生互相竞争的动画实例；动画期间头盔根节点持续跟随
- 头盔包含 `HelmetRoot`、`Crown`、`TempleLeft/Right`、`CheekLeft/Right`、`Jaw`、`Visor`、`EyeLightLeft/Right` 独立节点，全部由基础几何体、材质和灯光在代码中生成
- 头盔为深色金属、青色发光与淡金边缘的原创抽象占位造型，不复制受保护影视角色轮廓、配色或标志
- 切换到其他模型时立即隐藏并重置为未装备；切回 Face Landmarker 后默认处于待机状态
- 2D 关键点开关只控制 landmark canvas，不影响 3D 头盔生命周期
- WebGL 初始化失败时给出中文降级提示，头盔按钮不可用，但其他 MediaPipe 演示尽可能保持工作

## 7. 技术方案

- **工程**：Vite + TypeScript，目录结构对齐参考项目（`src/tracking`、`src/interaction`、`src/rendering`）便于阅读；但本项目独立维护，不迁回正式项目
- **依赖**：`@mediapipe/tasks-vision ^0.10.35` 与 `three ^0.185.1`；Three.js 的使用范围仅限头盔 POC
- **资产自托管**：扩展参考项目 `sync-mediapipe-assets.mjs` 方案，predev/prebuild 时拷贝 wasm 并下载 5 个模型（合计约 30MB）到 `public/mediapipe/models/`：
  - `face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite`
  - `face_landmarker/face_landmarker/float16/1/face_landmarker.task`
  - `gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task`
  - `hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task`
  - `pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task`
- **推理架构**：交互引擎 Hand Landmarker 跑在 Worker（照搬参考项目 worker + ImageBitmap 方案）；演示模型主线程 VIDEO 模式运行，检测节流对齐参考项目（活跃 33ms / 空闲 120ms）
- **代码复用策略**：参考项目为只读，允许"读取并复制"其代码/样式/音效文件进入本项目，复制后独立维护
- **Face Landmarker 输出**：保留 `outputFaceBlendshapes: true`，同时开启 `outputFacialTransformationMatrixes: true`；无脸、矩阵缺失、landmark 数量异常和摄像头尺寸未就绪均按无有效新姿态处理
- **透明 WebGL 层**：视频与 2D landmark canvas 之间增加独立 Three.js canvas，`pointer-events: none`、透明清屏、DPR 最大 2；沿用应用唯一 `requestAnimationFrame` 循环，不创建第二个永久循环
- **姿态适配**：复用 `mapNormalizedToStagePixel` 的 contain 与自拍镜像口径；位置取额头/下巴/左右太阳穴多点中心，尺度取两侧脸宽，三轴旋转取列主序面部变换矩阵。自拍镜像对旋转使用 `S * R * S` 变换
- **相机与平滑**：使用正交相机，让世界 XY 与舞台 CSS 像素一一对应；位置/尺度采用基于 delta time 的指数平滑，旋转采用 Quaternion `slerp`，所有响应参数集中配置
- **丢脸策略**：短暂丢脸保留最后姿态 220ms，超过宽限后平滑淡出；重获人脸时平滑恢复。矩阵单帧缺失时沿用最后有效旋转，不输出 `NaN` 或无穷值
- **生命周期**：只有 Face Landmarker 且装配进度可见时才执行 WebGL 绘制；离开模式立即停止并清空，页面卸载时释放 renderer、geometry、material 与 WebGL context

## 8. 视觉设计

复刻参考项目 HUD 语言：深空底色、青 `#7dd3fc` / 金 `#f6c177` 双色系、细线框、四角括号、扫描线动效；中文主文案 + 英文 HUD 点缀（`READY`、`CHARGING`、`MODEL · XXX`）。样式变量与关键动画从参考项目 `styles.css` 摘取移植。

## 9. 验收标准

- 徒手（不碰鼠标）完成：开摄像头后切换全部 5 个模型、开关关键点叠加
- 每个模型的叠加可视化和介绍卡内容正确且随切换更新
- 断网环境下刷新页面可正常运行
- 蓄力交互手感与参考项目一致：500ms、进度环、防连触、音效齐全
- 桌面 Chrome / Edge 正常运行，30 FPS 摄像头下交互无明显卡顿
- Face Landmarker 模式显示独立头盔按钮；装备/卸下动画完整，快速重复点击可稳定反向
- 用户在镜像画面左右移动、靠近/远离、缓慢转头及抬头/低头时，头盔的位置、尺度和方向与画面一致
- 单帧丢脸不闪烁，持续丢脸后平滑隐藏；窗口尺寸变化后仍按 contain 区域对齐
- 切换其他模型时头盔立即隐藏，切回后默认未装备；关闭 2D landmark overlay 不影响已装备头盔
- WebGL 不可用时清晰降级，原有五模型切换、交互引擎、2D overlay、音效和错误处理尽可能不受影响
- `npm run typecheck` 与 `npm run build` 均通过，不使用 `any`、`@ts-ignore` 或关闭检查规避错误

## 10. 里程碑

- **M1 工程骨架**：Vite 工程 + 摄像头 + 常驻手部交互引擎 + UI 卡蓄力交互（含鼠标兜底）
- **M2 四模型接入**：模型切换 + 各自可视化叠加 + 关键点开关卡
- **M3 打磨**：介绍卡 + 界面折叠 + 音效 + 加载/错误态 + 验收走查
- **M4 Face Landmarker 3D 头盔跟随 POC**：透明 WebGL 层 + 面部姿态适配 + 程序几何头盔 + 可逆装配动画 + 平滑/丢脸/生命周期处理
