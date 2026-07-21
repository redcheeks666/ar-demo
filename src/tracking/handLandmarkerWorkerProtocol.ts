import type {
  NormalizedPoint,
  TrackedHandSide,
} from '../utils/types';

/** Ported verbatim from ar-solar-system-demo. */

export type HandLandmarkerDelegate = 'GPU' | 'CPU';

export interface RawWorkerHand {
  readonly landmarks: NormalizedPoint[];
  readonly worldLandmarks: NormalizedPoint[] | null;
  readonly side: TrackedHandSide;
  readonly handednessScore: number;
}

export type HandLandmarkerWorkerRequest =
  | {
      readonly type: 'initialize';
    }
  | {
      readonly type: 'detect';
      readonly requestId: number;
      readonly frame: ImageBitmap;
      readonly timestamp: number;
      readonly videoCurrentTimeMs: number;
    }
  | {
      readonly type: 'dispose';
    };

export type HandLandmarkerWorkerResponse =
  | {
      readonly type: 'ready';
      readonly delegate: HandLandmarkerDelegate;
    }
  | {
      readonly type: 'result';
      readonly requestId: number;
      readonly timestamp: number;
      readonly videoCurrentTimeMs: number;
      readonly inferenceMs: number;
      readonly hands: RawWorkerHand[];
    }
  | {
      readonly type: 'error';
      readonly phase: 'initialize' | 'detect';
      readonly requestId: number | null;
      readonly message: string;
    };
