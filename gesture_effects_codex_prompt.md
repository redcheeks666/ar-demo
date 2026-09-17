# Codex 任务：为 Gesture Recognizer 演示模式实现手势触发特效

> 这是一份交给 Codex 执行的实现规范。请严格按本文档在 `ar-demo` 项目内实现。先完成 **Phase 1（2D 粒子特效，必做）**，通过验收后再按需做 **Phase 2（three.js 辉光升级，可选）**。

---

## 0. 角色与总体要求

你是在 `ar-demo` 这个 Vite + TypeScript 项目里工作。目标：在 **Gesture Recognizer 演示模式**下，让 MediaPipe 识别到的 7 种手势各自触发一套锚定在手上的视觉特效，随手移动、随手势出现/消失，并处理好抖动与误触。

硬约束（务必遵守）：

- **只改 `ar-demo`**。同机另有一个 `ar-solar-system-demo` 项目，**只读、禁止写入/修改**它的任何文件。
- 本项目**不部署、不迁移**，是独立演示。
- TypeScript `strict` 已开，且 `noUnusedLocals` / `noUnusedParameters` 开启。**不要引入 `any`**，不要留未使用变量。
- 依赖已就绪：`@mediapipe/tasks-vision`、`three`（Phase 2 用）。**Phase 1 不得新增任何依赖**。
- 完成后必须 `npm run build`（= `tsc --noEmit && vite build`）零错误通过。运行时保持完全离线（不新增任何 CDN/网络请求）。
- 沿用现有代码风格：类字段 `private readonly`、私有方法、中文注释可保留、模块内聚。

---

## 1. 现有架构与集成点（先读这些文件再动手）

关键文件：

- `src/app.ts` —— `DemoApp` 类，主循环 `tick(timestamp)` 在这里。
- `src/tracking/demoModels.ts` —— `DemoModelController`，`update()` 每帧返回 `DemoDetection | null`；手势模式返回 `{ kind: 'gesture_recognizer', result: GestureRecognizerResult }`。
- `src/tracking/interactionEngine.ts` —— 常驻手部交互引擎；手势模式下会被 `setSuspended(true)`，并由 app 用手势识别器的关键点 `injectHands(...)` 回填。
- `src/rendering/overlayRenderer.ts` —— 在 `overlay-canvas` 上画骨架/关键点。含 `getContainRect()`。**特效不要塞进这里**，另建独立层。
- `src/interaction/coordinateMapping.ts` —— `mapNormalizedToStagePixel(point, layout)` 与 `PreviewLayout`（镜像 + object-fit:contain 感知）。**所有归一化坐标→屏幕像素必须走它**。
- `src/interaction/handLandmarks.ts` —— 手部 landmark 索引常量（`WRIST_INDEX=0`、`THUMB_TIP_INDEX=4`、`INDEX_MCP_INDEX=5`、`INDEX_TIP_INDEX=8`、`MIDDLE_MCP_INDEX=9`、`RING_MCP_INDEX=13`、`PINKY_MCP_INDEX=17`）。你可以在此补充 `INDEX_PIP_INDEX=6`、`MIDDLE_TIP_INDEX=12`。
- `index.html` / `src/styles.css` —— 舞台 DOM 与样式。

`tick(timestamp)` 现有流程（简述，务必先读实际代码确认）：

1. `const layout = this.computeLayout()` —— 返回 `PreviewLayout`（`mirrored: true`）。
2. `const demo = this.demoModels.update(this.video, timestamp)`。
3. 手势模式：`this.engine.setSuspended(true)`；当 `demo.kind==='gesture_recognizer'` 且有新结果时，`this.engine.injectHands(this.gestureProcessor.process(gestureResultToRawHands(demo.result), timestamp))`。
4. `const snapshot = this.engine.getSnapshot()`。
5. UI 卡蓄力：遍历 `this.holdButtons` 更新。
6. `this.overlay.draw(this.buildOverlayContent(demo, snapshot))`。

**特效数据来源**：手势模式下 `demo.result`（`GestureRecognizerResult`）就是每帧的手势结果，含：

- `result.gestures[i]`：第 i 只手的手势分类数组，取 `[0]` 得 `{ categoryName, score }`。
- `result.landmarks[i]`：第 i 只手的 21 个图像归一化关键点 `{x,y,z}`。
- `result.handedness[i][0].categoryName`：`'Left'|'Right'`（原始 MediaPipe 标签）。

手势类别名（MediaPipe 固定）：`Closed_Fist`、`Open_Palm`、`Pointing_Up`、`Thumb_Up`、`Thumb_Down`、`Victory`、`ILoveYou`、`None`。

