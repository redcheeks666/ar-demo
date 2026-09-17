import type { GestureRecognizerResult } from '@mediapipe/tasks-vision';
import {
  EffectsLayer3d,
  type CosmicCubeInteractionFeedback,
} from './effectsLayer3d';
import {
  mapNormalizedToStagePixel,
  type PreviewLayout,
} from '../interaction/coordinateMapping';
import {
  INDEX_MCP_INDEX,
  INDEX_TIP_INDEX,
  MIDDLE_MCP_INDEX,
  MIDDLE_TIP_INDEX,
  PINKY_MCP_INDEX,
  RING_MCP_INDEX,
  THUMB_TIP_INDEX,
  WRIST_INDEX,
} from '../interaction/handLandmarks';
import type { NormalizedPoint } from '../utils/types';

const CONF_THRESHOLD = 0.55;
const STABLE_FRAMES = 3;
const COOLDOWN_MS = 400;
const FIST_CHARGE_MS = 800;
const OPEN_PALM_CHARGE_MS = 3000;
const CUBE_REVEAL_MS = 420;
const CUBE_DISMISS_MS = 380;
const HAND_LOST_GRACE_MS = 250;
const GRAB_STABLE_FRAMES = 3;
const GRAB_RELEASE_RATIO = 1.28;
const PINCH_DISMISS_RATIO = 0.18;
const PINCH_DISMISS_HOLD_MS = 120;
const GOLD = '#f6c177';
const PINK = '#f7a8c4';
const RED = '#ff7185';

const SINGLE_SHOT_GESTURES = ['Victory', 'Thumb_Up', 'Thumb_Down'] as const;

type SingleShotGesture = (typeof SINGLE_SHOT_GESTURES)[number];
type GestureName =
  | 'Closed_Fist'
  | 'Open_Palm'
  | 'Pointing_Up'
  | 'Thumb_Up'
  | 'Thumb_Down'
  | 'Victory'
  | 'ILoveYou';
type OpenPalmPhase =
  | 'IDLE'
  | 'CHARGING'
  | 'REVEALING'
  | 'FLOATING'
  | 'TARGETING'
  | 'GRABBED'
  | 'DISMISSING';

interface HandGestureState {
  confirmed: GestureName | null;
  candidate: GestureName | null;
  candidateFrames: number;
  readonly needsClear: Set<SingleShotGesture>;
  readonly lastFireAt: Map<SingleShotGesture, number>;
  readonly lastEmitAt: Map<GestureName, number>;
  fistChargeStartAt: number | null;
  fistChargeValue: number;
  openPalmPhase: OpenPalmPhase;
  openPalmStartedAt: number | null;
  cubeRevealStartedAt: number | null;
  cubeDismissStartedAt: number | null;
  cubeX: number;
  cubeY: number;
  cubeScale: number;
  cubeMaxScale: number;
  cubeGripCandidate: string | null;
  cubeGripCandidateFrames: number;
  cubeGripAOffset: Point;
  cubeGripBOffset: Point;
  cubeGrabStartDistance: number;
  cubeGrabStartScale: number;
  cubeGrabOffsetX: number;
  cubeGrabOffsetY: number;
  cubePinchStartedAt: number | null;
  cubeLastMidpoint: Point | null;
  cubeMotionEnergy: number;
  lastSeenAt: number;
}

interface Point {
  x: number;
  y: number;
}

interface HandAnchors {
  readonly palm: Point;
  readonly thumbTip: Point;
  readonly indexTip: Point;
  readonly victory: Point;
  readonly palmScale: number;
}

interface GripSelection {
  readonly id: string;
  readonly targetA: Point;
  readonly targetB: Point;
  readonly offsetA: Point;
  readonly offsetB: Point;
  readonly distanceA: number;
  readonly distanceB: number;
}

/**
 * 将 MediaPipe 手势结果去抖后转换成 EffectsLayer3d 的屏幕空间触发指令。
 * 普通特效按手独立；宇宙魔方同一时间只有一个，由完成召唤的手持有。
 */
export class GestureEffectsController {
  private readonly states = new Map<string, HandGestureState>();
  private lastDetectionSequence = -1;
  private likeCount = 0;
  private cubeOwnerKey: string | null = null;

