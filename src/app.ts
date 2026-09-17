import { startCamera, stopCamera } from './camera/camera';
import { UiHoldSfx } from './audio/sfx';
import { EffectsLayer3d } from './effects/effectsLayer3d';
import { GestureEffectsController } from './effects/gestureEffectsController';
import { UiHoldButton } from './interaction/uiHoldButton';
import type { PreviewLayout } from './interaction/coordinateMapping';
import { FaceReactionController } from './reactions/faceReactionController';
import { HelmetRenderer } from './rendering/helmetRenderer';
import { OverlayRenderer, type OverlayContent } from './rendering/overlayRenderer';
import {
  DemoModelController,
  gestureResultToRawHands,
  type DemoDetection,
} from './tracking/demoModels';
import { HandTrackingResultProcessor } from './tracking/handTracker';
import { InteractionEngine, type EngineSnapshot } from './tracking/interactionEngine';
import { FacePoseAdapter } from './tracking/facePoseAdapter';
import { MODEL_INFO, MODEL_ORDER } from './demo/modelInfo';
import type { DemoModelId } from './utils/types';

type Phase = 'intro' | 'starting' | 'loading' | 'running';

export function bootstrapApp(): void {
  new DemoApp();
}

class DemoApp {
  // DOM
  private readonly video = requireElement<HTMLVideoElement>('camera-video');
  private readonly helmetCanvas = requireElement<HTMLCanvasElement>('helmet-canvas');
  private readonly overlayCanvas = requireElement<HTMLCanvasElement>('overlay-canvas');
  private readonly effectsCanvas = requireElement<HTMLCanvasElement>('effects-canvas');
  private readonly faceReactionElement = requireElement<HTMLDivElement>('face-reaction');
  private readonly stageOverlay = requireElement<HTMLDivElement>('preview-overlay');
  private readonly introPanel = requireElement<HTMLElement>('intro-panel');
  private readonly statusText = requireElement<HTMLParagraphElement>('status-text');
  private readonly startButton = requireElement<HTMLButtonElement>('start-camera');
  private readonly loadingPanel = requireElement<HTMLElement>('loading-panel');
  private readonly loadingStage = requireElement<HTMLHeadingElement>('loading-stage');
  private readonly loadingDetail = requireElement<HTMLParagraphElement>('loading-detail');
  private readonly hudLeft = requireElement<HTMLDivElement>('hud-left');
  private readonly modelHudTag = requireElement<HTMLParagraphElement>('model-hud-tag');
  private readonly infoNameEn = requireElement<HTMLSpanElement>('info-name-en');
  private readonly infoNameCn = requireElement<HTMLSpanElement>('info-name-cn');
  private readonly infoPurpose = requireElement<HTMLParagraphElement>('info-purpose');
  private readonly infoOutput = requireElement<HTMLParagraphElement>('info-output');
  private readonly infoPrinciple = requireElement<HTMLParagraphElement>('info-principle');
  private readonly modelRail = requireElement<HTMLElement>('model-rail');
  private readonly landmarksToggle = requireElement<HTMLButtonElement>('landmarks-toggle');
  private readonly landmarksLabel = requireElement<HTMLSpanElement>('landmarks-toggle-label');
  private readonly landmarksHint = requireElement<HTMLSpanElement>('landmarks-toggle-hint');
  private readonly reactionDemoToggle =
    requireElement<HTMLButtonElement>('reaction-demo-toggle');
  private readonly reactionDemoToggleLabel =
    requireElement<HTMLSpanElement>('reaction-demo-toggle-label');
  private readonly reactionDemoToggleHint =
    requireElement<HTMLSpanElement>('reaction-demo-toggle-hint');
  private readonly uiToggle = requireElement<HTMLButtonElement>('ui-toggle');
  private readonly uiToggleLabel = requireElement<HTMLSpanElement>('ui-toggle-label');
  private readonly uiToggleHint = requireElement<HTMLSpanElement>('ui-toggle-hint');
  private readonly errorBanner = requireElement<HTMLDivElement>('error-banner');
  private readonly errorBannerText = requireElement<HTMLParagraphElement>('error-banner-text');
  private readonly errorBannerRetry = requireElement<HTMLButtonElement>('error-banner-retry');