---

## 2. 需要新增 / 修改的文件

**新增：**

- `src/effects/effectsLayer.ts` —— 自包含的 2D 粒子特效层（拥有自己的 canvas，负责 spawn/update/render/clear/resize）。
- `src/effects/gestureEffectsController.ts` —— 消费每帧手势结果，跑触发状态机，把归一化关键点映射为屏幕像素后调用 `effectsLayer` 生成特效。
- （Phase 2）`src/effects/effectsLayer3d.ts` —— three.js 版辉光层（可选）。

**修改：**

- `index.html` —— 新增 `<canvas id="effects-canvas" class="effects-canvas" hidden></canvas>`，放在 `overlay-canvas` 之后、`preview-overlay` 之前。
- `src/styles.css` —— 新增 `.effects-canvas { position:absolute; inset:0; width:100%; height:100%; z-index:2; pointer-events:none; }`（须在骨架层之上、UI 卡 `preview-overlay`(z-index:3) 之下）。
- `src/app.ts` —— 实例化 `EffectsLayer` 与 `GestureEffectsController`；在 `tick` 中驱动；模型切换/相机启动时管理显隐与清理。

---

## 3. 手势 → 特效映射与锚点

映射（每只手独立判定）：

| 手势 | 特效 | 锚点（landmark） | 触发方式 |
|---|---|---|---|
| ✋ Open_Palm | 力场冲击波环 + 火花 | 掌心 = mean(0,5,9,13,17) | 持续（每帧发射微粒 + 进入时一次强冲击波） |
| ✊ Closed_Fist | 吸能聚球（越握越大） | 掌心 | 蓄力（持续增强，配合连招释放） |
| ☝️ Pointing_Up | 指尖激光束 + 上升火花 | 食指尖(8)，方向 = normalize(pt8 − pt6) | 持续 |
| ✌️ Victory | 星星/彩屑爆发 | mid(食指尖8, 中指尖12) | 上升沿单发 |
| 👍 Thumb_Up | 大号 `+1` 上浮 + 金色火花 + 计数 | 拇指尖(4) | 上升沿单发 |
| 👎 Thumb_Down | `-1` 下沉（红） | 拇指尖(4) | 上升沿单发 |
| 🤟 ILoveYou | 爱心上浮 | 掌心 | 持续 |

**尺寸一致性**：以 `palmScale = 屏幕像素距离(landmark0, landmark9)` 作为该手的"基准尺度"，特效半径/粒子大小/发射数都按它缩放，使手离镜头远近时特效比例一致。

**坐标**：所有 landmark 先 `mapNormalizedToStagePixel(pt, layout)` 得到舞台像素，再进 effectsLayer。effectsLayer 的 canvas 与舞台像素同坐标系（见第 5 节）。

---

## 4. 触发状态机（防抖动 + 防误触，核心）

每只手维护独立状态（用稳定 handKey，如 `handedness.categoryName + '-' + i`）：

```
interface HandGestureState {
  confirmed: string | null;      // 已确认手势
  candidate: string | null;      // 去抖候选
  candidateFrames: number;
  needsClear: Set<string>;       // 已触发、需先离开才能再触发的单发手势
  lastFireAt: Map<string, number>;
  chargeStartAt: number | null;  // 蓄力开始（Open_Palm/Closed_Fist）
  chargeValue: number;           // 0..1
}
```

常量（可微调，先用这些）：

```
CONF_THRESHOLD = 0.55       // 手势置信度门槛
STABLE_FRAMES  = 3          // 连续 N 帧一致才确认（去抖，约 100ms@30fps）
COOLDOWN_MS    = 400        // 同一手势两次单发的最小间隔
CHARGE_MS      = 800        // 蓄满所需时长
```

每帧对每只检测到的手：

1. 取 `top = gestures[i][0]`。若 `top.score < CONF_THRESHOLD` 或 `top.categoryName==='None'`，视为 `raw=null`，否则 `raw=top.categoryName`。
2. 去抖：`raw===candidate ? candidateFrames++ : (candidate=raw, candidateFrames=1)`；当 `candidateFrames>=STABLE_FRAMES` 时 `confirmed=candidate`（可为 null）。
3. 若 `confirmed` 相比上一帧发生变化：清理离开的手势（把不再是 confirmed 的手势从 `needsClear` 移除；结束其持续特效；重置 charge）。
4. 按 confirmed 类型执行：
   - **单发**（Victory / Thumb_Up / Thumb_Down）：若 `!needsClear.has(g)` 且 `now-lastFireAt[g] > COOLDOWN_MS` → 在锚点触发一次；`needsClear.add(g)`；`lastFireAt[g]=now`。
   - **持续**（Pointing_Up / ILoveYou / Open_Palm 的常态发射）：每帧在锚点发射少量粒子/更新光束。
   - **蓄力**（Open_Palm 或 Closed_Fist 进入即开始）：`chargeStartAt` 记录，`chargeValue=clamp01((now-chargeStartAt)/CHARGE_MS)`，绘制聚球随 chargeValue 增大。
