import type {
  HandLandmarkerDelegate,
  HandLandmarkerWorkerRequest,
  HandLandmarkerWorkerResponse,
  RawWorkerHand,
} from './handLandmarkerWorkerProtocol';

/** Ported verbatim from ar-solar-system-demo. */

export interface HandLandmarkerInferenceResult {
  readonly timestamp: number;
  readonly videoCurrentTimeMs: number;
  readonly inferenceMs: number;
  readonly hands: RawWorkerHand[];
}

export interface HandLandmarkerInferenceClient {
  initialize(): Promise<HandLandmarkerDelegate>;
  detect(
    frame: ImageBitmap,
    timestamp: number,
    videoCurrentTimeMs: number,
  ): Promise<HandLandmarkerInferenceResult>;
  dispose(): void;
}

interface WorkerLike {
  onmessage: ((event: MessageEvent<HandLandmarkerWorkerResponse>) => void) | null;
  onerror: ((event: ErrorEvent) => void) | null;
  postMessage(message: HandLandmarkerWorkerRequest, transfer?: Transferable[]): void;
  terminate(): void;
}

interface PendingDetection {
  readonly requestId: number;
  readonly resolve: (result: HandLandmarkerInferenceResult) => void;
  readonly reject: (error: Error) => void;
}

type WorkerFactory = () => WorkerLike;

export class HandLandmarkerWorkerClient implements HandLandmarkerInferenceClient {
  private readonly worker: WorkerLike;
  private initializePromise: Promise<HandLandmarkerDelegate> | null = null;
  private resolveInitialize: ((delegate: HandLandmarkerDelegate) => void) | null = null;
  private rejectInitialize: ((error: Error) => void) | null = null;
  private pendingDetection: PendingDetection | null = null;
  private nextRequestId = 1;
  private disposed = false;

  constructor(workerFactory: WorkerFactory = createHandLandmarkerWorker) {
    this.worker = workerFactory();
    this.worker.onmessage = (event) => this.handleMessage(event.data);
    this.worker.onerror = (event) => {
      this.failAll(new Error(event.message || 'HandLandmarker worker crashed.'));
    };
  }

  initialize(): Promise<HandLandmarkerDelegate> {
    if (this.disposed) {
      return Promise.reject(new Error('HandLandmarker worker client is disposed.'));
    }

    if (this.initializePromise) {
      return this.initializePromise;
    }

    this.initializePromise = new Promise<HandLandmarkerDelegate>((resolve, reject) => {
      this.resolveInitialize = resolve;
      this.rejectInitialize = reject;
      this.worker.postMessage({ type: 'initialize' });
    });

    return this.initializePromise;
  }

  detect(
    frame: ImageBitmap,
    timestamp: number,
    videoCurrentTimeMs: number,
  ): Promise<HandLandmarkerInferenceResult> {
    if (this.disposed) {
      frame.close();
      return Promise.reject(new Error('HandLandmarker worker client is disposed.'));
    }

    if (this.pendingDetection) {
      frame.close();
      return Promise.reject(new Error('HandLandmarker worker already has a frame in flight.'));
    }

    const requestId = this.nextRequestId;
    this.nextRequestId += 1;

    return new Promise<HandLandmarkerInferenceResult>((resolve, reject) => {
      this.pendingDetection = { requestId, resolve, reject };

      try {
        this.worker.postMessage(
          {
            type: 'detect',
            requestId,
            frame,
            timestamp,
            videoCurrentTimeMs,
          },
          [frame],
        );
      } catch (error) {
        this.pendingDetection = null;
        frame.close();
        reject(toError(error, 'Failed to send a video frame to the worker.'));
      }
    });
  }

  dispose(): void {
    if (this.disposed) {
      return;
    }

    this.disposed = true;
    const error = new Error('HandLandmarker worker client was disposed.');
    this.failAll(error);

    try {
      this.worker.postMessage({ type: 'dispose' });
    } finally {
      this.worker.terminate();
      this.worker.onmessage = null;
      this.worker.onerror = null;
    }
  }

  private handleMessage(message: HandLandmarkerWorkerResponse): void {
    if (this.disposed) {
      return;
    }

    if (message.type === 'ready') {
      this.resolveInitialize?.(message.delegate);
      this.resolveInitialize = null;
      this.rejectInitialize = null;
      return;
    }

    if (message.type === 'error') {
      const error = new Error(message.message);

      if (message.phase === 'initialize') {
        this.rejectInitialize?.(error);
        this.resolveInitialize = null;
        this.rejectInitialize = null;
        return;
      }

      if (this.pendingDetection?.requestId === message.requestId) {
        this.pendingDetection.reject(error);
        this.pendingDetection = null;
      }
      return;
    }

    if (this.pendingDetection?.requestId !== message.requestId) {
      return;
    }

    const pending = this.pendingDetection;
    this.pendingDetection = null;
    pending.resolve({
      timestamp: message.timestamp,
      videoCurrentTimeMs: message.videoCurrentTimeMs,
      inferenceMs: message.inferenceMs,
      hands: message.hands,
    });
  }

  private failAll(error: Error): void {
    this.rejectInitialize?.(error);
    this.pendingDetection?.reject(error);
    this.resolveInitialize = null;
    this.rejectInitialize = null;
    this.pendingDetection = null;
  }
}

function createHandLandmarkerWorker(): WorkerLike {
  return new Worker(new URL('./handLandmarker.worker.ts', import.meta.url), {
    type: 'module',
    name: 'hand-landmarker',
  });
}

function toError(error: unknown, fallback: string): Error {
  return error instanceof Error ? error : new Error(fallback);
}
