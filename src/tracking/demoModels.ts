import type {
  FaceDetector,
  FaceDetectorResult,
  FaceLandmarker,
  FaceLandmarkerResult,
  GestureRecognizer,
  GestureRecognizerResult,
} from '@mediapipe/tasks-vision';
import type { DemoModelId, NormalizedPoint, TrackedHandSide } from '../utils/types';
import type { RawWorkerHand } from './handLandmarkerWorkerProtocol';
import {
  ACTIVE_DETECTION_INTERVAL_MS,
  FACE_DETECTOR_MODEL_URL,
  FACE_LANDMARKER_MODEL_URL,
  GESTURE_RECOGNIZER_MODEL_URL,
  MEDIAPIPE_WASM_URL,
} from './config';

/**
 * 演示模型控制器：负责 Face Detector / Face Landmarker / Gesture Recognizer
 * 的创建、切换、推理与销毁（主线程 VIDEO 模式）。
 *
 * hand_landmarker 模式不在这里建实例——它复用常驻交互引擎的 Worker 输出。
 */
export type DemoDetection =
  | { kind: 'face_detector'; result: FaceDetectorResult }
  | { kind: 'face_landmarker'; result: FaceLandmarkerResult }
  | { kind: 'gesture_recognizer'; result: GestureRecognizerResult };

type DemoDelegate = 'GPU' | 'CPU';

type DemoInstance =
  | { kind: 'face_detector'; model: FaceDetector }
  | { kind: 'face_landmarker'; model: FaceLandmarker }
  | { kind: 'gesture_recognizer'; model: GestureRecognizer };

export interface DemoModelPerformance {
  inferenceMs: number | null;
  delegate: DemoDelegate | null;
}

type VisionFileset = Awaited<
  ReturnType<(typeof import('@mediapipe/tasks-vision'))['FilesetResolver']['forVisionTasks']>
>;

export class DemoModelController {
  private fileset: VisionFileset | null = null;
  private active: DemoInstance | null = null;
  private activeDelegate: DemoDelegate | null = null;
  private switchGeneration = 0;
  private latest: DemoDetection | null = null;
  private lastDetectionAt = 0;
  private inferenceMs: number | null = null;
  private detectionSeq = 0;

  /**
   * 切换演示模型。旧模型在新模型就绪前保持运行；切换失败时旧模型不受影响。
   */
  async switchTo(id: DemoModelId): Promise<void> {
    this.switchGeneration += 1;
    const generation = this.switchGeneration;

    if (id === 'hand_landmarker') {
      this.disposeActive();
      return;
    }

    const {
      FilesetResolver,
      FaceDetector,
      FaceLandmarker,
      GestureRecognizer,
    } = await import('@mediapipe/tasks-vision');

    if (!this.fileset) {
      this.fileset = await FilesetResolver.forVisionTasks(MEDIAPIPE_WASM_URL);
    }

    const fileset = this.fileset;
    const create = async (delegate: DemoDelegate): Promise<DemoInstance> => {
      if (id === 'face_detector') {
        return {
          kind: 'face_detector',
          model: await FaceDetector.createFromOptions(fileset, {
            baseOptions: { modelAssetPath: FACE_DETECTOR_MODEL_URL, delegate },
            runningMode: 'VIDEO',
          }),
        };
      }

      if (id === 'face_landmarker') {
        return {
          kind: 'face_landmarker',
          model: await FaceLandmarker.createFromOptions(fileset, {
            baseOptions: { modelAssetPath: FACE_LANDMARKER_MODEL_URL, delegate },
            runningMode: 'VIDEO',
            numFaces: 1,
            outputFaceBlendshapes: true,
            outputFacialTransformationMatrixes: true,
          }),
        };
      }

      return {
        kind: 'gesture_recognizer',
        model: await GestureRecognizer.createFromOptions(fileset, {
          baseOptions: { modelAssetPath: GESTURE_RECOGNIZER_MODEL_URL, delegate },
          runningMode: 'VIDEO',
          numHands: 2,
        }),
      };
    };

    let instance: DemoInstance;
    let delegate: DemoDelegate;

    try {
      instance = await create('GPU');
      delegate = 'GPU';
    } catch (gpuError) {
      console.warn(`[demo-models] ${id} GPU delegate 不可用，回退 CPU。`, gpuError);
      instance = await create('CPU');
      delegate = 'CPU';
    }

    // 切换期间又发起了新的切换：丢弃本次结果。
    if (generation !== this.switchGeneration) {
      instance.model.close();
      return;
    }

    this.disposeActive();
    this.active = instance;
    this.activeDelegate = delegate;
  }

  /** 每帧调用；按活跃间隔节流。返回最新检测结果（可能是上一帧的）。 */
  update(video: HTMLVideoElement, timestamp: number): DemoDetection | null {
    if (
      !this.active ||
      !video.videoWidth ||
      !video.videoHeight ||
      timestamp - this.lastDetectionAt < ACTIVE_DETECTION_INTERVAL_MS
    ) {
      return this.latest;
    }

    this.lastDetectionAt = timestamp;
    const startedAt = performance.now();

    try {
      if (this.active.kind === 'face_detector') {
        this.latest = {
          kind: 'face_detector',
          result: this.active.model.detectForVideo(video, timestamp),
        };
      } else if (this.active.kind === 'face_landmarker') {
        this.latest = {
          kind: 'face_landmarker',
          result: this.active.model.detectForVideo(video, timestamp),
        };
      } else {
        this.latest = {
          kind: 'gesture_recognizer',
          result: this.active.model.recognizeForVideo(video, timestamp),
        };
      }
    } finally {
      this.inferenceMs = performance.now() - startedAt;
      this.detectionSeq += 1;
    }

    return this.latest;
  }

  /** 每次新检测递增；用于判断是否有新结果需要注入交互层。 */
  getDetectionSeq(): number {
    return this.detectionSeq;
  }

  getLatest(): DemoDetection | null {
    return this.latest;
  }

  getPerformance(): DemoModelPerformance {
    return {
      inferenceMs: this.inferenceMs,
      delegate: this.active ? this.activeDelegate : null,
    };
  }

  dispose(): void {
    this.switchGeneration += 1;
    this.disposeActive();
    this.fileset = null;
  }

  private disposeActive(): void {
    this.active?.model.close();
    this.active = null;
    this.activeDelegate = null;
    this.latest = null;
    this.inferenceMs = null;
    this.lastDetectionAt = 0;
  }
}

/**
 * 将 Gesture Recognizer 的输出转换为交互引擎的 RawWorkerHand 形状，
 * 使 UI 卡蓄力交互在手势模式下无需双跑手部模型。
 */
export function gestureResultToRawHands(
  result: GestureRecognizerResult,
): RawWorkerHand[] {
  return result.landmarks.map((landmarks, index) => {
    const category = result.handedness[index]?.[0];
    const name = category?.categoryName.toLowerCase();
    const side: TrackedHandSide = name === 'left' || name === 'right' ? name : 'unknown';

    return {
      landmarks: landmarks.map(toNormalizedPoint),
      worldLandmarks: result.worldLandmarks[index]?.map(toNormalizedPoint) ?? null,
      side,
      handednessScore: category?.score ?? 0,
    };
  });
}

function toNormalizedPoint(landmark: {
  x: number;
  y: number;
  z: number;
  visibility?: number;
}): NormalizedPoint {
  return {
    x: landmark.x,
    y: landmark.y,
    z: landmark.z,
    visibility: landmark.visibility,
  };
}
