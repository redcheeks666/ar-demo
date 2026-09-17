# Codex 执行任务：v6a —— 通过 Tripo API 从参考图生成 AI 头盔网格候选

## 1. 角色与授权

你是执行窗口，被授权编写并运行脚本、调用外部 API、下载文件、运行 headless Blender 渲染预览。本轮不做任何手工建模，也不改动 v3/v4/v5 的程序化建模资产。

## 2. 背景与本轮唯一目标

程序化建模路线（v1–v5）已被监理判定达到天花板：工程管线合格，但曲面造型无法接近参考图。项目改用图生 3D 路线：调用 Tripo 官方 API，用参考图多视角裁剪图生成 2–3 个带贴图的头盔网格候选，供监理和用户挑选。

本轮只负责"生成候选 + 渲染预览"。不做清理、减面、发光、Web 接入（那是 v6b 的事）。

## 3. 前置条件（先检查，不满足立即停止）

1. 环境变量 `TRIPO_API_KEY` 必须存在。检查方式：`python3 -c "import os;assert os.environ.get('TRIPO_API_KEY'),'missing'"`。
   - 如果不存在：**立即停止**，回复用户："请到 https://platform.tripo3d.ai 注册登录 → 控制台创建 API Key → 在终端 `export TRIPO_API_KEY=你的key` 后重新运行我"。不要尝试任何绕过方式。
2. 网络可访问 `platform.tripo3d.ai`。不可访问则停止并报告。
3. 输入图存在：`blender/references/derived_v3/front_crop.png`、`left_crop.png`、`right_crop.png`、`back_crop.png`、`closed_front_3q_crop.png`。

## 4. API 事实与文档要求

已核实的事实：Tripo 平台 API 支持 `image_to_model` 与 `multiview_to_model` 任务类型；鉴权为 API Key（Bearer）；图片建议分辨率 >256×256，支持 PNG/JPEG，≤20MB；任务为异步创建+轮询模式。

**实现前必须先读官方文档**（https://platform.tripo3d.ai/docs/generation 及相关页面，必要时用文档站的 API 参考或官方 OpenAPI），以文档为准确定：上传图片的方式（直传/URL/base64）、`multiview_to_model` 各视角字段名与朝向定义、轮询接口、模型下载字段（选带 PBR 贴图的 GLB 输出）。如官方提供 Python SDK（如 PyPI `tripo3d`），优先用官方 SDK，否则直接 REST。**禁止凭记忆猜字段**；字段不确定就再查文档或用最小请求试探一次。

## 5. 实现步骤

1. 新建脚本 `scripts/tripo_generate_v6.py`：
   - 从环境变量读 Key（**Key 不得写入任何文件、日志或 git**）；
   - 提交 `multiview_to_model` 任务：front=`front_crop.png`，left=`left_crop.png`，right=`right_crop.png`，back=`back_crop.png`（视角与字段的对应严格按文档；如 API 只要求 front+若干可选视角，按文档最优组合传）；
   - 开启贴图与 PBR 选项（按文档字段名）；几何面数用默认，不主动限制（减面留给 v6b）；
   - 每个任务用不同 `model_seed`；
   - 轮询至完成，下载 GLB 到 `blender/references/ai_mesh_v6/candidate_XX/model.glb`，同时保存 API 返回的任务元数据到同目录 `task_meta.json`（含 task id、seed、参数；**不含 Key**）。
2. 生成 **3 个 multiview 候选**（不同 seed）。
3. 若 multiview 结果明显失败（几何破碎、多头、视角打架——从预览渲染判断），再用 `image_to_model` + `closed_front_3q_crop.png` 生成 **最多 2 个**单图候选（candidate_04/05）。
4. 预览渲染：写 `blender/scripts/render_ai_candidates_v6.py`，headless Blender 逐候选导入 GLB，渲 front / left / back / front_3q 四视角（中性灰底、三点光、相机框满头盔），输出到 `blender/output/ai_mesh_v6/candidate_XX/*.png`，并合成一张 `blender/output/ai_mesh_v6/candidates_contact_sheet.png`（每行一个候选：4 渲染视角 + 对应参考 crop 缩略图并排）。
5. 解析每个 GLB 的三角面数、mesh/材质/贴图数量、文件大小，写入 `blender/output/ai_mesh_v6/candidates_report.json`。

## 6. 花费控制（硬约束）

- API 调用消耗账户 credits：全程最多 3 次 multiview + 2 次单图任务，**总计不超过 5 次生成**；
- 同一任务轮询属免费查询，不受限；
- 任何请求返回配额/余额不足错误：立即停止并如实报告，不重试；
- 不订阅、不购买、不触发任何付费升级操作。

## 7. 禁止项

- 禁止把 API Key 写入代码、配置、日志、报告或 commit；
- 禁止修改 `src/`、v3/v4/v5 的脚本与输出；
- 禁止对候选 GLB 做任何"修复/优化/减面"（本轮只生成和预览）；
- 禁止 push；
- 禁止在候选失败时自行改用其他第三方生成服务（换服务需用户决定）。

## 8. 输出路径汇总

- `scripts/tripo_generate_v6.py`
- `blender/scripts/render_ai_candidates_v6.py`
- `blender/references/ai_mesh_v6/candidate_XX/model.glb` + `task_meta.json`
- `blender/output/ai_mesh_v6/candidate_XX/{front,left,back,front_3q}.png`
- `blender/output/ai_mesh_v6/candidates_contact_sheet.png`
- `blender/output/ai_mesh_v6/candidates_report.json`

## 9. 最终回复格式

1. 实际执行的命令与任务参数（seed、选项）；
2. 每个候选：三角面数 / 贴图情况 / 文件大小 / 一句话形体评价；
3. contact sheet 与各渲染文件路径；
4. 消耗的生成次数；
5. 遇到的 API 问题与处理方式。

## 10. 停止条件

候选与预览产出后立即停止，等待监理与用户挑选。不进入清理、减面、发光、导出规范化或 Web 接入。