5. `needsClear` 的清除：当某手当前 confirmed ≠ 该单发手势时，从 `needsClear` 删除它（即"手离开了这个手势"）。

**Hero 连招：蓄力 & 释放**（在上面基础上加一个每手的小型序列检测器）

- 状态 `IDLE → CHARGING → LOCKED → (release)`：
  - `Open_Palm` 持续到 `chargeValue>=1` → `CHARGING` 完成，进入可释放。
  - 若随后 confirmed 变为 `Closed_Fist` → `LOCKED`（聚球压缩）。
  - 若在 LOCKED 后 confirmed 变为 `Open_Palm` 或 `Pointing_Up` → **释放**：以 `chargeValue` 为强度触发大冲击波 + 大爆发 + `boom` 反馈；序列回 `IDLE`。
- 任意中断（手势变 None / 手丢失 / 超时 1.5s 无进展）→ 回 `IDLE`，清聚球。

**每只手独立**：两只手可同时各自触发不同手势/连招。

---

## 5. 特效层技术规范（`effectsLayer.ts`，Phase 1）

- 拥有一块专用 canvas（`effects-canvas`）。`resize()` 逻辑照抄 `OverlayRenderer` 的做法：按 `getBoundingClientRect()` × `devicePixelRatio` 设 `canvas.width/height`，`ctx.setTransform(dpr,0,0,dpr,0,0)`；坐标系 = 舞台 CSS 像素（与 `mapNormalizedToStagePixel` 输出一致）。
- 渲染循环用**加法混合 + 拖尾**：每帧先 `globalCompositeOperation='source-over'` 用低透明度深色矩形 `rgba(5,8,11,0.30)` 铺满（产生拖尾），再切 `'lighter'` 画所有发光粒子；文字/emoji 用 `'source-over'` 画。
- 粒子发光：每个粒子用径向渐变（中心色→透明）画圆，`globalAlpha=life`。
- 上限：粒子总数封顶（如 900），超出丢最旧。仅在需要时运行（见第 6 节）。
- 色板（沿用项目 HUD）：青 `#7dd3fc`、金 `#f6c177`、薄荷 `#a9f3df`、粉 `#f7a8c4`。

需要实现的 spawn 接口（名字可调，语义要在）：

- `shockwave(x,y,color,maxR)`：扩张的描边圆环（`shadowBlur` 发光）+ `sparkBurst`。
- `sparkBurst(x,y,color,count,speed)`：四散粒子，带轻微重力/阻力。
- `beam(x,y,dirX,dirY,color)`：沿方向的发光光束（线性渐变 stroke + 沿途上升火花）。
- `confetti(x,y)`：多色小片，带重力 + 旋转。
- `floatText(x,y,text,color)`：`+1`/`-1`/`👍` 上浮淡出（emoji 用系统字体 `fillText`）。
- `hearts(x,y,color)`：爱心路径上浮。
- `chargeOrb(x,y,progress,color)`：随 progress 增大的聚球（径向渐变）+ 向心吸入粒子；释放时由控制器改调 `shockwave`。

**参考实现**（已验证观感，TS 化后可直接借鉴其数学；注意加法混合与拖尾是关键）：

```js
// 拖尾 + 加法混合主循环骨架
ctx.globalCompositeOperation = 'source-over';
ctx.fillStyle = 'rgba(5,8,11,0.30)'; ctx.fillRect(0,0,W,H);
ctx.globalCompositeOperation = 'lighter';
// 粒子：p.vx*=drag; p.vy=p.vy*drag+g; p.x+=p.vx; p.y+=p.vy; p.life-=dec;
// 发光圆：
const s = p.size*(0.6+p.life*0.8);
const g = ctx.createRadialGradient(p.x,p.y,0,p.x,p.y,s*3);
g.addColorStop(0,p.color); g.addColorStop(1,'rgba(0,0,0,0)');
ctx.globalAlpha=Math.max(0,p.life); ctx.fillStyle=g;
ctx.beginPath(); ctx.arc(p.x,p.y,s*3,0,6.283); ctx.fill();
// 冲击波环：r += (maxR-r)*0.08+1.2; life-=0.02; 描边 + shadowBlur=16
// 光束：linearGradient(x,y → x+dir*len)，lineWidth=5*life+1，shadowBlur=18
// 彩屑：save/translate/rotate 画小矩形
// 文本/emoji：切 source-over，font + fillText，globalAlpha=life
```