  // Runtime
  private readonly sfx = new UiHoldSfx();
  private readonly engine = new InteractionEngine();
  private readonly demoModels = new DemoModelController();
  private readonly overlay = new OverlayRenderer(this.overlayCanvas, this.video);
  private readonly facePoseAdapter = new FacePoseAdapter();
  private readonly faceReaction = new FaceReactionController(this.faceReactionElement);
  private readonly gestureProcessor = new HandTrackingResultProcessor();
  private readonly effects: EffectsLayer3d | null;
  private readonly gestureEffects: GestureEffectsController | null;
  private readonly modelCards = new Map<DemoModelId, HTMLButtonElement>();
  /** 可随“隐藏界面”折叠的卡片（模型卡 + 关键点/表情演示开关）。 */
  private readonly holdButtons: UiHoldButton[] = [];
  /** 界面折叠开关本身，永远常驻可用。 */
  private uiToggleHoldButton!: UiHoldButton;
  private helmetRenderer: HelmetRenderer | null = null;

  private phase: Phase = 'intro';
  private stream: MediaStream | null = null;
  private activeModel: DemoModelId = 'hand_landmarker';
  private pendingModel: DemoModelId | null = null;
  private overlayVisible = true;
  private uiCollapsed = false;
  private reactionDemoEnabled = false;
  private helmetFaceModeActive = false;
  private lastInjectedSeq = 0;
  private engineErrorShown = false;
  private retryAction: (() => void) | null = null;
  private animationFrameId = 0;
  private disposed = false;

  constructor() {
    for (const id of MODEL_ORDER) {
      const card = requireElement<HTMLButtonElement>(`model-card-${id}`);
      this.modelCards.set(id, card);
      this.holdButtons.push(
        new UiHoldButton(card, this.stageOverlay, this.sfx, {
          onTrigger: () => this.requestModelSwitch(id),
        }),
      );
    }

    this.holdButtons.push(
      new UiHoldButton(this.landmarksToggle, this.stageOverlay, this.sfx, {
        onTrigger: () => this.toggleLandmarks(),
      }),
      new UiHoldButton(this.reactionDemoToggle, this.stageOverlay, this.sfx, {
        onTrigger: () => this.toggleReactionDemo(),
      }),
    );

    this.uiToggleHoldButton = new UiHoldButton(
      this.uiToggle,
      this.stageOverlay,
      this.sfx,
      { onTrigger: () => this.toggleUiCollapsed() },
    );

    this.startButton.addEventListener('click', () => {
      void this.start();
    });
    this.errorBannerRetry.addEventListener('click', () => {
      this.hideError();
      this.retryAction?.();
    });
    window.addEventListener('beforeunload', () => this.dispose());

    try {
      this.effects = new EffectsLayer3d(this.effectsCanvas);
      this.gestureEffects = new GestureEffectsController(this.effects);
    } catch (error) {
      this.effects = null;
      this.gestureEffects = null;
      this.effectsCanvas.hidden = true;
      this.showError(
        `three.js 手势特效初始化失败（${toMessage(error)}）。手势识别与其他演示仍可继续使用。`,
        null,
      );
    }

    try {
      this.helmetRenderer = new HelmetRenderer(this.helmetCanvas);
    } catch (error) {
      this.helmetCanvas.hidden = true;
      this.showError(
        `3D 头盔初始化失败（${toMessage(error)}）。其他 MediaPipe 演示仍可继续使用。`,
        null,
      );
    }

    this.applyModelUi();
    this.animationFrameId = requestAnimationFrame(this.loop);
  }

