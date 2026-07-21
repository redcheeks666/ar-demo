# Codex 执行任务：AEGIS-R7 头盔 Graybox v4 —— 表面特征重建（feature projection）

## 1. 角色与授权

你是执行窗口。你被授权直接检查、修改、运行和验证本仓库中的 Blender/Python 脚本与资产。不要只给教程或建议；自己动手完成并自我验证。全程使用无头（headless/background）Blender 运行脚本，不要求用户在 Blender 界面做任何操作。

## 2. 仓库与基线

- 仓库：`https://github.com/redcheeks666/ar-demo`（本地工作副本）
- 基线 commit：`e480eec feat: rebuild helmet graybox v3 from reference silhouettes`
- 开始前运行 `git status`，确认工作树干净；如有用户未提交修改，保留它们，不得 reset/checkout 丢弃。

关键路径：

- 参考图：`blender/references/helmet_turnaround.png`、`blender/references/helmet_assembly_reference.png`
- v3 裁剪与 mask：`blender/references/derived_v3/`（front/left/right/back/top/bottom 的 crop 和 mask）
- v3 脚本：`blender/scripts/helmet_v3_config.py`、`build_helmet_graybox_v3.py`、`compare_helmet_v3.py`、`render_helmet_review_v3.py`、`export_helmet_graybox_v3.py`
- v3 结果（只读历史基线，禁止覆盖）：`blender/work/helmet_graybox_v3.blend`、`blender/output/graybox_v3/`

## 3. 本轮唯一目标

v3 的外轮廓（HelmetEnvelope_Master）、23 件分件结构、局部 pivot、GLB 导出与重导入管线已通过监理审查，必须保留其成果与方法。

v3 未通过的是**表面特征设计**：渲染结果是"红色光球 + 切缝"，与参考图完全对不上。本轮唯一目标：在保留 v3 外轮廓的前提下，把参考图中的以下特征真实建出来，使 front / side / front 3/4 / back 四个视角能被一眼认出与参考图是同一设计：

1. V 形雕塑感面甲（有眉棚、鼻梁、口部、下巴的纵向深度变化，不是光滑鼓包盾牌）；
2. 面甲内部的横向发光眼缝（不是贴在面甲边缘外侧的小药丸）；
3. 面甲四周的黑色内衬包边带（faceplate 与红色外壳之间可见的黑色沟槽框）；
4. 沿参考图斜向分区线布置的颊板 / 下颌 U 形框（不是横向百叶窗切带）;
5. 大直径同心圆耳碟，凹陷嵌入侧壳，带青色竖向发光条；
6. 顶部前后向的黑色分段中央纵脊（crown 中线），并延续到后脑；
7. 后脑：中央分段纵脊 + 横向散热缝 + 左右层叠后壳分区（不是两块空白半球）；
8. 封闭当前顶部的圆形开孔伪影；消除面甲上部的皱褶/凹坑高光伪影。

## 4. 已确认根因（必须逐条解决，不许绕过）

v1→v3 一直"对不上"的根本原因：**参考图只以外轮廓 mask 的形式参与建模，所有表面特征（面甲边界、眼缝、耳罩、板缝、纵脊）都由 `helmet_v3_config.py` 里的程序参数臆造，从未从参考图测量**。同时 `compare_helmet_v3.py` 只计算外轮廓 IoU，而参考图外轮廓接近卵形，导致"球体+切缝"也能拿 0.88–0.95 的高分。指标通过 ≠ 视觉通过。

具体到 v3 产物：

- `build_helmet_graybox_v3.py` 中面甲是解析 UV patch 的光滑连续面，无任何纵向结构线；其细分产生了皱褶伪影；
- 眼缝几何放在面甲边界之外（iteration 3 的错误决策被保留了）；
- 颊/颌/下巴由横向 band 切割生成，呈百叶窗状；
- RearShell 只有对称切缝，无任何后脑结构；
- EarCover 半径过小（按钮状），未形成凹陷同心圆结构；
- 顶部纵脊缺失，crown 顶部有圆形开孔。

## 5. 输入素材

