import { averagePoints, distance2d, distance3d } from '../utils/math';
import { LandmarkSmoother } from '../tracking/smoothing';
import { assignVisualHandPair, type PointPair } from './handPairing';
import {
  INDEX_MCP_INDEX,
  INDEX_TIP_INDEX,
  MIDDLE_MCP_INDEX,
  PINKY_MCP_INDEX,
  RING_MCP_INDEX,
  THUMB_TIP_INDEX,
  WRIST_INDEX,
} from './handLandmarks';
import type { NormalizedPoint, TrackedHand, TrackedHandSide } from '../utils/types';

const HAND_SMOOTHING_ALPHA = 0.38;

/** Build a TrackedHand from (un)smoothed landmarks; returns null if required landmarks are missing. */
export function buildTrackedHand(
  landmarks: NormalizedPoint[],
  side: TrackedHandSide,
  handednessScore: number,
  worldLandmarks: NormalizedPoint[] | null = null,
): TrackedHand | null {
  const wrist = landmarks[WRIST_INDEX];
  const thumbTip = landmarks[THUMB_TIP_INDEX];
  const indexFingerTip = landmarks[INDEX_TIP_INDEX];
  const indexMcp = landmarks[INDEX_MCP_INDEX];
  const middleMcp = landmarks[MIDDLE_MCP_INDEX];
  const ringMcp = landmarks[RING_MCP_INDEX];
  const pinkyMcp = landmarks[PINKY_MCP_INDEX];

  if (
    !wrist ||
    !thumbTip ||
    !indexFingerTip ||
    !indexMcp ||
    !middleMcp ||
    !ringMcp ||
    !pinkyMcp
  ) {
    return null;
  }

  return {
    side,
    handednessScore,
    center: averagePoints([wrist, indexMcp, middleMcp, ringMcp, pinkyMcp]),
    wrist,
    thumbTip,
    indexFingerTip,
    palmScale: Math.max(distance3d(wrist, middleMcp), distance3d(indexMcp, pinkyMcp)),
    landmarks,
    worldLandmarks,
  };
}

/**
 * Stabilizes raw MediaPipe hands by assigning visual-left / visual-right identity from the previous
 * frame's trajectory BEFORE smoothing, then smoothing each visual slot against its own history.
 * (Ported verbatim from ar-solar-system-demo.)
 */
export class GestureInputFramePairer {
  private readonly visualLeftSmoother = new LandmarkSmoother(HAND_SMOOTHING_ALPHA);
  private readonly visualRightSmoother = new LandmarkSmoother(HAND_SMOOTHING_ALPHA);
  private previousVisualPair: PointPair | null = null;

  stabilize(rawHands: TrackedHand[]): TrackedHand[] {
    if (rawHands.length === 0) {
      this.reset();
      return [];
    }

    if (rawHands.length === 1) {
      return [this.stabilizeSingle(rawHands[0])];
    }

    return this.stabilizePair(rawHands[0], rawHands[1]);
  }

  reset(): void {
    this.visualLeftSmoother.reset();
    this.visualRightSmoother.reset();
    this.previousVisualPair = null;
  }

  private stabilizePair(first: TrackedHand, second: TrackedHand): TrackedHand[] {
    const { left, right } = assignVisualHandPair(first, second, this.previousVisualPair);
    const smoothedLeft = rebuildHand(left, this.visualLeftSmoother.smooth(left.landmarks));
    const smoothedRight = rebuildHand(right, this.visualRightSmoother.smooth(right.landmarks));

    this.previousVisualPair = {
      left: smoothedLeft.center,
      right: smoothedRight.center,
    };

    return [smoothedLeft, smoothedRight];
  }

  private stabilizeSingle(hand: TrackedHand): TrackedHand {
    const useLeftSlot = this.previousVisualPair
      ? distance2d(hand.center, this.previousVisualPair.left) <=
        distance2d(hand.center, this.previousVisualPair.right)
      : hand.center.x >= 0.5; // visual-left = larger camera x in the mirrored preview

    if (useLeftSlot) {
      this.visualRightSmoother.reset();
      const smoothed = rebuildHand(hand, this.visualLeftSmoother.smooth(hand.landmarks));
      if (this.previousVisualPair) {
        this.previousVisualPair = { ...this.previousVisualPair, left: smoothed.center };
      }
      return smoothed;
    }

    this.visualLeftSmoother.reset();
    const smoothed = rebuildHand(hand, this.visualRightSmoother.smooth(hand.landmarks));
    if (this.previousVisualPair) {
      this.previousVisualPair = { ...this.previousVisualPair, right: smoothed.center };
    }
    return smoothed;
  }
}

function rebuildHand(raw: TrackedHand, smoothedLandmarks: NormalizedPoint[]): TrackedHand {
  // World landmarks are carried through raw (metric, not affected by image-landmark smoothing).
  return (
    buildTrackedHand(smoothedLandmarks, raw.side, raw.handednessScore, raw.worldLandmarks) ?? raw
  );
}
