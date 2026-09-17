# Codex 执行任务：v6b —— AI 网格分件、铰链与交付（split & finishing）

## 1. 角色与授权

你是执行窗口，被授权编写运行脚本、headless Blender 处理网格、导出验证。全程不需要用户做 Blender 手动操作。

## 2. 基线与输入

- 开始前 `git status`，保留用户未提交内容；
- 造型母体：`blender/references/ai_mesh_v6/candidate_XX/model.glb`（用户与监理已选定，具体 XX 以用户指定为准；未指定则停下询问）；
- 切割线数据：`blender/output/graybox_v4/feature_curves.json` 与 `feature_annotation_*.png`（v4 实测的面甲边界、颌线、耳碟圆、纵脊走向）；
- 内构复用：`blender/work/helmet_graybox_v4.blend` 中的 `InnerShell`、`NeckRingFront/Rear` 与 HeadProxy；
- v3/v4/v5 所有历史输出只读。

## 3. 造型权威条款（最高优先级）

**选定的 AI 生成网格是造型权威。** 禁止重新设计整体轮廓、面甲比例、头顶与侧壳体积；禁止用 primitive 或程序化网格替换它的任何可见外表面；禁止任何"审美优化"迭代。你的全部工作是工程处理：对齐、切割、加厚、装轴、发光、减面、导出。若发现网格缺陷（破洞、非流形、噪点），只做最小修复，不重塑造型。

## 4. 本轮唯一目标

把一体 AI 网格变成**可做机械动画的分件 GLB**：

1. 导入规范化：统一单位与朝向（Y-up 导出约定与现有 POC 一致）、原点在头心、按 HeadProxy 缩放到真实头围（复用 v3/v4 的适配标准，全头净空 ≥ 4 mm 无穿插；AI 网格如内部实心/有底座，先清理）；
2. 分件切割（按 feature_curves 投影到 AI 网格表面定切割线）：
   - `Faceplate`（面甲边界线切出）；
   - `CrownFront` / `CrownRear`（顶部纵脊线与横向分界）；
   - `Temple_L/R`、`Cheek_L/R`；
   - `JawU`（下颌 U 形整体一件，不拆三块）；
   - `EarCover_L/R`（耳碟圆切出）;
   - `RearShell_L/R`；
   - 共 11–13 个外壳件；命名沿用上述约定；
3. 每件 solidify 加厚（内侧偏移，厚度约 2–3 mm 等效），切割开边封闭，无非流形；
4. 内构：从 v4 blend 提取 `InnerShell`、`NeckRing`，缩放适配到 AI 外壳内侧，深灰/黑材质，保证壳件展开时露出的是内衬而不是空洞；
5. Pivot 与铰链：每件设置有意义的局部原点；`Faceplate` 的 pivot 放在左右耳碟圆心连线（开面甲的旋转轴），验证绕该轴旋转 40–60° 无穿插即通过；其余件 pivot 放各自质心偏内侧；
6. 眼缝发光：在面甲眼缝位置（feature_curves 有坐标）分离或叠加薄条几何，蓝白 Emission 材质，命名 `EyeLens_L/R`；
7. 材质与减面：保留 AI 贴图（basecolor/PBR），贴图 ≤ 2048px；几何减面到总计 ≤ 80,000 三角面（视觉无明显退化为准，切割边界处保护）；
8. 导出 `blender/output/ai_mesh_v6/helmet_ai_v6.glb`：仅 HelmetRoot 层级 + 上述件，无 HeadProxy/相机/灯光；空场景重导入验证节点、transform、材质；
9. 审查渲染：clay 与带贴图两套 front/left/right/back/front_3q/rear_3q/top/bottom；`faceplate_open_test/` 渲面甲开启 0°/30°/60° 三帧；`with_head_proxy/` 四视角；`exploded_sanity/` 一张全件展开。

## 5. 实施提示

- 切割优先用 bisect/boolean 沿投影切割面；切不干净的区域允许按最近特征线的平面近似；
- 贴图在切割边会有接缝：切割边内侧面与加厚面用深灰哑光材质，不试图补贴图；
- AI 网格可能左右不完全对称：切割线以模型实际几何为准做左右各自投影，不强制镜像；
- 若 AI 网格底部封死无颈口：按 NeckRing 位置开颈部开口；
- 迭代由渲染自查驱动，至少 2 轮：切割边缘干净、无浮空碎片、面甲开合无穿插、发光可见。

## 6. 禁止项

- 禁止改动外表面造型（见第 3 节）；
- 禁止把件拆成"三块口罩"式碎片；禁止超过 15 个外壳件；
- 禁止覆盖历史输出；禁止 push；禁止改 `src/`；
- 禁止在自查未过时宣称完成。

## 7. 自动检查

- 重导入验证 JSON；三角面统计；非流形/浮空件检测脚本输出 `mesh_integrity_v6.json`；
- HeadProxy 净空采样 ≥ 4 mm；
- Faceplate 绕耳轴 0–60° 旋转采样碰撞检测，结果入报告。

## 8. 输出路径

- 脚本：`blender/scripts/split_ai_mesh_v6.py` 等 `*_v6.py`
- `blender/work/helmet_ai_v6.blend`
- `blender/output/ai_mesh_v6/`：helmet_ai_v6.glb、helmet_ai_v6_report.json、mesh_integrity_v6.json、clay/、textured/、faceplate_open_test/、with_head_proxy/、exploded_sanity/、logs/

## 9. 最终回复格式

1. 文件与命令清单；2. 分件清单（件名/三角面/pivot 位置/铰链轴）；3. 自动检查结果；4. 渲染自查表（逐项 是/否 + 文件名）；5. 已知问题（贴图缝位置等）。

## 10. 停止条件

完成导出与渲染后停止，等待监理审查。不进入装配动画关键帧制作、Web 接入阶段。