- `blender/references/derived_v3/` 全部 crop 与 mask；
- `blender/work/helmet_graybox_v3.blend`（作为起点复制，不在原文件上工作）；
- `blender/output/graybox_v3/reference_measurements.json`（可复用其头高归一化基准）；
- HeadProxy 沿用 v3 场景中的头部代理。

## 6. 保护规则

- 不覆盖、不删除 `blender/output/graybox_v3/`、`blender/work/helmet_graybox_v3.blend` 及更早版本的任何文件；
- v4 全部新文件走 v4 路径（见第 11 节）；脚本新建 `*_v4.py`，可以 import/复制 v3 代码，但不得修改 v3 脚本行为；
- 不修改 `src/` 下任何 Web/MediaPipe 代码；
- 不执行 `git push`；本地 commit 可以做，但保持单一、信息清晰；
- 不将 HeadProxy、参考图 Empty、相机、灯光导出进 GLB。

## 7. 实施顺序（内部阶段，全部由你完成）

### Phase A — 特征测量（feature extraction）

从 derived_v3 的 crop 图中测量特征几何，写入 `blender/output/graybox_v4/feature_curves.json`，坐标一律以"参考头盔像素高度"归一化：

- front_crop：面甲外边界折线（V 形，含下巴尖）、左右眼缝中心 / 宽 / 高 / 倾角、黑色包边带宽度、颊颌斜向分区线端点；
- left/right_crop：耳碟圆心与半径、耳碟青色竖条位置、面甲侧缘轮廓、颊/颌斜切线、面甲纵向深度曲线（额-眉-鼻-口-下巴的前后深度采样 ≥ 12 点）；
- back_crop：中央纵脊宽度与分段数、横向散热缝的高度位置、层叠后壳分界线；
- top_crop：纵脊在顶部的走向与宽度。

方法可用颜色/亮度分割半自动提取，也允许人工硬编码折线控制点，但**每个控制点必须来自对 crop 实际像素坐标的采样**。必须输出验证图 `feature_annotation_front.png / _left.png / _right.png / _back.png / _top.png`：把提取的特征线叠画回对应 crop 上。特征线与图中特征明显不符则本阶段不通过，自行修正后重来。

### Phase B — 特征投影与面板重划

- 将 front/side/back 特征曲线按对应正交方向投影到 HelmetEnvelope_Master 表面，得到表面上的特征分区线；
- 依据投影线重新裁切 Faceplate、Cheek、Jaw、Chin、Temple、RearShell 边界；分件命名与 v3 保持一致（23 节点清单不变，允许新增眼缝/纵脊等子件）；
- Faceplate：从母体裁切后，按 side 深度曲线施加纵向起伏（额部外凸→眉棚棱线→鼻梁隆起→口部内收→下巴前突），左右对称；拓扑均匀，法线平滑，渲染高光必须连续无皱褶；
- 眼缝：在面甲表面按测量位置开横向凹槽，内嵌黑色 housing + 青白色 emissive lens；眼缝完全位于面甲边界内；
- 黑色包边：面甲边界与周围红色板件之间建出可见的黑色内衬沟槽带（宽度按测量值）；
- 颊/颌：沿斜向分区线形成连续 U 形下颌框；**禁止横向平行切带**。

### Phase C — 侧面与后脑结构

- EarCover 重做：直径按测量值（明显大于 v3 按钮），同心圆台阶 ≥ 2 级，整体凹陷嵌入侧壳，中心青色竖条 emissive；
- 顶部中央纵脊：黑色分段条带按 top/back 测量走向铺设，从额顶延续到后脑下缘；封闭 crown 顶部圆形开孔；
- 后脑：纵脊两侧布置横向散热缝与层叠后壳分界，RearShell_L/R 不得再是空白光面；
- NeckRing 保持隐藏在下缘内侧（v3 已合格，勿退化）。

### Phase D — 验收与迭代（内部质量门）

