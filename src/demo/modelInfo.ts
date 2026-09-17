import type { DemoModelId } from '../utils/types';

export interface ModelInfo {
  id: DemoModelId;
  nameEn: string;
  nameCn: string;
  /** 卡片中文主标签（“长按演示 …”前缀在 UI 中拼接）。 */
  cardLabel: string;
  /** 卡片英文 HUD 代号（MODEL · 前缀在 UI 中拼接）。 */
  code: string;
  purpose: string;
  output: string;
  principle: string;
}

/** 模型卡在右侧卡列中的排列顺序。 */
export const MODEL_ORDER: readonly DemoModelId[] = [
  'face_detector',
  'face_landmarker',
  'gesture_recognizer',
  'hand_landmarker',
];

export const MODEL_INFO: Record<DemoModelId, ModelInfo> = {
  face_detector: {
    id: 'face_detector',
    nameEn: 'Face Detector',
    nameCn: '人脸检测',
    cardLabel: '人脸检测',
    code: 'FACE DETECTOR',
    purpose:
      '用途：超轻量人脸检测，回答“画面里有没有脸、脸在哪”，常作为人脸流程的第一步。',
    output:
      '输出：人脸包围框 + 6 个关键点（双眼、鼻尖、嘴、双耳屏）+ 置信度。',
    principle:
      '原理：BlazeFace 短距模型，单次前向直接回归人脸框，为移动端实时场景设计。',
  },
  face_landmarker: {
    id: 'face_landmarker',
    nameEn: 'Face Landmarker',
    nameCn: '人脸网格',
    cardLabel: '人脸网格',
    code: 'FACE LANDMARKER',
    purpose:
      '用途：实时稠密面部几何，驱动 AR 贴纸、虚拟形象表情与美颜对齐。',
    output:
      '输出：478 个三维面部关键点 + 52 组表情 blendshapes 系数（右下数据面板实时显示最强表情）。',
    principle:
      '原理：先检测人脸，再在裁剪区域内回归全脸网格（Attention Mesh 两级流水线）。',
  },
  gesture_recognizer: {
    id: 'gesture_recognizer',
    nameEn: 'Gesture Recognizer',
    nameCn: '手势识别',
    cardLabel: '手势识别',
    code: 'GESTURE RECOGNIZER',
    purpose:
      '用途：直接给出手势语义（点赞、胜利、握拳……），适合做隔空控制指令。',
    output:
      '输出：7 类预置手势 + 21 点手部关键点 + 左右手。此模式下 UI 卡蓄力直接复用它输出的关键点。',
    principle:
      '原理：在手部关键点之上叠加手势嵌入与分类头，识别 Closed_Fist / Open_Palm / Pointing_Up / Thumb_Down / Thumb_Up / Victory / ILoveYou。',
  },
  hand_landmarker: {
    id: 'hand_landmarker',
    nameEn: 'Hand Landmarker',
    nameCn: '手部关键点',
    cardLabel: '手部关键点',
    code: 'HAND LANDMARKER',
    purpose:
      '用途：AR 太阳系全部手势交互的底层模型——本演示的“食指隔空长按”也由它常驻驱动。',
    output:
      '输出：每只手 21 个关键点（图像坐标 + 世界坐标）+ 左右手 + 置信度。左手青色 / 右手金色。',
    principle:
      '原理：手掌检测 + 关键点回归两级流水线；视频模式下跨帧跟踪，省去逐帧重新检测。',
  },
};
