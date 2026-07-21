import {
  FilesetResolver,
  HandLandmarker,
  type Category,
  type NormalizedLandmark,
} from '@mediapipe/tasks-vision';
import type {
  NormalizedPoint,
  TrackedHandSide,
} from '../utils/types';
import {
  HAND_LANDMARKER_MODEL_URL,
  HAND_LANDMARKER_NUM_HANDS,
  MEDIAPIPE_WASM_URL,
  MIN_HAND_DETECTION_CONFIDENCE,
  MIN_HAND_PRESENCE_CONFIDENCE,
  MIN_HAND_TRACKING_CONFIDENCE,
} from './config';
import type {
  HandLandmarkerDelegate,
  HandLandmarkerWorkerRequest,
  HandLandmarkerWorkerResponse,
  RawWorkerHand,
} from './handLandmarkerWorkerProtocol';

/** Ported verbatim from ar-solar-system-demo. */

interface WorkerScope {
  onmessage: ((event: MessageEvent<HandLandmarkerWorkerRequest>) => void) | null;
  postMessage(message: HandLandmarkerWorkerResponse): void;
  import?: (url: string) => Promise<void>;
  ModuleFactory?: unknown;
  close(): void;
}

const workerScope = globalThis as unknown as WorkerScope;
const ALLOWED_WASM_LOADER_PATHS = new Set(
  [
    'vision_wasm_internal.js',
    'vision_wasm_module_internal.js',
    'vision_wasm_nosimd_internal.js',
  ].map(
    (filename) =>
      new URL(
        `${MEDIAPIPE_WASM_URL}/${filename}`,
        globalThis.location.href,
      ).pathname,
  ),
);
const WASM_FACTORY_BODY_START = 'return async function(moduleArg = {}) {';
const MODULE_SAFE_DEBUG_SHIM =
  'return async function(moduleArg = {}) {\n    var custom_dbg = (...args) => console.warn(...args);';

// FilesetResolver uses this hook when importScripts() is unavailable in a module Worker. The
// module build cannot use importScripts(), while Vite rejects MediaPipe's runtime dynamic import
// of a /public asset in dev. Fetch and compile only the three same-origin synchronized loaders,
// then explicitly expose the Emscripten factory MediaPipe expects during off-thread initialization.
workerScope.import = async (url) => {
  const loaderUrl = new URL(url, globalThis.location.href);
  if (
    loaderUrl.origin !== globalThis.location.origin ||
    !ALLOWED_WASM_LOADER_PATHS.has(loaderUrl.pathname)
  ) {
    throw new Error(`Refused unexpected MediaPipe wasm loader: ${loaderUrl.href}`);
  }

  const response = await fetch(loaderUrl);
  if (!response.ok) {
    throw new Error(`Failed to load MediaPipe wasm bootstrap (HTTP ${response.status}).`);
  }

  const loaderSource = await response.text();
  const moduleSafeLoaderSource = loaderSource.replace(
    WASM_FACTORY_BODY_START,
    MODULE_SAFE_DEBUG_SHIM,
  );
  if (moduleSafeLoaderSource === loaderSource) {
    throw new Error('MediaPipe wasm bootstrap format changed; debug shim was not applied.');
  }

  const loaderModuleUrl = URL.createObjectURL(
    new Blob([moduleSafeLoaderSource, '\nexport { ModuleFactory };\n'], {
      type: 'text/javascript',
    }),
  );

  try {
    const loaderModule = (await import(/* @vite-ignore */ loaderModuleUrl)) as {
      ModuleFactory?: unknown;
    };
    if (typeof loaderModule.ModuleFactory !== 'function') {
      throw new Error('MediaPipe wasm bootstrap did not provide ModuleFactory.');
    }

    workerScope.ModuleFactory = loaderModule.ModuleFactory;
  } finally {
    URL.revokeObjectURL(loaderModuleUrl);
  }
};

