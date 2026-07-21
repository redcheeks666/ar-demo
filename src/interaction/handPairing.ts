import type { NormalizedPoint, TrackedHand } from '../utils/types';
import { distance2d } from '../utils/math';

/**
 * Single source of truth for visual-left / visual-right hand assignment.
 *
 * Visual-left = the hand the user sees on the LEFT of the mirrored camera preview.
 * Because the preview is mirrored (`transform: scaleX(-1)`), that corresponds to the
 * LARGER camera-frame x. Do NOT confuse this with MediaPipe handedness labels.
 */
export interface TrackedHandPair {
  left: TrackedHand;
  right: TrackedHand;
}

export interface PointPair {
  left: NormalizedPoint;
  right: NormalizedPoint;
}

export function assignVisualHandPair(
  first: TrackedHand,
  second: TrackedHand,
  previousPair: PointPair | null,
): TrackedHandPair {
  if (!previousPair) {
    return first.center.x >= second.center.x
      ? { left: first, right: second }
      : { left: second, right: first };
  }

  const directCost =
    distance2d(first.center, previousPair.left) +
    distance2d(second.center, previousPair.right);
  const swappedCost =
    distance2d(first.center, previousPair.right) +
    distance2d(second.center, previousPair.left);

  return directCost <= swappedCost
    ? { left: first, right: second }
    : { left: second, right: first };
}