  constructor(private readonly effects: EffectsLayer3d) {}

  update(
    result: GestureRecognizerResult,
    layout: PreviewLayout,
    timestamp: number,
    detectionSequence: number,
  ): void {
    const isNewDetection = detectionSequence !== this.lastDetectionSequence;

    if (isNewDetection) {
      this.lastDetectionSequence = detectionSequence;
    }

    const seenKeys = new Set<string>();

    result.landmarks.forEach((landmarks, index) => {
      if (landmarks.length <= PINKY_MCP_INDEX) {
        return;
      }

      const handedness = result.handedness[index]?.[0]?.categoryName ?? 'Unknown';
      const handKey = `${handedness}-${index}`;
      const anchors = buildAnchors(landmarks, layout);
      const state = this.getState(handKey);
      state.lastSeenAt = timestamp;
      seenKeys.add(handKey);

      if (isNewDetection) {
        const top = result.gestures[index]?.[0];
        const raw = top && top.score >= CONF_THRESHOLD
          ? toGestureName(top.categoryName)
          : null;
        this.updateConfirmation(handKey, state, raw, timestamp);
      }

      this.runConfirmedGesture(handKey, state, anchors, timestamp, isNewDetection);
    });

    for (const [key, state] of this.states) {
      if (seenKeys.has(key)) {
        continue;
      }

      const lostFor = timestamp - state.lastSeenAt;

      if (state.openPalmPhase === 'DISMISSING') {
        this.finishCubeDismissIfNeeded(key, state, timestamp);
        continue;
      }

      if (lostFor <= HAND_LOST_GRACE_MS) {
        continue;
      }

      if (state.openPalmPhase === 'GRABBED' || state.openPalmPhase === 'TARGETING') {
        this.releaseCube(key, state, timestamp);
        continue;
      }

      if (isPersistentCubePhase(state.openPalmPhase)) {
        continue;
      }

      this.effects.clearChargeOrb(key);
      this.effects.clearCosmicCube(key, false);
      this.releaseCubeOwnership(key);
      this.states.delete(key);
    }
  }

  reset(): void {
    for (const key of this.states.keys()) {
      this.effects.clearChargeOrb(key);
      this.effects.clearCosmicCube(key, false);
    }
    this.states.clear();
    this.lastDetectionSequence = -1;
    this.likeCount = 0;
    this.cubeOwnerKey = null;
  }

  private getState(handKey: string): HandGestureState {
    const existing = this.states.get(handKey);

    if (existing) {
      return existing;
    }

    const state: HandGestureState = {
      confirmed: null,
      candidate: null,
      candidateFrames: 0,
      needsClear: new Set<SingleShotGesture>(),
      lastFireAt: new Map<SingleShotGesture, number>(),
      lastEmitAt: new Map<GestureName, number>(),
      fistChargeStartAt: null,
      fistChargeValue: 0,
      openPalmPhase: 'IDLE',
      openPalmStartedAt: null,
      cubeRevealStartedAt: null,
      cubeDismissStartedAt: null,
      cubeX: 0,
      cubeY: 0,
      cubeScale: 0,
      cubeMaxScale: 0,
      cubeGripCandidate: null,
      cubeGripCandidateFrames: 0,
      cubeGripAOffset: { x: 0, y: 0 },
      cubeGripBOffset: { x: 0, y: 0 },
      cubeGrabStartDistance: 0,
      cubeGrabStartScale: 0,
      cubeGrabOffsetX: 0,
      cubeGrabOffsetY: 0,
      cubePinchStartedAt: null,
      cubeLastMidpoint: null,
      cubeMotionEnergy: 0,
      lastSeenAt: 0,
    };
    this.states.set(handKey, state);
    return state;
  }

  private updateConfirmation(
    handKey: string,
    state: HandGestureState,
    raw: GestureName | null,
    timestamp: number,
  ): void {
    if (raw === state.candidate) {
      state.candidateFrames += 1;
    } else {
      state.candidate = raw;
      state.candidateFrames = 1;
    }

    if (state.candidateFrames < STABLE_FRAMES || state.confirmed === state.candidate) {
      return;
    }

    state.confirmed = state.candidate;
    this.handleGestureChange(handKey, state, state.confirmed, timestamp);

    for (const gesture of SINGLE_SHOT_GESTURES) {
      if (gesture !== state.confirmed) {
        state.needsClear.delete(gesture);
      }
    }
  }