let landmarker: HandLandmarker | null = null;
let activeDelegate: HandLandmarkerDelegate | null = null;
let initializePromise: Promise<HandLandmarkerDelegate> | null = null;

workerScope.onmessage = (event) => {
  const message = event.data;

  if (message.type === 'initialize') {
    void initialize().then(
      (delegate) => {
        workerScope.postMessage({ type: 'ready', delegate });
      },
      (error: unknown) => {
        workerScope.postMessage({
          type: 'error',
          phase: 'initialize',
          requestId: null,
          message: toErrorMessage(error),
        });
      },
    );
    return;
  }

  if (message.type === 'detect') {
    runDetection(message);
    return;
  }

  landmarker?.close();
  landmarker = null;
  activeDelegate = null;
  workerScope.close();
};

function initialize(): Promise<HandLandmarkerDelegate> {
  if (initializePromise) {
    return initializePromise;
  }

  initializePromise = (async () => {
    const fileset = await FilesetResolver.forVisionTasks(MEDIAPIPE_WASM_URL);

    try {
      landmarker = await createHandLandmarker(fileset, 'GPU');
      activeDelegate = 'GPU';
    } catch (gpuError) {
      console.warn(
        '[hand-tracker] GPU delegate unavailable; falling back to CPU.',
        gpuError,
      );
      landmarker = await createHandLandmarker(fileset, 'CPU');
      activeDelegate = 'CPU';
    }

    return activeDelegate;
  })();

  return initializePromise;
}

function runDetection(
  message: Extract<HandLandmarkerWorkerRequest, { type: 'detect' }>,
): void {
  try {
    if (!landmarker || !activeDelegate) {
      throw new Error('HandLandmarker worker is not initialized.');
    }

    const startedAt = performance.now();
    const result = landmarker.detectForVideo(message.frame, message.timestamp);
    const inferenceMs = performance.now() - startedAt;
    const hands: RawWorkerHand[] = result.landmarks.map((landmarks, index) => {
      const category = result.handedness[index]?.[0];

      return {
        landmarks: landmarks.map(toNormalizedPoint),
        worldLandmarks:
          result.worldLandmarks[index]?.map(toNormalizedPoint) ?? null,
        side: normalizeHandSide(category),
        handednessScore: category?.score ?? 0,
      };
    });

    workerScope.postMessage({
      type: 'result',
      requestId: message.requestId,
      timestamp: message.timestamp,
      videoCurrentTimeMs: message.videoCurrentTimeMs,
      inferenceMs,
      hands,
    });
  } catch (error) {
    workerScope.postMessage({
      type: 'error',
      phase: 'detect',
      requestId: message.requestId,
      message: toErrorMessage(error),
    });
  } finally {
    message.frame.close();
  }
}

async function createHandLandmarker(
  fileset: Awaited<ReturnType<typeof FilesetResolver.forVisionTasks>>,
  delegate: HandLandmarkerDelegate,
): Promise<HandLandmarker> {
  return HandLandmarker.createFromOptions(fileset, {
    baseOptions: {
      modelAssetPath: HAND_LANDMARKER_MODEL_URL,
      delegate,
    },
    runningMode: 'VIDEO',
    numHands: HAND_LANDMARKER_NUM_HANDS,
    minHandDetectionConfidence: MIN_HAND_DETECTION_CONFIDENCE,
    minHandPresenceConfidence: MIN_HAND_PRESENCE_CONFIDENCE,
    minTrackingConfidence: MIN_HAND_TRACKING_CONFIDENCE,
  });
}

function normalizeHandSide(category: Category | undefined): TrackedHandSide {
  const name = category?.categoryName.toLowerCase();

  if (name === 'left' || name === 'right') {
    return name;
  }

  return 'unknown';
}

function toNormalizedPoint(landmark: NormalizedLandmark): NormalizedPoint {
  return {
    x: landmark.x,
    y: landmark.y,
    z: landmark.z,
    visibility: landmark.visibility,
  };
}

function toErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown HandLandmarker worker error.';
}
