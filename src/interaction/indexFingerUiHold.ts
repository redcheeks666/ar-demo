import { mapNormalizedToStagePixel, type PreviewLayout } from './coordinateMapping';
import type { TrackedHand } from '../utils/types';

/** Ported verbatim from ar-solar-system-demo/src/interaction/indexFingerUiHold.ts */

export interface UiButtonStageRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface IndexFingerUiHit {
  handKey: string;
  point: {
    x: number;
    y: number;
  };
  hitScore: number;
}

export interface IndexFingerUiHoldState {
  startedAt: number | null;
  handKey: string | null;
}

export interface IndexFingerUiHoldUpdate {
  hold: IndexFingerUiHoldState;
  hit: IndexFingerUiHit | null;
  elapsedMs: number;
  progress: number;
  triggered: boolean;
  reason: 'inactive' | 'invalid-rect' | 'waiting-for-index-finger' | 'charging' | 'charged';
}

export const EMPTY_INDEX_FINGER_UI_HOLD: IndexFingerUiHoldState = {
  startedAt: null,
  handKey: null,
};

export function updateIndexFingerUiHold(input: {
  active: boolean;
  hands: readonly TrackedHand[];
  layout: PreviewLayout;
  rect: UiButtonStageRect | null;
  previous: IndexFingerUiHoldState;
  requiredMs: number;
  timestamp: number;
  hitSlopPx?: number;
}): IndexFingerUiHoldUpdate {
  if (!input.active) {
    return createEmptyHoldUpdate('inactive');
  }

  if (!input.rect || input.rect.width <= 0 || input.rect.height <= 0) {
    return createEmptyHoldUpdate('invalid-rect');
  }

  const hit = findIndexFingerUiHit({
    hands: input.hands,
    layout: input.layout,
    rect: input.rect,
    hitSlopPx: input.hitSlopPx ?? 0,
  });

  if (!hit) {
    return createEmptyHoldUpdate('waiting-for-index-finger');
  }

  const sameHandContinues = input.previous.handKey === hit.handKey;
  const startedAt =
    sameHandContinues && input.previous.startedAt !== null
      ? input.previous.startedAt
      : input.timestamp;
  const elapsedMs = Math.max(0, input.timestamp - startedAt);
  const progress = Math.min(1, elapsedMs / input.requiredMs);
  const triggered = progress >= 1;

  return {
    hold: {
      startedAt,
      handKey: hit.handKey,
    },
    hit,
    elapsedMs,
    progress,
    triggered,
    reason: triggered ? 'charged' : 'charging',
  };
}

export function findIndexFingerUiHit(input: {
  hands: readonly TrackedHand[];
  layout: PreviewLayout;
  rect: UiButtonStageRect;
  hitSlopPx?: number;
}): IndexFingerUiHit | null {
  const hitSlopPx = input.hitSlopPx ?? 0;
  const left = input.rect.x - hitSlopPx;
  const right = input.rect.x + input.rect.width + hitSlopPx;
  const top = input.rect.y - hitSlopPx;
  const bottom = input.rect.y + input.rect.height + hitSlopPx;
  const centerX = input.rect.x + input.rect.width / 2;
  const centerY = input.rect.y + input.rect.height / 2;
  const maxDistance = Math.max(
    Math.hypot(input.rect.width, input.rect.height) / 2,
    1,
  );
  let bestHit: IndexFingerUiHit | null = null;
  let bestDistance = Number.POSITIVE_INFINITY;

  input.hands.forEach((hand, index) => {
    const point = mapNormalizedToStagePixel(hand.indexFingerTip, input.layout);

    if (
      point.x < left ||
      point.x > right ||
      point.y < top ||
      point.y > bottom
    ) {
      return;
    }

    const distance = Math.hypot(point.x - centerX, point.y - centerY);

    if (distance >= bestDistance) {
      return;
    }

    bestDistance = distance;
    bestHit = {
      handKey: `${hand.side}-${index}`,
      point,
      hitScore: Math.max(0, 1 - distance / maxDistance),
    };
  });

  return bestHit;
}

function createEmptyHoldUpdate(
  reason: IndexFingerUiHoldUpdate['reason'],
): IndexFingerUiHoldUpdate {
  return {
    hold: EMPTY_INDEX_FINGER_UI_HOLD,
    hit: null,
    elapsedMs: 0,
    progress: 0,
    triggered: false,
    reason,
  };
}