  private handleGestureChange(
    handKey: string,
    state: HandGestureState,
    current: GestureName | null,
    timestamp: number,
  ): void {
    this.effects.clearChargeOrb(handKey);
    state.fistChargeStartAt = null;
    state.fistChargeValue = 0;

    // 召唤完成后，魔方拥有独立生命周期。分类器切到 Pointing_Up、None 等标签时
    // 不再销毁魔方，后续控制只读取拇指和食指关键点。
    if (isPersistentCubePhase(state.openPalmPhase)) {
      return;
    }

    if (state.openPalmPhase === 'CHARGING' && current !== 'Open_Palm') {
      this.effects.clearCosmicCube(handKey, false);
      this.resetCubeState(state);
      this.releaseCubeOwnership(handKey);
    }

    if (
      current === 'Open_Palm'
      && state.openPalmPhase === 'IDLE'
      && (this.cubeOwnerKey === null || this.cubeOwnerKey === handKey)
    ) {
      this.cubeOwnerKey = handKey;
      state.openPalmPhase = 'CHARGING';
      state.openPalmStartedAt = timestamp;
    } else if (current === 'Closed_Fist') {
      state.fistChargeStartAt = timestamp;
    }
  }

  private runConfirmedGesture(
    handKey: string,
    state: HandGestureState,
    anchors: HandAnchors,
    timestamp: number,
    isNewDetection: boolean,
  ): void {
    if (isPersistentCubePhase(state.openPalmPhase)) {
      this.runCubeInteraction(
        handKey,
        state,
        anchors,
        timestamp,
        isNewDetection,
      );
      return;
    }

    const gesture = state.confirmed;

    if (!gesture) {
      return;
    }

    if (isSingleShotGesture(gesture)) {
      this.fireSingleShot(state, gesture, anchors, timestamp);
      return;
    }

    switch (gesture) {
      case 'Open_Palm':
        this.runOpenPalm(handKey, state, anchors, timestamp);
        break;
      case 'Closed_Fist':
        this.runClosedFist(handKey, state, anchors, timestamp);
        break;
      case 'ILoveYou':
        if (this.shouldEmit(state, gesture, timestamp, 105)) {
          this.effects.hearts(anchors.palm.x, anchors.palm.y, PINK, anchors.palmScale);
          this.effects.sparkBurst(
            anchors.palm.x,
            anchors.palm.y,
            PINK,
            1,
            anchors.palmScale * 0.75,
            anchors.palmScale * 0.055,
          );
        }
        break;
    }
  }

  private runOpenPalm(
    handKey: string,
    state: HandGestureState,
    anchors: HandAnchors,
    timestamp: number,
  ): void {
    if (state.openPalmPhase === 'CHARGING' && state.openPalmStartedAt !== null) {
      const progress = clamp(
        (timestamp - state.openPalmStartedAt) / OPEN_PALM_CHARGE_MS,
        0,
        1,
      );
      this.effects.updateCubeCharge(
        handKey,
        anchors.palm.x,
        anchors.palm.y,
        progress,
        anchors.palmScale,
        timestamp,
      );

      if (progress >= 1) {
        state.openPalmPhase = 'REVEALING';
        state.cubeRevealStartedAt = timestamp;
        state.cubeX = anchors.palm.x;
        state.cubeY = anchors.palm.y;
        state.cubeMaxScale = anchors.palmScale * 0.78;
        state.cubeScale = state.cubeMaxScale;
        this.effects.revealCosmicCube(
          handKey,
          state.cubeX,
          state.cubeY,
          anchors.palmScale,
          timestamp,
        );
      }
      return;
    }
  }