  private async start(): Promise<void> {
    if (this.phase !== 'intro') {
      return;
    }

    this.phase = 'starting';
    this.startButton.disabled = true;
    this.statusText.textContent = '正在请求摄像头权限…';
    this.sfx.unlock();

    const result = await startCamera(this.video);

    if (!result.ok) {
      this.phase = 'intro';
      this.startButton.disabled = false;
      this.statusText.textContent = result.message;
      return;
    }

    this.stream = result.stream;
    this.phase = 'loading';
    this.introPanel.hidden = true;
    this.loadingPanel.hidden = false;
    this.loadingPanel.setAttribute('aria-busy', 'true');

    try {
      await this.engine.initialize();
    } catch (error) {
      this.loadingPanel.classList.add('is-error');
      this.loadingPanel.setAttribute('aria-busy', 'false');
      this.loadingStage.textContent = '手势交互引擎加载失败';
      this.loadingDetail.textContent = `${toMessage(error)}。请确认 public/mediapipe 资源完整后刷新页面。`;
      this.showError('手势交互引擎加载失败，无法继续。', () => window.location.reload());
      return;
    }

    this.loadingPanel.hidden = true;
    this.loadingPanel.setAttribute('aria-busy', 'false');
    this.hudLeft.hidden = false;
    this.modelRail.hidden = false;
    this.overlay.setVisible(this.overlayVisible);
    this.phase = 'running';
  }

  private readonly loop = (timestamp: number): void => {
    if (this.disposed) {
      return;
    }

    if (this.phase === 'running') {
      this.tick(timestamp);
    }

    this.animationFrameId = requestAnimationFrame(this.loop);
  };

  private tick(timestamp: number): void {
    const layout = this.computeLayout();
    const gestureMode = this.activeModel === 'gesture_recognizer';
    const demo = this.demoModels.update(this.video, timestamp);

    this.engine.setSuspended(gestureMode);

    if (!gestureMode) {
      this.engine.update(this.video, timestamp);
    } else {
      const seq = this.demoModels.getDetectionSeq();

      if (demo?.kind === 'gesture_recognizer' && seq !== this.lastInjectedSeq) {
        this.lastInjectedSeq = seq;
        this.engine.injectHands(
          this.gestureProcessor.process(gestureResultToRawHands(demo.result), timestamp),
        );
      }
    }

    const snapshot = this.engine.getSnapshot();

    if (snapshot.status === 'error') {
      this.handleEngineError(snapshot.error);
    }

    const hands = snapshot.hands.hands;

    // 折叠时可隐藏卡停用（避免指尖扫过隐藏卡误触发）；界面开关卡永远可用。
    for (const holdButton of this.holdButtons) {
      holdButton.update({ hands, layout, timestamp, active: !this.uiCollapsed });
    }
    this.uiToggleHoldButton.update({ hands, layout, timestamp, active: true });

    this.overlay.draw(this.buildOverlayContent(demo, snapshot));

    const effectsEnabled = gestureMode && this.pendingModel === null;
    this.effects?.setActive(effectsEnabled);

    if (effectsEnabled && demo?.kind === 'gesture_recognizer') {
      this.gestureEffects?.update(
        demo.result,
        layout,
        timestamp,
        this.demoModels.getDetectionSeq(),
      );
    } else {
      this.gestureEffects?.reset();
    }
    this.effects?.step(timestamp);

    const faceReactionEnabled =
      this.activeModel === 'face_landmarker'
      && this.pendingModel === null
      && this.reactionDemoEnabled;
    this.faceReaction.setActive(faceReactionEnabled);

    if (this.activeModel === 'face_landmarker') {
      const faceResult = demo?.kind === 'face_landmarker' ? demo.result : null;
      this.faceReaction.update(
        faceResult,
        layout,
        timestamp,
        this.demoModels.getDetectionSeq(),
      );

      if (this.helmetRenderer) {
        const facePose = this.facePoseAdapter.update(faceResult, layout, timestamp);
        this.helmetRenderer.update(facePose, layout, timestamp);
      }
    }
  }

  // ---- 模型切换 ----

