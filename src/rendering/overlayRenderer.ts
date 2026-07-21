import type {
  FaceDetectorResult,
  FaceLandmarkerResult,
  GestureRecognizerResult,
  PoseLandmarkerResult,
} from '@mediapipe/tasks-vision';
import {
  computeContainRect,
  mapNormalizedToStagePixel,
  type PreviewLayout,
} from '../interaction/coordinateMapping';
import { buildTrackedHand } from '../interaction/gestureInputFrame';
import type { NormalizedPoint, TrackedHand } from '../utils/types';

const LEFT_COLOR = '#7dd3fc';
const RIGHT_COLOR = '#f6c177';
const UNKNOWN_COLOR = '#a9f3df';
const POSE_COLOR = '#a9f3df';
const POSE_MIN_VISIBILITY = 0.5;
const FACE_LINE_COLOR = 'rgba(125, 211, 252, 0.85)';
const FACE_MESH_COLOR = 'rgba(125, 211, 252, 0.16)';
const FACE_POINT_COLOR = 'rgba(236, 247, 255, 0.8)';
const FACE_BOX_COLOR = '#f6c177';
const LABEL_FONT =
  '12px "SFMono-Regular", Consolas, "Liberation Mono", Menlo, ui-monospace, monospace';

/**
 * MediaPipe 21 点手部骨架连线（模型固定，与参考项目 HandLandmarker.HAND_CONNECTIONS 一致）。
 * 手动内联以避免主包静态引入整个 tasks-vision。
 */
const HAND_CONNECTIONS: ReadonlyArray<readonly [number, number]> = [
  [0, 1], [1, 2], [2, 3], [3, 4],
  [0, 5], [5, 6], [6, 7], [7, 8],
  [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16],
  [13, 17], [17, 18], [18, 19], [19, 20],
  [0, 17],
];

/**
 * MediaPipe Pose Landmarker 33 点骨架连线（BlazePose，模型固定）。
 * 内联以避免为几十条连线再走一次异步常量加载。
 */
const POSE_CONNECTIONS: ReadonlyArray<readonly [number, number]> = [
  [0, 1], [1, 2], [2, 3], [3, 7],
  [0, 4], [4, 5], [5, 6], [6, 8],
  [9, 10],
  [11, 12],
  [11, 13], [13, 15], [15, 17], [15, 19], [15, 21], [17, 19],
  [12, 14], [14, 16], [16, 18], [16, 20], [16, 22], [18, 20],
  [11, 23], [12, 24], [23, 24],
  [23, 25], [25, 27], [27, 29], [29, 31], [27, 31],
  [24, 26], [26, 28], [28, 30], [30, 32], [28, 32],
];

interface FaceConnection {
  start: number;
  end: number;
}

export type OverlayContent =
  | { kind: 'hands'; hands: readonly TrackedHand[] }
  | { kind: 'face_detector'; result: FaceDetectorResult }
  | { kind: 'face_landmarker'; result: FaceLandmarkerResult }
  | { kind: 'gesture_recognizer'; result: GestureRecognizerResult }
  | { kind: 'pose_landmarker'; result: PoseLandmarkerResult };

/**
 * 单画布 2D 叠加层，镜像 + object-fit: contain 坐标映射与参考项目 LandmarkOverlay 一致。
 */