---

## 6. 渲染与生命周期集成（`app.ts`）

- 构造：`this.effects = new EffectsLayer(effectsCanvasEl, this.stageOverlay/或video)`；`this.gestureEffects = new GestureEffectsController(this.effects)`。
- 相机就绪进入 running 后，只有 `activeModel==='gesture_recognizer'` 时才 `effects-canvas` 显示并运行；其它模型隐藏并清空。
- 在 `tick(timestamp)` 末尾（`overlay.draw` 之后）：
  ```
  if (this.activeModel === 'gesture_recognizer' && demo?.kind === 'gesture_recognizer') {
    this.gestureEffects.update(demo.result, layout, timestamp);   // 跑状态机 + spawn
  } else {
    this.gestureEffects.reset();                                   // 非手势模式不产生新特效
  }
  this.effects.step(timestamp);                                    // 推进 + 渲染（拖尾自然淡出）
  ```
- `requestModelSwitch` 切走手势模型时：`this.gestureEffects.reset()`，并在过渡后隐藏/清空 `effects-canvas`。
- 不得破坏现有 UI 卡蓄力交互与骨架叠加；特效层 `pointer-events:none`，不拦截输入。
- 性能：手势模式目标 ≥30fps；粒子封顶 + 仅手势模式运行来保证。

---

## 7. 音效（可选，做完 Phase 1 主体后再加）

参考 `src/audio/sfx.ts`（`UiHoldSfx`）的做法，从 `ar-solar-system-demo` 复制可用音效到 `public/assets/sfx/`（只读拷贝，已有 `ui-hold-loop/success/cancel`），给：蓄力循环、释放 boom、点赞 pop 各配一个。加载失败静默降级。首个音效必须在用户手势（开摄像头点击）后解锁 AudioContext。

---

## 8. 代码规范

- 严格 TS，无 `any`、无未使用符号；导出接口清晰。
- 每手状态、常量集中定义；控制器与特效层解耦（控制器只发"在某屏幕坐标触发某效果"的调用，特效层不知道手势）。
- 复用 `mapNormalizedToStagePixel` 与现有 landmark 常量，不要自己另写镜像/contain 逻辑。
- 中文注释可用，风格与现有文件一致。

---

## 9. 验收标准（Definition of Done）

**Phase 1（必做）：**

1. `npm run build` 零错误（`tsc --noEmit && vite build` 均过）。
2. 手势模式下，7 种手势各自触发对应特效，特效锚在正确的手部位置并随手移动。
3. 去抖生效：手势轻微抖动不误触；单发手势一次手势只触发一次（离开后才能再触发）；持续手势离手即停。
4. 蓄力 & 释放连招可用：✋ 蓄力 → ✊ 锁定 → ✋/☝️ 释放，出大特效。
5. 双手可同时各自触发。
6. 切到其它模型时特效停止并清空；断网刷新可正常运行；桌面 Chrome ≥30fps 无明显卡顿。
7. 未修改 `ar-solar-system-demo` 任何文件；未新增依赖。

**Phase 2（可选，Phase 1 通过后再做）：**

- 新增 `effectsLayer3d.ts`：three.js 透明层（`WebGLRenderer` alpha、加法混合 Points/精灵、`UnrealBloomPass` 辉光、程序化生成粒子贴图，不引入图片），用于蓄力释放大爆发与体积辉光；粒子贴图用代码生成的径向渐变纹理。正确处理 resize 与 dispose。仍满足第 9 节 1/6/7 各项。

---

## 10. 建议实现顺序

1. 建 `effects-canvas` + `EffectsLayer`（先只做 `shockwave`/`sparkBurst` + 主循环），在 app 里手势模式下每秒随机触发一次，肉眼确认拖尾与发光观感。
2. 补齐其余 spawn 函数（beam/confetti/floatText/hearts/chargeOrb）。
3. 写 `GestureEffectsController` 状态机，接 `demo.result`，逐个手势打通单发/持续。
4. 加蓄力 & 释放连招。
5. （可选）音效。
6. `npm run build` + 手动走查验收清单。
7. （可选）Phase 2 three.js 升级。