  private requestModelSwitch(id: DemoModelId): void {
    if (id === this.activeModel || this.pendingModel !== null) {
      return;
    }

    if (this.activeModel === 'gesture_recognizer') {
      this.gestureEffects?.reset();
      this.effects?.setActive(false);
    }

    if (this.activeModel === 'face_landmarker') {
      this.reactionDemoEnabled = false;
      this.reactionDemoToggle.hidden = true;
      this.faceReaction.setActive(false);
      this.updateReactionDemoUi();
    }

    this.pendingModel = id;
    const card = this.modelCards.get(id);
    card?.classList.add('is-loading');
    this.setCardHint(id, 'LOADING');

    void this.demoModels
      .switchTo(id)
      .then(() => {
        if (this.pendingModel !== id) {
          return;
        }

        this.pendingModel = null;
        this.activeModel = id;
        card?.classList.remove('is-loading');
        this.gestureProcessor.reset();
        this.lastInjectedSeq = this.demoModels.getDetectionSeq();
        this.applyModelUi();
      })
      .catch((error: unknown) => {
        if (this.pendingModel === id) {
          this.pendingModel = null;
        }

        card?.classList.remove('is-loading');
        this.applyModelUi();
        this.showError(
          `${MODEL_INFO[id].nameCn}模型加载失败（${toMessage(error)}）。请检查 public/mediapipe/models 下的模型文件，或重试。`,
          () => this.requestModelSwitch(id),
        );
      });
  }

  private applyModelUi(): void {
    const activeInfo = MODEL_INFO[this.activeModel];

    for (const id of MODEL_ORDER) {
      const card = this.modelCards.get(id);

      if (!card) {
        continue;
      }

      const isActive = id === this.activeModel;
      card.classList.toggle('is-active', isActive);

      if (this.pendingModel !== id) {
        this.setCardHint(id, isActive ? 'ACTIVE' : 'MODEL');
      }

      const info = MODEL_INFO[id];
      card.setAttribute(
        'aria-label',
        isActive ? `当前演示中：${info.nameCn}` : `长按演示${info.nameCn}`,
      );
    }

    this.modelHudTag.textContent = `MEDIAPIPE VISION // ${activeInfo.code}`;
    this.infoNameEn.textContent = activeInfo.nameEn;
    this.infoNameCn.textContent = activeInfo.nameCn;
    this.infoPurpose.textContent = activeInfo.purpose;
    this.infoOutput.textContent = activeInfo.output;
    this.infoPrinciple.textContent = activeInfo.principle;

    const faceMode = this.activeModel === 'face_landmarker';

    const effectsActive =
      this.phase === 'running'
      && this.activeModel === 'gesture_recognizer'
      && this.pendingModel === null;
    this.effects?.setActive(effectsActive);

    if (!effectsActive) {
      this.gestureEffects?.reset();
    }

    if (faceMode !== this.helmetFaceModeActive) {
      this.helmetFaceModeActive = faceMode;
      this.reactionDemoEnabled = false;
      this.facePoseAdapter.reset();
      this.helmetRenderer?.setFaceMode(faceMode);
      this.helmetRenderer?.setEquipped(false);
    }

    this.reactionDemoToggle.hidden = !faceMode;
    this.updateReactionDemoUi();
    this.faceReaction.setActive(
      faceMode && this.pendingModel === null && this.reactionDemoEnabled,
    );
  }

  private setCardHint(id: DemoModelId, prefix: 'MODEL' | 'ACTIVE' | 'LOADING'): void {
    const card = this.modelCards.get(id);
    const hint = card?.querySelector('.planet-focus-action-card-hint');

    if (hint) {
      hint.textContent = `${prefix} · ${MODEL_INFO[id].code}`;
    }
  }

  // ---- 表情演示开关 ----

  private toggleReactionDemo(): void {
    if (this.activeModel !== 'face_landmarker' || this.pendingModel !== null) {
      return;
    }

    this.reactionDemoEnabled = !this.reactionDemoEnabled;
    this.faceReaction.setActive(this.reactionDemoEnabled);
    this.updateReactionDemoUi();
  }

  private updateReactionDemoUi(): void {
    const action = this.reactionDemoEnabled ? '关闭' : '开启';
    this.reactionDemoToggleLabel.textContent = `长按${action}表情演示`;
    this.reactionDemoToggleHint.textContent =
      `REACTIONS · ${this.reactionDemoEnabled ? 'ON' : 'OFF'}`;
    this.reactionDemoToggle.setAttribute('aria-label', `长按${action}表情演示`);
    this.reactionDemoToggle.setAttribute(
      'aria-pressed',
      this.reactionDemoEnabled ? 'true' : 'false',
    );
    this.reactionDemoToggle.classList.toggle('is-active', this.reactionDemoEnabled);
  }

