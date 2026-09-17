import { withPublicBasePath } from '../utils/publicAssetPath';

// Self-hosted copies synced by scripts/sync-mediapipe-assets.mjs (predev /
// prebuild), so the demo never depends on CDNs at runtime.
export const MEDIAPIPE_WASM_URL = withPublicBasePath('/mediapipe/wasm');

export const HAND_LANDMARKER_MODEL_URL = withPublicBasePath(
  '/mediapipe/models/hand_landmarker.task',
);
export const FACE_DETECTOR_MODEL_URL = withPublicBasePath(
  '/mediapipe/models/blaze_face_short_range.tflite',
);
export const FACE_LANDMARKER_MODEL_URL = withPublicBasePath(
  '/mediapipe/models/face_landmarker.task',
);
export const GESTURE_RECOGNIZER_MODEL_URL = withPublicBasePath(
  '/mediapipe/models/gesture_recognizer.task',
);

// Hand tracking thresholds are identical to ar-solar-system-demo so the
// interaction feel carries over unchanged.
export const HAND_LANDMARKER_NUM_HANDS = 2;
export const MIN_HAND_DETECTION_CONFIDENCE = 0.35;
export const MIN_HAND_PRESENCE_CONFIDENCE = 0.35;
export const MIN_HAND_TRACKING_CONFIDENCE = 0.4;

// UI hold interaction (identical to the reference project's demo cards).
export const UI_HOLD_REQUIRED_MS = 500;
export const UI_BUTTON_FINGER_HIT_SLOP_PX = 12;

// Detection scheduling (identical to the reference project).
export const ACTIVE_DETECTION_INTERVAL_MS = 33;
export const IDLE_DETECTION_INTERVAL_MS = 120;
export const IDLE_DETECTION_DELAY_MS = 2000;
