import {
  GestureInputFramePairer,
  buildTrackedHand,
} from '../interaction/gestureInputFrame';
import type {
  HandTrackingSnapshot,
  TrackedHand,
} from '../utils/types';
import type { RawWorkerHand } from './handLandmarkerWorkerProtocol';

/** Ported verbatim from ar-solar-system-demo. */

const EMPTY_HANDS_SNAPSHOT: HandTrackingSnapshot = {
  detectedHands: 0,
  left: null,
  right: null,
  hands: [],
  lastUpdatedAt: null,
};

/**
 * Converts raw MediaPipe output into the gesture input shape. Visual identity assignment and
 * smoothing stay on the main thread so inference location cannot change gesture semantics.
 */
export class HandTrackingResultProcessor {
  private readonly pairer = new GestureInputFramePairer();

  process(
    workerHands: readonly RawWorkerHand[],
    timestamp: number,
  ): HandTrackingSnapshot {
    if (workerHands.length === 0) {
      this.pairer.reset();
      return {
        ...EMPTY_HANDS_SNAPSHOT,
        lastUpdatedAt: timestamp,
      };
    }

    const rawHands: TrackedHand[] = [];

    for (const workerHand of workerHands) {
      const hand = buildTrackedHand(
        workerHand.landmarks,
        workerHand.side,
        workerHand.handednessScore,
        workerHand.worldLandmarks,
      );

      if (hand) {
        rawHands.push(hand);
      }
    }

    // Assign visual identity before smoothing, per the existing gesture-input invariant.
    const hands = this.pairer.stabilize(rawHands);

    return {
      detectedHands: hands.length,
      left: chooseBestHand(hands, 'left'),
      right: chooseBestHand(hands, 'right'),
      hands,
      lastUpdatedAt: timestamp,
    };
  }

  reset(): void {
    this.pairer.reset();
  }
}

function chooseBestHand(
  hands: TrackedHand[],
  side: 'left' | 'right',
): TrackedHand | null {
  return hands
    .filter((hand) => hand.side === side)
    .sort((a, b) => b.handednessScore - a.handednessScore)[0] ?? null;
}