  private runCubeInteraction(
    handKey: string,
    state: HandGestureState,
    anchors: HandAnchors,
    timestamp: number,
    isNewDetection: boolean,
  ): void {
    if (state.openPalmPhase === 'DISMISSING') {
      this.finishCubeDismissIfNeeded(handKey, state, timestamp);
      return;
    }

    if (state.openPalmPhase === 'REVEALING') {
      this.effects.updateCosmicCubePose(
        handKey,
        state.cubeX,
        state.cubeY,
        state.cubeScale,
        timestamp,
        { mode: 'floating' },
      );

      if (
        state.cubeRevealStartedAt !== null
        && timestamp - state.cubeRevealStartedAt >= CUBE_REVEAL_MS
      ) {
        state.openPalmPhase = 'FLOATING';
      }
      return;
    }

    if (state.openPalmPhase === 'GRABBED') {
      this.updateGrabbedCube(handKey, state, anchors, timestamp, isNewDetection);
      return;
    }

    this.updateFloatingCube(handKey, state, anchors, timestamp, isNewDetection);
  }

  private updateFloatingCube(
    handKey: string,
    state: HandGestureState,
    anchors: HandAnchors,
    timestamp: number,
    isNewDetection: boolean,
  ): void {
    const selection = selectGripTargets(
      state.cubeX,
      state.cubeY,
      state.cubeScale,
      anchors.thumbTip,
      anchors.indexTip,
    );
    const hitRadius = Math.max(18, state.cubeScale * 0.32);
    const canTarget = state.confirmed !== 'Open_Palm';
    const hoverA = canTarget
      ? clamp(1 - selection.distanceA / (hitRadius * 1.65), 0, 1)
      : 0;
    const hoverB = canTarget
      ? clamp(1 - selection.distanceB / (hitRadius * 1.65), 0, 1)
      : 0;
    const bothInside = canTarget
      && selection.distanceA <= hitRadius
      && selection.distanceB <= hitRadius;

    if (bothInside) {
      if (state.cubeGripCandidate === selection.id) {
        if (isNewDetection) {
          state.cubeGripCandidateFrames += 1;
        }
      } else {
        state.cubeGripCandidate = selection.id;
        state.cubeGripCandidateFrames = isNewDetection ? 1 : 0;
      }
    } else {
      state.cubeGripCandidate = null;
      state.cubeGripCandidateFrames = 0;
    }

    state.openPalmPhase = Math.max(hoverA, hoverB) > 0.08
      ? 'TARGETING'
      : 'FLOATING';

    if (state.cubeGripCandidateFrames >= GRAB_STABLE_FRAMES) {
      this.captureCube(handKey, state, anchors, selection, timestamp);
      return;
    }

    this.effects.updateCosmicCubePose(
      handKey,
      state.cubeX,
      state.cubeY,
      state.cubeScale,
      timestamp,
      {
        mode: state.openPalmPhase === 'TARGETING' ? 'targeting' : 'floating',
        targetA: selection.targetA,
        targetB: selection.targetB,
        fingerA: anchors.thumbTip,
        fingerB: anchors.indexTip,
        hoverA,
        hoverB,
        motionEnergy: 0,
      },
    );
  }

  private captureCube(
    handKey: string,
    state: HandGestureState,
    anchors: HandAnchors,
    selection: GripSelection,
    timestamp: number,
  ): void {
    const midpoint = meanPoint([anchors.thumbTip, anchors.indexTip]);
    const fingerDistance = distanceBetween(anchors.thumbTip, anchors.indexTip);
    state.openPalmPhase = 'GRABBED';
    state.cubeGripAOffset = selection.offsetA;
    state.cubeGripBOffset = selection.offsetB;
    state.cubeGrabStartDistance = Math.max(fingerDistance, state.cubeScale * 0.45);
    state.cubeGrabStartScale = state.cubeScale;
    state.cubeGrabOffsetX = state.cubeX - midpoint.x;
    state.cubeGrabOffsetY = state.cubeY - midpoint.y;
    state.cubePinchStartedAt = null;
    state.cubeLastMidpoint = midpoint;
    state.cubeMotionEnergy = 0.35;
    state.cubeGripCandidate = null;
    state.cubeGripCandidateFrames = 0;

    this.effects.updateCosmicCubePose(
      handKey,
      state.cubeX,
      state.cubeY,
      state.cubeScale,
      timestamp,
      this.buildGrabFeedback(state, anchors, 0.35),
    );
  }

