import type { CameraPermissionStatus } from '../utils/types';

const CAMERA_CONSTRAINTS: MediaStreamConstraints = {
  audio: false,
  video: {
    facingMode: 'user',
    width: { ideal: 1280 },
    height: { ideal: 720 },
    frameRate: { ideal: 30 },
  },
};

export interface CameraStartSuccess {
  ok: true;
  status: 'granted';
  stream: MediaStream;
}

export interface CameraStartFailure {
  ok: false;
  status: Exclude<CameraPermissionStatus, 'idle' | 'requesting' | 'granted'>;
  message: string;
}

export type CameraStartResult = CameraStartSuccess | CameraStartFailure;

export async function startCamera(
  videoElement: HTMLVideoElement,
): Promise<CameraStartResult> {
  if (!navigator.mediaDevices?.getUserMedia) {
    return {
      ok: false,
      status: 'unavailable',
      message: '当前浏览器不支持摄像头访问。',
    };
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia(CAMERA_CONSTRAINTS);
    videoElement.srcObject = stream;
    await videoElement.play();

    return {
      ok: true,
      status: 'granted',
      stream,
    };
  } catch (error) {
    if (error instanceof DOMException) {
      if (error.name === 'NotAllowedError' || error.name === 'SecurityError') {
        return {
          ok: false,
          status: 'denied',
          message: '摄像头权限被拒绝，请允许浏览器访问摄像头后重试。',
        };
      }

      if (error.name === 'NotFoundError' || error.name === 'OverconstrainedError') {
        return {
          ok: false,
          status: 'unavailable',
          message: '没有找到可用摄像头。',
        };
      }
    }

    return {
      ok: false,
      status: 'error',
      message: '摄像头启动失败，请检查浏览器权限或设备占用状态。',
    };
  }
}

export function stopCamera(stream: MediaStream | null): void {
  stream?.getTracks().forEach((track) => track.stop());
}