export class OverlayRenderer {
  private readonly context: CanvasRenderingContext2D;
  private visible = false;
  private faceContours: FaceConnection[] | null = null;
  private faceTesselation: FaceConnection[] | null = null;
  private faceConstantsRequested = false;
  private layout: PreviewLayout = {
    stageWidth: 0,
    stageHeight: 0,
    sourceWidth: 0,
    sourceHeight: 0,
    mirrored: true,
  };

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly video: HTMLVideoElement,
  ) {
    const context = canvas.getContext('2d');

    if (!context) {
      throw new Error('2D overlay context is unavailable.');
    }

    this.context = context;
  }

  setVisible(visible: boolean): void {
    this.visible = visible;
    this.canvas.hidden = !visible;

    if (!visible) {
      this.clear();
    }
  }

  isVisible(): boolean {
    return this.visible;
  }

  draw(content: OverlayContent | null): void {
    if (!this.visible) {
      return;
    }

    const bounds = this.canvas.getBoundingClientRect();
    this.resizeToDisplay(bounds);
    this.layout = {
      stageWidth: bounds.width,
      stageHeight: bounds.height,
      sourceWidth: this.video.videoWidth,
      sourceHeight: this.video.videoHeight,
      mirrored: true,
    };
    this.clear();

    if (!content) {
      return;
    }

    switch (content.kind) {
      case 'hands':
        content.hands.forEach((hand) => this.drawHand(hand));
        break;
      case 'face_detector':
        this.drawFaceDetections(content.result);
        break;
      case 'face_landmarker':
        this.drawFaceLandmarks(content.result);
        break;
      case 'gesture_recognizer':
        this.drawGestureResult(content.result);
        break;
      case 'pose_landmarker':
        this.drawPoseResult(content.result);
        break;
    }
  }

  clear(): void {
    this.context.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }

  private resizeToDisplay(bounds: DOMRect): void {
    const width = Math.max(1, Math.round(bounds.width * window.devicePixelRatio));
    const height = Math.max(1, Math.round(bounds.height * window.devicePixelRatio));

    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }

    this.context.setTransform(window.devicePixelRatio, 0, 0, window.devicePixelRatio, 0, 0);
  }

  // ---- Hand skeleton (与参考项目 drawHand 一致) ----

  private drawHand(hand: TrackedHand): void {
    const color = handColor(hand);

    HAND_CONNECTIONS.forEach(([start, end]) => {
      const from = hand.landmarks[start];
      const to = hand.landmarks[end];

      if (from && to) {
        this.drawLine(from, to, color, 1.6);
      }
    });

    hand.landmarks.forEach((landmark) => this.drawPoint(landmark, color, 2.4));
    this.drawPoint(hand.center, '#ffffff', 4);
    this.drawPoint(hand.thumbTip, '#f38ba8', 4);
    this.drawPoint(hand.indexFingerTip, '#b4f9f8', 4);
  }

  // ---- Face Detector: HUD 风格人脸框 + 6 关键点 + 置信度 ----

  private drawFaceDetections(result: FaceDetectorResult): void {
    for (const detection of result.detections) {
      const box = detection.boundingBox;

      if (box && this.layout.sourceWidth > 0 && this.layout.sourceHeight > 0) {
        const topLeft = this.mapPoint({
          x: box.originX / this.layout.sourceWidth,
          y: box.originY / this.layout.sourceHeight,
          z: 0,
        });
        const bottomRight = this.mapPoint({
          x: (box.originX + box.width) / this.layout.sourceWidth,
          y: (box.originY + box.height) / this.layout.sourceHeight,
          z: 0,
        });
        const x = Math.min(topLeft.x, bottomRight.x);
        const y = Math.min(topLeft.y, bottomRight.y);
        const width = Math.abs(bottomRight.x - topLeft.x);
        const height = Math.abs(bottomRight.y - topLeft.y);

        this.drawHudBox(x, y, width, height, FACE_BOX_COLOR);

        const score = detection.categories[0]?.score;

        if (score !== undefined) {
          this.drawLabel(`FACE ${score.toFixed(2)}`, x, y - 8, FACE_BOX_COLOR);
        }
      }

      detection.keypoints?.forEach((keypoint) => {
        this.drawPoint({ x: keypoint.x, y: keypoint.y, z: 0 }, LEFT_COLOR, 3);
      });
    }
  }

  /** 四角括号 + 细边线的 HUD 检测框（呼应卡片语言）。 */
  private drawHudBox(
    x: number,
    y: number,
    width: number,
    height: number,
    color: string,
  ): void {
    const ctx = this.context;
    const corner = Math.min(16, width * 0.2, height * 0.2);

    ctx.save();
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.35;
    ctx.lineWidth = 1;
    ctx.strokeRect(x, y, width, height);
    ctx.globalAlpha = 1;
    ctx.lineWidth = 2;
    ctx.shadowColor = color;
    ctx.shadowBlur = 8;

    const corners: Array<[number, number, number, number, number, number]> = [
      [x + corner, y, x, y, x, y + corner],
      [x + width - corner, y, x + width, y, x + width, y + corner],
      [x, y + height - corner, x, y + height, x + corner, y + height],
      [x + width, y + height - corner, x + width, y + height, x + width - corner, y + height],
    ];

    for (const [x1, y1, x2, y2, x3, y3] of corners) {
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.lineTo(x3, y3);
      ctx.stroke();
    }

    ctx.restore();
  }

  // ---- Face Landmarker: 478 点面网格 + 轮廓 ----

  private drawFaceLandmarks(result: FaceLandmarkerResult): void {
    this.ensureFaceConstants();

    for (const landmarks of result.faceLandmarks) {
      // 每帧只映射一次全部 478 点，网格连线查表绘制。
      const mapped = landmarks.map((landmark) =>
        this.mapPoint({ x: landmark.x, y: landmark.y, z: landmark.z }),
      );
      const ctx = this.context;

      if (this.faceTesselation) {
        ctx.strokeStyle = FACE_MESH_COLOR;
        ctx.lineWidth = 0.6;
        ctx.beginPath();

        for (const { start, end } of this.faceTesselation) {
          const from = mapped[start];
          const to = mapped[end];

          if (from && to) {
            ctx.moveTo(from.x, from.y);
            ctx.lineTo(to.x, to.y);
          }
        }

        ctx.stroke();
      }

      if (this.faceContours) {
        ctx.strokeStyle = FACE_LINE_COLOR;
        ctx.lineWidth = 1.4;
        ctx.beginPath();

        for (const { start, end } of this.faceContours) {
          const from = mapped[start];
          const to = mapped[end];

          if (from && to) {
            ctx.moveTo(from.x, from.y);
            ctx.lineTo(to.x, to.y);
          }
        }

        ctx.stroke();
      }

      ctx.fillStyle = FACE_POINT_COLOR;

      for (const point of mapped) {
        ctx.beginPath();
        ctx.arc(point.x, point.y, 1, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  /** 面网格连线常量按需懒加载（与演示模型共用同一 tasks-vision 模块实例）。 */
  private ensureFaceConstants(): void {
    if (this.faceConstantsRequested) {
      return;
    }

    this.faceConstantsRequested = true;
    void import('@mediapipe/tasks-vision').then(({ FaceLandmarker }) => {
      this.faceContours = FaceLandmarker.FACE_LANDMARKS_CONTOURS.map((connection) => ({
        start: connection.start,
        end: connection.end,
      }));
      this.faceTesselation = FaceLandmarker.FACE_LANDMARKS_TESSELATION.map((connection) => ({
        start: connection.start,
        end: connection.end,
      }));
    });
  }

  // ---- Gesture Recognizer: 骨架 + 手势标签 ----

  private drawGestureResult(result: GestureRecognizerResult): void {
    result.landmarks.forEach((landmarks, index) => {
      const category = result.handedness[index]?.[0];
      const name = category?.categoryName.toLowerCase();
      const side = name === 'left' || name === 'right' ? name : 'unknown';
      const hand = buildTrackedHand(
        landmarks.map((landmark) => ({
          x: landmark.x,
          y: landmark.y,
          z: landmark.z,
          visibility: landmark.visibility,
        })),
        side,
        category?.score ?? 0,
      );

      if (!hand) {
        return;
      }

      this.drawHand(hand);

      const gesture = result.gestures[index]?.[0];

      if (gesture) {
        const anchor = this.mapPoint(hand.wrist);
        const label =
          gesture.categoryName === 'None' || gesture.categoryName === ''
            ? `— ${gesture.score.toFixed(2)}`
            : `${gesture.categoryName} ${gesture.score.toFixed(2)}`;

        this.drawLabel(label, anchor.x, anchor.y + 26, handColor(hand));
      }
    });
  }

  // ---- Pose Landmarker: 33 点全身骨架（按可见度过滤） ----

  private drawPoseResult(result: PoseLandmarkerResult): void {
    const ctx = this.context;

    for (const landmarks of result.landmarks) {
      const mapped = landmarks.map((landmark) => ({
        point: this.mapPoint({ x: landmark.x, y: landmark.y, z: landmark.z }),
        visible: (landmark.visibility ?? 1) >= POSE_MIN_VISIBILITY,
      }));

      ctx.save();
      ctx.strokeStyle = POSE_COLOR;
      ctx.lineWidth = 3;
      ctx.lineCap = 'round';
      ctx.shadowColor = POSE_COLOR;
      ctx.shadowBlur = 6;

      for (const [start, end] of POSE_CONNECTIONS) {
        const from = mapped[start];
        const to = mapped[end];

        if (from && to && from.visible && to.visible) {
          ctx.beginPath();
          ctx.moveTo(from.point.x, from.point.y);
          ctx.lineTo(to.point.x, to.point.y);
          ctx.stroke();
        }
      }

      ctx.fillStyle = '#ecf7ff';

      for (const node of mapped) {
        if (!node.visible) {
          continue;
        }

        ctx.beginPath();
        ctx.arc(node.point.x, node.point.y, 3.4, 0, Math.PI * 2);
        ctx.fill();
      }

      ctx.restore();
    }
  }

  // ---- primitives ----

  private drawLabel(text: string, x: number, y: number, color: string): void {
    const ctx = this.context;

    ctx.save();
    ctx.font = LABEL_FONT;
    ctx.textBaseline = 'middle';

    const metrics = ctx.measureText(text);
    const paddingX = 7;
    const height = 20;
    const clampedX = Math.max(
      4,
      Math.min(x, this.layout.stageWidth - metrics.width - paddingX * 2 - 4),
    );
    const clampedY = Math.max(height / 2 + 4, Math.min(y, this.layout.stageHeight - height));

    ctx.fillStyle = 'rgba(5, 8, 10, 0.72)';
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.rect(clampedX, clampedY - height / 2, metrics.width + paddingX * 2, height);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = color;
    ctx.shadowColor = color;
    ctx.shadowBlur = 6;
    ctx.fillText(text, clampedX + paddingX, clampedY + 1);
    ctx.restore();
  }

  private drawPoint(point: NormalizedPoint, color: string, radius: number): void {
    const { x, y } = this.mapPoint(point);

    this.context.beginPath();
    this.context.arc(x, y, radius, 0, Math.PI * 2);
    this.context.fillStyle = color;
    this.context.fill();
  }

  private drawLine(
    from: NormalizedPoint,
    to: NormalizedPoint,
    color: string,
    width: number,
  ): void {
    const start = this.mapPoint(from);
    const end = this.mapPoint(to);

    this.context.beginPath();
    this.context.moveTo(start.x, start.y);
    this.context.lineTo(end.x, end.y);
    this.context.strokeStyle = color;
    this.context.lineWidth = width;
    this.context.stroke();
  }

  private mapPoint(point: NormalizedPoint): { x: number; y: number } {
    return mapNormalizedToStagePixel(point, this.layout);
  }

  /** 供画面提示定位使用：摄像头画面在舞台内的实际显示矩形。 */
  getContainRect(): { x: number; y: number; width: number; height: number } {
    return computeContainRect(this.layout);
  }
}

function handColor(hand: TrackedHand): string {
  if (hand.side === 'left') {
    return LEFT_COLOR;
  }

  if (hand.side === 'right') {
    return RIGHT_COLOR;
  }

  return UNKNOWN_COLOR;
}