  private updateGrabbedCube(
    handKey: string,
    state: HandGestureState,
    anchors: HandAnchors,
    timestamp: number,
    isNewDetection: boolean,
  ): void {
    const midpoint = meanPoint([anchors.thumbTip, anchors.indexTip]);
    const fingerDistance = distanceBetween(anchors.thumbTip, anchors.indexTip);
    const releaseDistance = state.cubeGrabStartDistance * GRAB_RELEASE_RATIO;
    const releasedByOpenPalm = state.confirmed === 'Open_Palm'
      && fingerDistance >= state.cubeGrabStartDistance * 0.82;

    if (fingerDistance > releaseDistance || releasedByOpenPalm) {
      this.releaseCube(handKey, state, timestamp);
      return;
    }

    state.cubeX = midpoint.x + state.cubeGrabOffsetX;
    state.cubeY = midpoint.y + state.cubeGrabOffsetY;
    state.cubeScale = clamp(
      state.cubeGrabStartScale * (fingerDistance / state.cubeGrabStartDistance),
      state.cubeMaxScale * PINCH_DISMISS_RATIO,
      state.cubeMaxScale,
    );

    if (isNewDetection && state.cubeLastMidpoint) {
      const movement = distanceBetween(midpoint, state.cubeLastMidpoint);
      const sampledEnergy = clamp(
        movement / Math.max(12, state.cubeMaxScale * 0.32),
        0,
        1,
      );
      state.cubeMotionEnergy = Math.max(sampledEnergy, state.cubeMotionEnergy * 0.72);
      state.cubeLastMidpoint = midpoint;
    } else {
      state.cubeMotionEnergy *= 0.94;
    }

    const dismissDistance = state.cubeGrabStartDistance * PINCH_DISMISS_RATIO;

    if (fingerDistance <= dismissDistance) {
      state.cubePinchStartedAt ??= timestamp;

      if (timestamp - state.cubePinchStartedAt >= PINCH_DISMISS_HOLD_MS) {
        state.openPalmPhase = 'DISMISSING';
        state.cubeDismissStartedAt = timestamp;
        this.effects.dismissCosmicCube(handKey, timestamp);
        return;
      }
    } else {
      state.cubePinchStartedAt = null;
    }

    this.effects.updateCosmicCubePose(
      handKey,
      state.cubeX,
      state.cubeY,
      state.cubeScale,
      timestamp,
      this.buildGrabFeedback(state, anchors, state.cubeMotionEnergy),
    );
  }

  private buildGrabFeedback(
    state: HandGestureState,
    anchors: HandAnchors,
    motionEnergy: number,
  ): CosmicCubeInteractionFeedback {
    return {
      mode: 'grabbed',
      targetA: offsetPoint(
        state.cubeX,
        state.cubeY,
        state.cubeGripAOffset,
        state.cubeScale,
      ),
      targetB: offsetPoint(
        state.cubeX,
        state.cubeY,
        state.cubeGripBOffset,
        state.cubeScale,
      ),
      fingerA: anchors.thumbTip,
      fingerB: anchors.indexTip,
      hoverA: 1,
      hoverB: 1,
      motionEnergy,
    };
  }

  private releaseCube(
    handKey: string,
    state: HandGestureState,
    timestamp: number,
  ): void {
    state.openPalmPhase = 'FLOATING';
    state.cubeGripCandidate = null;
    state.cubeGripCandidateFrames = 0;
    state.cubePinchStartedAt = null;
    state.cubeLastMidpoint = null;
    state.cubeMotionEnergy = 0;
    this.effects.updateCosmicCubePose(
      handKey,
      state.cubeX,
      state.cubeY,
      state.cubeScale,
      timestamp,
      { mode: 'floating' },
    );
  }

  private finishCubeDismissIfNeeded(
    handKey: string,
    state: HandGestureState,
    timestamp: number,
  ): void {
    if (
      state.cubeDismissStartedAt === null
      || timestamp - state.cubeDismissStartedAt < CUBE_DISMISS_MS
    ) {
      return;
    }

    this.effects.clearCosmicCube(handKey, false);
    this.resetCubeState(state);
    this.releaseCubeOwnership(handKey);
  }