  // ---- 关键点叠加开关 ----

  private toggleLandmarks(): void {
    this.overlayVisible = !this.overlayVisible;
    this.overlay.setVisible(this.overlayVisible);

    const action = this.overlayVisible ? '隐藏' : '显示';
    this.landmarksLabel.textContent = `长按${action}捕捉关键点`;
    this.landmarksHint.textContent = `LANDMARKS · ${this.overlayVisible ? 'ON' : 'OFF'}`;
    this.landmarksToggle.setAttribute('aria-label', `长按${action}捕捉关键点`);
    this.landmarksToggle.setAttribute('aria-pressed', this.overlayVisible ? 'true' : 'false');
  }

  // ---- 界面折叠开关 ----

  private toggleUiCollapsed(): void {
    this.uiCollapsed = !this.uiCollapsed;
    this.stageOverlay.classList.toggle('ui-collapsed', this.uiCollapsed);

    const action = this.uiCollapsed ? '展示' : '隐藏';
    this.uiToggleLabel.textContent = `长按${action}界面`;
    this.uiToggleHint.textContent = `INTERFACE · ${this.uiCollapsed ? 'OFF' : 'ON'}`;
    this.uiToggle.setAttribute('aria-label', `长按${action}界面`);
    this.uiToggle.setAttribute('aria-pressed', this.uiCollapsed ? 'true' : 'false');
  }

  // ---- 叠加内容 ----

  private buildOverlayContent(
    demo: DemoDetection | null,
    snapshot: EngineSnapshot,
  ): OverlayContent | null {
    switch (this.activeModel) {
      case 'hand_landmarker':
        return { kind: 'hands', hands: snapshot.hands.hands };
      case 'gesture_recognizer':
        return demo?.kind === 'gesture_recognizer'
          ? { kind: 'gesture_recognizer', result: demo.result }
          : null;
      case 'face_detector':
        return demo?.kind === 'face_detector'
          ? { kind: 'face_detector', result: demo.result }
          : null;
      case 'face_landmarker':
        return demo?.kind === 'face_landmarker'
          ? { kind: 'face_landmarker', result: demo.result }
          : null;
    }
  }

  // ---- 错误处理 ----

  private handleEngineError(message: string | null): void {
    if (this.engineErrorShown) {
      return;
    }

    this.engineErrorShown = true;
    this.showError(
      `手势交互引擎出错（${message ?? '未知错误'}）。卡片仍可用鼠标长按操作；刷新页面可恢复手势交互。`,
      () => window.location.reload(),
    );
  }

  private showError(message: string, retry: (() => void) | null): void {
    this.errorBannerText.textContent = message;
    this.retryAction = retry;
    this.errorBannerRetry.hidden = retry === null;
    this.errorBanner.hidden = false;
  }

  private hideError(): void {
    this.errorBanner.hidden = true;
  }

  // ---- 基础设施 ----

  private computeLayout(): PreviewLayout {
    const bounds = this.stageOverlay.getBoundingClientRect();

    return {
      stageWidth: bounds.width,
      stageHeight: bounds.height,
      sourceWidth: this.video.videoWidth,
      sourceHeight: this.video.videoHeight,
      mirrored: true,
    };
  }

  private dispose(): void {
    if (this.disposed) {
      return;
    }

    this.disposed = true;
    cancelAnimationFrame(this.animationFrameId);
    this.engine.dispose();
    this.demoModels.dispose();
    this.gestureEffects?.reset();
    this.effects?.dispose();
    this.faceReaction.dispose();
    this.helmetRenderer?.dispose();
    this.helmetRenderer = null;
    stopCamera(this.stream);
    this.stream = null;
  }
}

function requireElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);

  if (!element) {
    throw new Error(`Required element #${id} is missing.`);
  }

  return element as T;
}

function toMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
