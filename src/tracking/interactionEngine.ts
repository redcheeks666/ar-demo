import type { HandTrackingSnapshot, TrackingStatus } from '../utils/types';
import { HandTrackingResultProcessor } from './handTracker';
import {
  HandLandmarkerWorkerClient,
  type HandLandmarkerInferenceClient,
} from './handLandmarkerWorkerClient';
import type { HandLandmarkerDelegate } from './handLandmarkerWorkerProtocol';
import {
  ACTIVE_DETECTION_INTERVAL_MS,
  IDLE_DETECTION_DELAY_MS,
  IDLE_DETECTION_INTERVAL_MS,
} from './config';

/**
 * 常驻手部交互引擎（简化自 ar-solar-system-demo 的 MediaPipeTrackingController，去掉 Pose）。
 *
 * HandLandmarker 推理在专用 Worker 中运行；渲染循环只读最新完成的快照。
 * 演示 Gesture Recognizer 时可 suspend（暂停送帧），交互手数据改由识别器输出直接注入。
 */
export interface EnginePerformanceSnapshot {
  inferenceMs: number | null;
  workerRoundTripMs: number | null;
  delegate: HandLandmarkerDelegate | null;
  trackingHz: number | null;
}

export interface EngineSnapshot {
  status: TrackingStatus;
  error: string | null;
  hands: HandTrackingSnapshot;
  lastUpdatedAt: number | null;
}

const EMPTY_HANDS: HandTrackingSnapshot = {
  detectedHands: 0,
  left: null,
  right: null,
  hands: [],
  lastUpdatedAt: null,
};

export class InteractionEngine {
  private readonly handResultProcessor = new HandTrackingResultProcessor();
  private handClient: HandLandmarkerInferenceClient | null = null;
  private initializePromise: Promise<void> | null = null;
  private frameInFlight = false;
  private suspended = false;
  private lastDetectionAt = 0;
  private noHandsSinceAt: number | null = null;
  private previousDetectionAt: number | null = null;
  private smoothedTrackingHz: number | null = null;
  private snapshot: EngineSnapshot = createEmptySnapshot('idle');
  private performanceSnapshot: EnginePerformanceSnapshot = {
    inferenceMs: null,
    workerRoundTripMs: null,
    delegate: null,
    trackingHz: null,
  };

  initialize(): Promise<void> {
    if (this.initializePromise) {
      return this.initializePromise;
    }

    this.snapshot = createEmptySnapshot('loading');
    this.initializePromise = (async () => {
      this.handClient = new HandLandmarkerWorkerClient();

      try {
        const delegate = await this.handClient.initialize();
        this.performanceSnapshot = { ...this.performanceSnapshot, delegate };
        this.snapshot = createEmptySnapshot('ready');
      } catch (error) {
        this.handClient.dispose();
        this.handClient = null;
        this.snapshot = {
          ...createEmptySnapshot('error'),
          error: toErrorMessage(error, '手势交互引擎加载失败。'),
        };
        throw error;
      }
    })();

    return this.initializePromise;
  }

  /**
   * 挂起/恢复送帧。挂起时快照的手数据由外部（Gesture Recognizer 输出）注入。
   */
  setSuspended(suspended: boolean): void {
    if (this.suspended === suspended) {
      return;
    }

    this.suspended = suspended;
    this.handResultProcessor.reset();
    this.noHandsSinceAt = null;
    this.previousDetectionAt = null;
  }

  /** 供 Gesture Recognizer 模式注入手部快照，保持交互层数据源单一。 */
  injectHands(hands: HandTrackingSnapshot): void {
    if (this.snapshot.status !== 'ready') {
      return;
    }

    this.snapshot = {
      ...this.snapshot,
      hands,
      lastUpdatedAt: hands.lastUpdatedAt,
    };
  }

  update(video: HTMLVideoElement, timestamp: number): EngineSnapshot {
    if (
      this.suspended ||
      this.snapshot.status !== 'ready' ||
      !this.handClient ||
      !video.videoWidth ||
      !video.videoHeight ||
      this.frameInFlight ||
      timestamp - this.lastDetectionAt < this.getDetectionIntervalMs(timestamp)
    ) {
      return this.snapshot;
    }

    this.lastDetectionAt = timestamp;
    this.frameInFlight = true;
    const roundTripStartedAt = performance.now();
    const videoCurrentTimeMs = video.currentTime * 1000;

    void createImageBitmap(video)
      .then((frame) => {
        if (!this.handClient || this.snapshot.status !== 'ready') {
          frame.close();
          return null;
        }

        return this.handClient.detect(frame, timestamp, videoCurrentTimeMs);
      })
      .then((result) => {
        if (!result || this.suspended) {
          return;
        }

        const hands = this.handResultProcessor.process(result.hands, result.timestamp);

        if (hands.detectedHands > 0) {
          this.noHandsSinceAt = null;
        } else if (this.noHandsSinceAt === null) {
          this.noHandsSinceAt = result.timestamp;
        }

        this.updateTrackingHz(result.timestamp);
        this.performanceSnapshot = {
          ...this.performanceSnapshot,
          inferenceMs: result.inferenceMs,
          workerRoundTripMs: performance.now() - roundTripStartedAt,
          trackingHz: this.smoothedTrackingHz,
        };
        this.snapshot = {
          status: 'ready',
          error: null,
          hands,
          lastUpdatedAt: result.timestamp,
        };
      })
      .catch((error: unknown) => {
        this.snapshot = {
          ...this.snapshot,
          status: 'error',
          error: toErrorMessage(error, '手势检测失败。'),
        };
        this.handClient?.dispose();
        this.handClient = null;
      })
      .finally(() => {
        this.frameInFlight = false;
      });

    return this.snapshot;
  }

  getSnapshot(): EngineSnapshot {
    return this.snapshot;
  }

  getPerformanceSnapshot(): EnginePerformanceSnapshot {
    return { ...this.performanceSnapshot };
  }

  dispose(): void {
    this.handClient?.dispose();
    this.handClient = null;
    this.initializePromise = null;
    this.frameInFlight = false;
    this.handResultProcessor.reset();
    this.snapshot = createEmptySnapshot('idle');
  }

  private updateTrackingHz(timestamp: number): void {
    if (this.previousDetectionAt === null) {
      this.previousDetectionAt = timestamp;
      return;
    }

    const deltaMs = timestamp - this.previousDetectionAt;
    this.previousDetectionAt = timestamp;

    if (deltaMs <= 0) {
      return;
    }

    const instantHz = 1000 / deltaMs;
    this.smoothedTrackingHz =
      this.smoothedTrackingHz === null
        ? instantHz
        : this.smoothedTrackingHz + (instantHz - this.smoothedTrackingHz) * 0.2;
  }

  private getDetectionIntervalMs(timestamp: number): number {
    if (
      this.noHandsSinceAt !== null &&
      timestamp - this.noHandsSinceAt >= IDLE_DETECTION_DELAY_MS
    ) {
      return IDLE_DETECTION_INTERVAL_MS;
    }

    return ACTIVE_DETECTION_INTERVAL_MS;
  }
}

function createEmptySnapshot(status: TrackingStatus): EngineSnapshot {
  return {
    status,
    error: null,
    hands: { ...EMPTY_HANDS },
    lastUpdatedAt: null,
  };
}

function toErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