  private resetCubeState(state: HandGestureState): void {
    state.openPalmPhase = 'IDLE';
    state.openPalmStartedAt = null;
    state.cubeRevealStartedAt = null;
    state.cubeDismissStartedAt = null;
    state.cubeGripCandidate = null;
    state.cubeGripCandidateFrames = 0;
    state.cubeGrabStartDistance = 0;
    state.cubeGrabStartScale = 0;
    state.cubePinchStartedAt = null;
    state.cubeLastMidpoint = null;
    state.cubeMotionEnergy = 0;
  }

  private releaseCubeOwnership(handKey: string): void {
    if (this.cubeOwnerKey === handKey) {
      this.cubeOwnerKey = null;
    }
  }

  private runClosedFist(
    handKey: string,
    state: HandGestureState,
    anchors: HandAnchors,
    timestamp: number,
  ): void {
    if (state.fistChargeStartAt === null) {
      return;
    }

    state.fistChargeValue = clamp(
      (timestamp - state.fistChargeStartAt) / FIST_CHARGE_MS,
      0,
      1,
    );

    this.effects.chargeOrb(
      handKey,
      anchors.palm.x,
      anchors.palm.y,
      state.fistChargeValue,
      PINK,
      anchors.palmScale,
      false,
      timestamp,
    );
  }

  private fireSingleShot(
    state: HandGestureState,
    gesture: SingleShotGesture,
    anchors: HandAnchors,
    timestamp: number,
  ): void {
    const lastFireAt = state.lastFireAt.get(gesture) ?? Number.NEGATIVE_INFINITY;

    if (state.needsClear.has(gesture) || timestamp - lastFireAt <= COOLDOWN_MS) {
      return;
    }

    state.needsClear.add(gesture);
    state.lastFireAt.set(gesture, timestamp);

    if (gesture === 'Victory') {
      this.effects.confetti(anchors.victory.x, anchors.victory.y, anchors.palmScale);
      this.effects.sparkBurst(
        anchors.victory.x,
        anchors.victory.y,
        GOLD,
        30,
        anchors.palmScale * 5,
        anchors.palmScale * 0.08,
      );
      this.effects.floatText(
        anchors.victory.x,
        anchors.victory.y,
        '✦',
        GOLD,
        clamp(anchors.palmScale * 0.72, 36, 82),
      );
      return;
    }

    if (gesture === 'Thumb_Up') {
      this.likeCount += 1;
      this.effects.floatText(
        anchors.thumbTip.x,
        anchors.thumbTip.y,
        '+1',
        GOLD,
        clamp(anchors.palmScale * 0.7, 38, 84),
      );
      this.effects.floatText(
        anchors.thumbTip.x + anchors.palmScale * 0.25,
        anchors.thumbTip.y + anchors.palmScale * 0.45,
        `👍  ${this.likeCount}`,
        '#ffffff',
        clamp(anchors.palmScale * 0.34, 24, 46),
      );
      this.effects.sparkBurst(
        anchors.thumbTip.x,
        anchors.thumbTip.y,
        GOLD,
        26,
        anchors.palmScale * 4.2,
        anchors.palmScale * 0.075,
      );
      return;
    }

    this.effects.floatText(
      anchors.thumbTip.x,
      anchors.thumbTip.y,
      '-1',
      RED,
      clamp(anchors.palmScale * 0.7, 38, 84),
      'down',
    );
    this.effects.sparkBurst(
      anchors.thumbTip.x,
      anchors.thumbTip.y,
      RED,
      18,
      anchors.palmScale * 3.2,
      anchors.palmScale * 0.07,
    );
  }

  private shouldEmit(
    state: HandGestureState,
    gesture: GestureName,
    timestamp: number,
    intervalMs: number,
  ): boolean {
    const lastAt = state.lastEmitAt.get(gesture) ?? Number.NEGATIVE_INFINITY;

    if (timestamp - lastAt < intervalMs) {
      return false;
    }

    state.lastEmitAt.set(gesture, timestamp);
    return true;
  }

}

