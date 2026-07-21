export type CameraPermissionStatus =
  | 'idle'
  | 'requesting'
  | 'granted'
  | 'denied'
  | 'unavailable'
  | 'error';

export type TrackingStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface NormalizedPoint {
  x: number;
  y: number;
  z: number;
  visibility?: number;
}

export type TrackedHandSide = 'left' | 'right' | 'unknown';

export interface TrackedHand {
  side: TrackedHandSide;
  handednessScore: number;
  center: NormalizedPoint;
  wrist: NormalizedPoint;
  thumbTip: NormalizedPoint;
  indexFingerTip: NormalizedPoint;
  palmScale: number;
  /** Image-space landmarks (normalized 0-1, image-relative z). */
  landmarks: NormalizedPoint[];
  /** MediaPipe world landmarks (metric, palm-relative origin) when available. */
  worldLandmarks: NormalizedPoint[] | null;
}

export interface HandTrackingSnapshot {
  detectedHands: number;
  left: TrackedHand | null;
  right: TrackedHand | null;
  hands: TrackedHand[];
  lastUpdatedAt: number | null;
}

/** 演示模型标识。hand_landmarker 复用常驻交互引擎，不额外建实例。 */
export type DemoModelId =
  | 'face_detector'
  | 'face_landmarker'
  | 'gesture_recognizer'
  | 'hand_landmarker'
  | 'pose_landmarker';