1. 外轮廓 IoU 仅作回归检查：front ≥ 0.90、side ≥ 0.85（不得低于 v3 明显退化）；**IoU 通过不构成视觉通过**；
2. 新增特征级数值检查，写入 `feature_validation.json`，逐项与 feature_curves.json 比对，容差以头高百分比计：眼缝中心位置误差 ≤ 3%、眼缝长度误差 ≤ 15%、耳碟圆心误差 ≤ 4%、耳碟半径误差 ≤ 15%、面甲最大宽度误差 ≤ 4%、面甲下巴尖高度误差 ≤ 3%、纵脊宽度误差 ≤ 30%；
3. 同机位同框渲染与参考 crop 的并排对比图：`compare_front.png`、`compare_left.png`、`compare_right.png`、`compare_back.png`、`compare_front_3q.png`，及汇总 `comparison_contact_sheet_v4.png`；
4. 渲染后**逐视角自查**下列条目并在报告中给出 是/否 + 引用的具体渲染文件名：
   - 面甲是否有可见的眉棚/鼻梁/下巴纵向结构（看侧面与 3/4 高光）；
   - 眼缝是否位于面甲内部并发光；
   - 黑色包边是否在 front 与 3/4 可见；
   - 颊颌是否为斜向分区 U 形框（front 不得出现横向百叶窗）；
   - 耳碟是否为凹陷大同心圆；
   - back 是否有纵脊+散热缝+层叠分区；
   - 顶部开孔是否已封闭；面甲高光是否连续；
5. HeadProxy 净空复查：全头净空 ≥ 4 mm，无穿插；
6. 至少 3 轮内部迭代；每轮记录 observed / changes / result 到 `logs/visual_iteration_log_v4.json`。任何一项自查为"否"则继续迭代，不得交付。

### Phase E — 导出与重导入验证

- 导出 `blender/output/graybox_v4/helmet_graybox_v4.glb`；
- 保持全部可动件的局部 origin/translation（参照 v3 的 24 节点结构，新增子件同样要有合理 pivot）；
- 三角面 < 100,000；
- 空场景重导入验证节点名、transform、材质数，写入 `logs/export_reimport_validation_v4.json`；
- Clay 与 Design Preview 两套材质各渲染 8 视角（front/back/left/right/front_3q/rear_3q/top/bottom）+ with_head_proxy + exploded_sanity。

## 8. 禁止项

- 禁止只调 `helmet_v3_config.py` 参数而不做特征提取与投影；
- 禁止把 IoU 或任何单一数值当作视觉通过依据；
- 禁止横向切带表达颊/颌；禁止三块口罩积木；
- 禁止眼缝位于面甲边界之外；
- 禁止用"参考图不精确"作为跳过特征的理由；无法精确处按优先级：front 闭合轮廓 > 侧面轮廓 > front 3/4 包覆 > 对称性 > 小细节；
- 禁止覆盖任何 v3 及更早文件；禁止 push；禁止修改 Web 代码；
- 禁止宣称"完成"而未附带第 7.D.4 条逐项自查表。

## 9. 输出路径

- `blender/scripts/build_helmet_graybox_v4.py`、`compare_helmet_v4.py`、`render_helmet_review_v4.py`、`export_helmet_graybox_v4.py`、`helmet_v4_config.py`、`extract_features_v4.py`
- `blender/work/helmet_graybox_v4.blend`
- `blender/output/graybox_v4/`：feature_curves.json、feature_annotation_*.png、feature_validation.json、compare_*.png、comparison_contact_sheet_v4.png、clay/、design_preview/、with_head_proxy/、exploded_sanity/、silhouette/、logs/、helmet_graybox_v4.glb、helmet_graybox_v4_report.json

## 10. 最终回复格式

完成后按以下结构回复（不要只写"已完成"）：

1. 修改/新增文件清单；
2. 实际执行的命令列表；
3. 特征级验收指标表（目标值 / 实测值 / 是否通过）；
4. 第 7.D.4 条逐视角自查表（每行引用渲染文件名）；
5. 已知残留问题与原因；
6. 输出文件路径清单。

## 11. 停止条件

完成 Phase E 并输出上述回复后停止，等待监理审查。不得进入正式 PBR 材质、UV、装配动画或 Web 对接阶段。