function buildAnchors(
  landmarks: readonly NormalizedPoint[],
  layout: PreviewLayout,
): HandAnchors {
  const wrist = mapLandmark(landmarks, WRIST_INDEX, layout);
  const indexMcp = mapLandmark(landmarks, INDEX_MCP_INDEX, layout);
  const middleMcp = mapLandmark(landmarks, MIDDLE_MCP_INDEX, layout);
  const ringMcp = mapLandmark(landmarks, RING_MCP_INDEX, layout);
  const pinkyMcp = mapLandmark(landmarks, PINKY_MCP_INDEX, layout);
  const indexTip = mapLandmark(landmarks, INDEX_TIP_INDEX, layout);
  const middleTip = mapLandmark(landmarks, MIDDLE_TIP_INDEX, layout);

  return {
    palm: meanPoint([wrist, indexMcp, middleMcp, ringMcp, pinkyMcp]),
    thumbTip: mapLandmark(landmarks, THUMB_TIP_INDEX, layout),
    indexTip,
    victory: {
      x: (indexTip.x + middleTip.x) * 0.5,
      y: (indexTip.y + middleTip.y) * 0.5,
    },
    palmScale: clamp(Math.hypot(wrist.x - middleMcp.x, wrist.y - middleMcp.y), 18, 180),
  };
}

function mapLandmark(
  landmarks: readonly NormalizedPoint[],
  index: number,
  layout: PreviewLayout,
): Point {
  const point = landmarks[index];

  if (!point) {
    return { x: 0, y: 0 };
  }

  return mapNormalizedToStagePixel(point, layout);
}

function meanPoint(points: readonly Point[]): Point {
  const total = points.reduce(
    (sum, point) => ({ x: sum.x + point.x, y: sum.y + point.y }),
    { x: 0, y: 0 },
  );
  return { x: total.x / points.length, y: total.y / points.length };
}

function selectGripTargets(
  cubeX: number,
  cubeY: number,
  cubeScale: number,
  fingerA: Point,
  fingerB: Point,
): GripSelection {
  const corner = 0.39;
  const pairs = [
    { id: 'nw-se', a: { x: -corner, y: -corner }, b: { x: corner, y: corner } },
    { id: 'se-nw', a: { x: corner, y: corner }, b: { x: -corner, y: -corner } },
    { id: 'ne-sw', a: { x: corner, y: -corner }, b: { x: -corner, y: corner } },
    { id: 'sw-ne', a: { x: -corner, y: corner }, b: { x: corner, y: -corner } },
  ] as const;

  let best: GripSelection | null = null;

  for (const pair of pairs) {
    const targetA = offsetPoint(cubeX, cubeY, pair.a, cubeScale);
    const targetB = offsetPoint(cubeX, cubeY, pair.b, cubeScale);
    const distanceA = distanceBetween(fingerA, targetA);
    const distanceB = distanceBetween(fingerB, targetB);
    const selection: GripSelection = {
      id: pair.id,
      targetA,
      targetB,
      offsetA: pair.a,
      offsetB: pair.b,
      distanceA,
      distanceB,
    };

    if (!best || distanceA + distanceB < best.distanceA + best.distanceB) {
      best = selection;
    }
  }

  return best ?? {
    id: 'fallback',
    targetA: { x: cubeX, y: cubeY },
    targetB: { x: cubeX, y: cubeY },
    offsetA: { x: 0, y: 0 },
    offsetB: { x: 0, y: 0 },
    distanceA: Number.POSITIVE_INFINITY,
    distanceB: Number.POSITIVE_INFINITY,
  };
}

function offsetPoint(
  originX: number,
  originY: number,
  offset: Point,
  scale: number,
): Point {
  return {
    x: originX + offset.x * scale,
    y: originY + offset.y * scale,
  };
}

function distanceBetween(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function toGestureName(categoryName: string): GestureName | null {
  switch (categoryName) {
    case 'Closed_Fist':
    case 'Open_Palm':
    case 'Pointing_Up':
    case 'Thumb_Up':
    case 'Thumb_Down':
    case 'Victory':
    case 'ILoveYou':
      return categoryName;
    default:
      return null;
  }
}

function isSingleShotGesture(gesture: GestureName): gesture is SingleShotGesture {
  return gesture === 'Victory' || gesture === 'Thumb_Up' || gesture === 'Thumb_Down';
}

function isPersistentCubePhase(phase: OpenPalmPhase): boolean {
  return phase === 'REVEALING'
    || phase === 'FLOATING'
    || phase === 'TARGETING'
    || phase === 'GRABBED'
    || phase === 'DISMISSING';
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}
