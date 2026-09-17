import {
  ACESFilmicToneMapping,
  AdditiveBlending,
  BackSide,
  BoxGeometry,
  BufferAttribute,
  BufferGeometry,
  CanvasTexture,
  Color,
  CylinderGeometry,
  DoubleSide,
  DynamicDrawUsage,
  EdgesGeometry,
  FrontSide,
  Group,
  LineBasicMaterial,
  LineSegments,
  LinearFilter,
  Mesh,
  MeshBasicMaterial,
  OctahedronGeometry,
  OrthographicCamera,
  PlaneGeometry,
  Points,
  RingGeometry,
  Scene,
  ShaderMaterial,
  Sprite,
  SpriteMaterial,
  SRGBColorSpace,
  Vector2,
  WebGLRenderer,
} from 'three';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';

/**
 * 关键：UnrealBloomPass 作为最后一个 pass 时，会把底图当作不透明全屏 quad 重绘（alpha=1），
 * 导致整块 WebGL 画布不透明、盖住摄像头 → 黑屏。
 * 追加这个最终 pass：按亮度重建 alpha（暗处透明、亮处可见），既修透明又把 bloom 推入其“非末尾”透明分支。
 */
const AlphaFromLuminanceShader = {
  uniforms: {
    tDiffuse: { value: null },
    uAlphaBoost: { value: 1.05 },
  },
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
    }
  `,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform float uAlphaBoost;
    varying vec2 vUv;
    void main() {
      vec4 texel = texture2D( tDiffuse, vUv );
      float lum = max( texel.r, max( texel.g, texel.b ) );
      float alpha = clamp( max( texel.a, lum * uAlphaBoost ), 0.0, 1.0 );
      gl_FragColor = vec4( texel.rgb, alpha );
    }
  `,
};

const MAX_PARTICLES = 900;
const MAX_SHOCKWAVES = 24;
const MAX_BEAMS = 18;
const MAX_TEXTS = 16;
const PARTICLE_Z = 4;
const PRIMITIVE_Z = 3;
const LIGHTNING_BOLT_COUNT = 7;
const LIGHTNING_SEGMENTS = 12;
const CUBE_DISMISS_SECONDS = 0.38;

type ParticleShape = 0 | 1 | 2;

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
  readonly maxLife: number;
  readonly size: number;
  readonly color: Color;
  readonly gravity: number;
  readonly drag: number;
  rotation: number;
  readonly spin: number;
  readonly shape: ParticleShape;
  readonly targetX: number | null;
  readonly targetY: number | null;
  readonly attraction: number;
}

interface ShockwaveRecord {
  readonly mesh: Mesh<RingGeometry, MeshBasicMaterial>;
  life: number;
  readonly maxLife: number;
  readonly maxRadius: number;
}

interface BeamRecord {
  readonly mesh: Mesh<PlaneGeometry, ShaderMaterial>;
  life: number;
  readonly maxLife: number;
}

interface TextRecord {
  readonly sprite: Sprite;
  readonly material: SpriteMaterial;
  readonly texture: CanvasTexture;
  life: number;
  readonly maxLife: number;
  readonly velocityY: number;
}

interface ChargeOrbRecord {
  readonly sprite: Sprite;
  readonly spriteMaterial: SpriteMaterial;
  readonly ring: Mesh<RingGeometry, MeshBasicMaterial>;
  lastUpdatedAt: number;
}

type CosmicCubePhase = 'charging' | 'revealing' | 'active' | 'dismissing';
type CosmicCubeInteractionMode = 'floating' | 'targeting' | 'grabbed' | 'dismissing';

interface ScreenPoint {
  readonly x: number;
  readonly y: number;
}

export interface CosmicCubeInteractionFeedback {
  readonly mode: Exclude<CosmicCubeInteractionMode, 'dismissing'>;
  readonly targetA?: ScreenPoint;
  readonly targetB?: ScreenPoint;
  readonly fingerA?: ScreenPoint;
  readonly fingerB?: ScreenPoint;
  readonly hoverA?: number;
  readonly hoverB?: number;
  readonly motionEnergy?: number;
}

interface LightningStroke {
  readonly core: Line2;
  readonly glow: Line2;
  readonly coreMaterial: LineMaterial;
  readonly glowMaterial: LineMaterial;
}

interface CosmicCubeRecord {
  readonly group: Group;
  readonly cubeRoot: Group;
  readonly shell: Mesh<BoxGeometry, ShaderMaterial>;
  readonly backShell: Mesh<BoxGeometry, ShaderMaterial>;
  readonly innerCube: Mesh<BoxGeometry, MeshBasicMaterial>;
  readonly innerCage: Mesh<BoxGeometry, MeshBasicMaterial>;
  readonly energyCore: Mesh<OctahedronGeometry, ShaderMaterial>;
  readonly energyCoreWire: Mesh<OctahedronGeometry, MeshBasicMaterial>;
  readonly coreRays: LineSegments<BufferGeometry, LineBasicMaterial>;
  readonly cubeEdges: LineSegments<EdgesGeometry, LineBasicMaterial>;
  readonly edgeBeams: Array<Mesh<CylinderGeometry, MeshBasicMaterial>>;
  readonly cornerNodes: Array<Mesh<OctahedronGeometry, MeshBasicMaterial>>;
  readonly chargeFrames: Array<LineSegments<EdgesGeometry, LineBasicMaterial>>;
  readonly orbitRings: Array<Mesh<RingGeometry, MeshBasicMaterial>>;
  readonly core: Sprite;
  readonly coreMaterial: SpriteMaterial;
  readonly gripHandles: Array<Mesh<RingGeometry, MeshBasicMaterial>>;
  readonly lightningBolts: LightningStroke[];
  readonly tetherBolts: LightningStroke[];
  phase: CosmicCubePhase;
  interactionMode: CosmicCubeInteractionMode;
  progress: number;
  revealProgress: number;
  targetX: number;
  targetY: number;
  targetScale: number;
  currentX: number;
  currentY: number;
  currentScale: number;
  gripTargetA: ScreenPoint | null;
  gripTargetB: ScreenPoint | null;
  fingerA: ScreenPoint | null;
  fingerB: ScreenPoint | null;
  hoverA: number;
  hoverB: number;
  motionEnergy: number;
  lightningStrength: number;
  handleStrength: number;
  lastLightningAt: number;
  dismissStartedAt: number | null;
  lastUpdatedAt: number;
}

/**
 * Gesture Recognizer 专用透明 three.js 特效层。
 * 相机直接使用舞台 CSS 像素坐标，因此控制器无需了解 three.js 世界坐标。
 */
export class EffectsLayer3d {
  private readonly scene = new Scene();
  private readonly camera = new OrthographicCamera(0, 1, 0, 1, 0.1, 1000);
  private readonly renderer: WebGLRenderer;
  private readonly composer: EffectComposer;
  private readonly bloomPass: UnrealBloomPass;
  private readonly alphaPass: ShaderPass;
  private readonly radialTexture = createRadialTexture();
  private readonly ringGeometry = new RingGeometry(0.86, 1, 96);
  private readonly beamGeometry = new PlaneGeometry(1, 1);
  private readonly cubeGeometry = new BoxGeometry(1, 1, 1, 3, 3, 3);
  private readonly cubeEdgesGeometry = new EdgesGeometry(this.cubeGeometry);
  private readonly edgeBeamGeometry = new CylinderGeometry(0.014, 0.014, 1.025, 8, 1);
  private readonly energyNodeGeometry = new OctahedronGeometry(0.055, 0);
  private readonly energyCoreGeometry = new OctahedronGeometry(1, 2);
  private readonly coreRayGeometry = createCoreRayGeometry();
  private readonly squareEdgesGeometry = createSquareEdgesGeometry();
  private readonly particleGeometry = new BufferGeometry();
  private readonly particleMaterial: ShaderMaterial;
  private readonly particlePoints: Points<BufferGeometry, ShaderMaterial>;
  private readonly positions = new Float32Array(MAX_PARTICLES * 3);
  private readonly colors = new Float32Array(MAX_PARTICLES * 3);
  private readonly sizes = new Float32Array(MAX_PARTICLES);
  private readonly alphas = new Float32Array(MAX_PARTICLES);
  private readonly rotations = new Float32Array(MAX_PARTICLES);
  private readonly shapes = new Float32Array(MAX_PARTICLES);
  private readonly particles: Particle[] = [];
  private readonly shockwaves: ShockwaveRecord[] = [];
  private readonly beams: BeamRecord[] = [];
  private readonly texts: TextRecord[] = [];
  private readonly chargeOrbs = new Map<string, ChargeOrbRecord>();
  private readonly cosmicCubes = new Map<string, CosmicCubeRecord>();
  private active = false;
  private width = 1;
  private height = 1;
  private pixelRatio = 1;
  private lastStepAt: number | null = null;

  constructor(private readonly canvas: HTMLCanvasElement) {
    this.renderer = new WebGLRenderer({
      canvas,
      alpha: true,
      antialias: true,
      premultipliedAlpha: false,
      powerPreference: 'high-performance',
    });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.outputColorSpace = SRGBColorSpace;
    this.renderer.toneMapping = ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 0.98;

    this.particleMaterial = createParticleMaterial(this.radialTexture);
    this.particlePoints = new Points(this.particleGeometry, this.particleMaterial);
    this.particlePoints.frustumCulled = false;
    this.scene.add(this.particlePoints);
    this.configureParticleGeometry();

    const renderPass = new RenderPass(this.scene, this.camera);
    renderPass.clearAlpha = 0;
    // 只让核心、边缘与雷电等高亮区域进入 bloom，避免整个透明外壳发糊。
    this.bloomPass = new UnrealBloomPass(new Vector2(1, 1), 0.76, 0.14, 0.5);
    this.alphaPass = new ShaderPass(AlphaFromLuminanceShader);
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(renderPass);
    this.composer.addPass(this.bloomPass);
    // 最后一个 pass：恢复透明度，避免 bloom 输出不透明黑底盖住摄像头。
    this.composer.addPass(this.alphaPass);
  }

  setActive(active: boolean): void {
    if (this.active === active) {
      return;
    }

    this.active = active;
    this.canvas.hidden = !active;
    this.lastStepAt = null;

    if (!active) {
      this.clear();
    }
  }

  shockwave(x: number, y: number, color: string, maxRadius: number): void {
    const material = new MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 1,
      blending: AdditiveBlending,
      depthTest: false,
      depthWrite: false,
      side: DoubleSide,
    });
    const mesh = new Mesh(this.ringGeometry, material);
    mesh.position.set(x, y, PRIMITIVE_Z);
    mesh.scale.setScalar(Math.max(4, maxRadius * 0.08));
    this.scene.add(mesh);
    this.shockwaves.push({ mesh, life: 0.58, maxLife: 0.58, maxRadius });
    this.trimShockwaves();
    this.sparkBurst(x, y, color, 18, Math.max(90, maxRadius * 2.4), maxRadius * 0.045);
  }

  sparkBurst(
    x: number,
    y: number,
    color: string,
    count: number,
    speed: number,
    particleSize: number,
  ): void {
    const safeCount = Math.max(0, Math.round(count));

    for (let index = 0; index < safeCount; index += 1) {
      const angle = Math.random() * Math.PI * 2;
      const velocity = speed * (0.35 + Math.random() * 0.65);
      this.addParticle({
        x,
        y,
        vx: Math.cos(angle) * velocity,
        vy: Math.sin(angle) * velocity,
        life: 0.45 + Math.random() * 0.55,
        size: Math.max(2.5, particleSize * (0.55 + Math.random() * 0.8)),
        color,
        gravity: 70,
        drag: 0.965,
        shape: 0,
      });
    }
  }

  beam(
    x: number,
    y: number,
    dirX: number,
    dirY: number,
    color: string,
    length: number,
    width: number,
  ): void {
    const directionLength = Math.hypot(dirX, dirY);

    if (directionLength < 0.001) {
      return;
    }

    const nx = dirX / directionLength;
    const ny = dirY / directionLength;
    const material = createBeamMaterial(color);
    const mesh = new Mesh(this.beamGeometry, material);
    mesh.position.set(x + nx * length * 0.5, y + ny * length * 0.5, PRIMITIVE_Z);
    mesh.scale.set(length, Math.max(3, width), 1);
    mesh.rotation.z = Math.atan2(ny, nx);
    this.scene.add(mesh);
    this.beams.push({ mesh, life: 0.12, maxLife: 0.12 });
    this.trimBeams();

    if (Math.random() > 0.35) {
      const distance = Math.random() * length;
      this.addParticle({
        x: x + nx * distance,
        y: y + ny * distance,
        vx: -ny * (20 + Math.random() * 35),
        vy: -55 - Math.random() * 45,
        life: 0.32 + Math.random() * 0.2,
        size: Math.max(3, width * 0.75),
        color,
        gravity: -15,
        drag: 0.96,
        shape: 0,
      });
    }
  }

  confetti(x: number, y: number, palmScale: number): void {
    const palette = ['#7dd3fc', '#f6c177', '#a9f3df', '#f7a8c4'];
    const count = Math.round(clamp(palmScale * 0.42, 22, 42));

    for (let index = 0; index < count; index += 1) {
      const angle = -Math.PI * (0.12 + Math.random() * 0.76);
      const speed = palmScale * (2.2 + Math.random() * 3.1);
      this.addParticle({
        x,
        y,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        life: 0.9 + Math.random() * 0.7,
        size: clamp(palmScale * (0.07 + Math.random() * 0.06), 4, 11),
        color: palette[index % palette.length] ?? '#ffffff',
        gravity: 260,
        drag: 0.985,
        rotation: Math.random() * Math.PI,
        spin: (Math.random() - 0.5) * 8,
        shape: 1,
      });
    }
  }

  floatText(
    x: number,
    y: number,
    text: string,
    color: string,
    fontSize: number,
    direction: 'up' | 'down' = 'up',
  ): void {
    const label = createTextTexture(text, color, fontSize);
    const material = new SpriteMaterial({
      map: label.texture,
      transparent: true,
      opacity: 1,
      depthTest: false,
      depthWrite: false,
    });
    const sprite = new Sprite(material);
    const displayScale = 0.5;
    sprite.position.set(x, y, 8);
    sprite.scale.set(label.width * displayScale, label.height * displayScale, 1);
    this.scene.add(sprite);
    this.texts.push({
      sprite,
      material,
      texture: label.texture,
      life: 1.1,
      maxLife: 1.1,
      velocityY: direction === 'up' ? -48 : 48,
    });
    this.trimTexts();
  }

  hearts(x: number, y: number, color: string, palmScale: number): void {
    const spread = palmScale * 0.35;
    this.addParticle({
      x: x + (Math.random() - 0.5) * spread,
      y: y + (Math.random() - 0.5) * palmScale * 0.15,
      vx: (Math.random() - 0.5) * palmScale * 0.3,
      vy: -palmScale * (0.8 + Math.random() * 0.55),
      life: 0.9 + Math.random() * 0.55,
      size: clamp(palmScale * (0.2 + Math.random() * 0.08), 12, 34),
      color,
      gravity: -8,
      drag: 0.982,
      rotation: (Math.random() - 0.5) * 0.35,
      spin: (Math.random() - 0.5) * 0.45,
      shape: 2,
    });
  }

  chargeOrb(
    key: string,
    x: number,
    y: number,
    progress: number,
    color: string,
    palmScale: number,
    compressed: boolean,
    timestamp: number,
  ): void {
    let orb = this.chargeOrbs.get(key);

    if (!orb) {
      const spriteMaterial = new SpriteMaterial({
        map: this.radialTexture,
        color,
        transparent: true,
        opacity: 0.85,
        blending: AdditiveBlending,
        depthTest: false,
        depthWrite: false,
      });
      const sprite = new Sprite(spriteMaterial);
      const ringMaterial = new MeshBasicMaterial({
        color,
        transparent: true,
        opacity: 0.9,
        blending: AdditiveBlending,
        depthTest: false,
        depthWrite: false,
        side: DoubleSide,
      });
      const ring = new Mesh(this.ringGeometry, ringMaterial);
      sprite.position.z = 5;
      ring.position.z = 6;
      this.scene.add(sprite, ring);
      orb = { sprite, spriteMaterial, ring, lastUpdatedAt: timestamp };
      this.chargeOrbs.set(key, orb);
    }

    const safeProgress = clamp(progress, 0, 1);
    const baseSize = palmScale * (compressed ? 0.32 : 0.28 + safeProgress * 0.48);
    orb.sprite.position.set(x, y, 5);
    orb.sprite.scale.setScalar(Math.max(15, baseSize * 2.25));
    orb.spriteMaterial.color.set(color);
    orb.spriteMaterial.opacity = compressed ? 1 : 0.55 + safeProgress * 0.35;
    orb.ring.position.set(x, y, 6);
    orb.ring.scale.setScalar(Math.max(8, baseSize * (compressed ? 0.72 : 1.15)));
    orb.ring.rotation.z += compressed ? 0.18 : 0.05;
    orb.ring.material.color.set(color);
    orb.ring.material.opacity = compressed ? 1 : 0.35 + safeProgress * 0.55;
    orb.lastUpdatedAt = timestamp;

    const angle = Math.random() * Math.PI * 2;
    const distance = palmScale * (0.7 + Math.random() * 0.6);
    const startX = x + Math.cos(angle) * distance;
    const startY = y + Math.sin(angle) * distance;
    this.addParticle({
      x: startX,
      y: startY,
      vx: (x - startX) * 1.5,
      vy: (y - startY) * 1.5,
      life: 0.42 + Math.random() * 0.25,
      size: clamp(palmScale * 0.07, 3, 9),
      color,
      gravity: 0,
      drag: 0.98,
      shape: 0,
      targetX: x,
      targetY: y,
      attraction: 7.5,
    });
  }

  clearChargeOrb(key: string): void {
    const orb = this.chargeOrbs.get(key);

    if (!orb) {
      return;
    }

    this.scene.remove(orb.sprite, orb.ring);
    orb.spriteMaterial.dispose();
    orb.ring.material.dispose();
    this.chargeOrbs.delete(key);
  }

  updateCubeCharge(
    key: string,
    x: number,
    y: number,
    progress: number,
    palmScale: number,
    timestamp: number,
  ): void {
    const cube = this.ensureCosmicCube(key, x, y, palmScale, timestamp);
    cube.phase = 'charging';
    cube.progress = clamp(progress, 0, 1);
    cube.targetX = x;
    cube.targetY = y;
    cube.targetScale = palmScale * 0.78;
    cube.interactionMode = 'floating';
    cube.lastUpdatedAt = timestamp;

    const particleCount = cube.progress > 0.72 ? 2 : 1;

    for (let index = 0; index < particleCount; index += 1) {
      const angle = Math.random() * Math.PI * 2;
      const distance = palmScale * (0.72 + Math.random() * 0.72);
      const startX = x + Math.cos(angle) * distance;
      const startY = y + Math.sin(angle) * distance;
      this.addParticle({
        x: startX,
        y: startY,
        vx: (x - startX) * (0.5 + cube.progress * 0.9),
        vy: (y - startY) * (0.5 + cube.progress * 0.9),
        life: 0.42 + Math.random() * 0.28,
        size: clamp(palmScale * (0.045 + cube.progress * 0.035), 3, 9),
        color: Math.random() > 0.22 ? '#63d8ff' : '#ffffff',
        gravity: 0,
        drag: 0.985,
        shape: 0,
        targetX: x,
        targetY: y,
        attraction: 5 + cube.progress * 5,
      });
    }
  }

  revealCosmicCube(
    key: string,
    x: number,
    y: number,
    palmScale: number,
    timestamp: number,
  ): void {
    const cube = this.ensureCosmicCube(key, x, y, palmScale, timestamp);
    cube.phase = 'revealing';
    cube.progress = 1;
    cube.revealProgress = 0;
    cube.targetX = x;
    cube.targetY = y;
    cube.targetScale = palmScale * 0.78;
    cube.lastUpdatedAt = timestamp;
    this.sparkBurst(x, y, '#7dd3fc', 12, palmScale * 2.6, palmScale * 0.052);
    this.sparkBurst(x, y, '#ffffff', 3, palmScale * 1.8, palmScale * 0.04);
  }

  updateCosmicCubePose(
    key: string,
    x: number,
    y: number,
    scale: number,
    timestamp: number,
    feedback: CosmicCubeInteractionFeedback,
  ): void {
    const palmScale = Math.max(18, scale / 0.78);
    const cube = this.ensureCosmicCube(key, x, y, palmScale, timestamp);
    const wasGrabbed = cube.interactionMode === 'grabbed';
    cube.targetX = x;
    cube.targetY = y;
    cube.targetScale = scale;
    cube.interactionMode = feedback.mode;
    cube.gripTargetA = feedback.targetA ?? cube.gripTargetA;
    cube.gripTargetB = feedback.targetB ?? cube.gripTargetB;
    cube.fingerA = feedback.fingerA ?? cube.fingerA;
    cube.fingerB = feedback.fingerB ?? cube.fingerB;
    cube.hoverA = feedback.hoverA ?? 0;
    cube.hoverB = feedback.hoverB ?? 0;
    cube.motionEnergy = clamp(feedback.motionEnergy ?? 0, 0, 1);
    cube.lastUpdatedAt = timestamp;

    if (cube.phase === 'charging') {
      cube.phase = 'revealing';
      cube.progress = 1;
      cube.revealProgress = 0;
    }

    if (!wasGrabbed && feedback.mode === 'grabbed') {
      const pointA = feedback.fingerA ?? { x, y };
      const pointB = feedback.fingerB ?? { x, y };
      this.sparkBurst(pointA.x, pointA.y, '#dffbff', 5, scale * 0.72, scale * 0.035);
      this.sparkBurst(pointB.x, pointB.y, '#7dd3fc', 5, scale * 0.72, scale * 0.035);
      this.sparkBurst(x, y, '#63d8ff', 4, scale * 0.9, scale * 0.03);
    }

    if (cube.phase === 'active' && Math.random() < 0.08) {
      const radius = scale * 0.58;
      const angle = Math.random() * Math.PI * 2;
      const direction = Math.random() > 0.5 ? 1 : -1;
      const orbitalSpeed = scale * (0.52 + Math.random() * 0.36) * direction;
      this.addParticle({
        x: x + Math.cos(angle) * radius,
        y: y + Math.sin(angle) * radius * 0.72,
        vx: -Math.sin(angle) * orbitalSpeed,
        vy: Math.cos(angle) * orbitalSpeed * 0.72,
        life: 0.62 + Math.random() * 0.48,
        size: clamp(scale * 0.045, 2.2, 6.5),
        color: Math.random() > 0.18 ? '#7dd3fc' : '#a98cff',
        gravity: 0,
        drag: 0.975,
        shape: 0,
        targetX: x,
        targetY: y,
        attraction: 1.8,
      });
    }
  }

  dismissCosmicCube(key: string, timestamp: number): void {
    const cube = this.cosmicCubes.get(key);

    if (!cube || cube.phase === 'dismissing') {
      return;
    }

    cube.phase = 'dismissing';
    cube.interactionMode = 'dismissing';
    cube.dismissStartedAt = timestamp;
    cube.lightningStrength = Math.max(cube.lightningStrength, 0.9);
    cube.handleStrength = 0;
    cube.lastUpdatedAt = timestamp;
    this.sparkBurst(
      cube.currentX,
      cube.currentY,
      '#dffbff',
      7,
      Math.max(24, cube.currentScale * 0.9),
      Math.max(2, cube.currentScale * 0.032),
    );
  }

  clearCosmicCube(key: string, dissolve: boolean): void {
    const cube = this.cosmicCubes.get(key);

    if (!cube) {
      return;
    }

    if (dissolve) {
      this.sparkBurst(
        cube.currentX,
        cube.currentY,
        '#7dd3fc',
        22,
        Math.max(100, cube.currentScale * 3.2),
        Math.max(3, cube.currentScale * 0.075),
      );
    }

    this.scene.remove(cube.group);
    cube.shell.material.dispose();
    cube.backShell.material.dispose();
    cube.innerCube.material.dispose();
    cube.innerCage.material.dispose();
    cube.energyCore.material.dispose();
    cube.energyCoreWire.material.dispose();
    cube.coreRays.material.dispose();
    cube.cubeEdges.material.dispose();
    cube.edgeBeams[0]?.material.dispose();
    cube.cornerNodes[0]?.material.dispose();
    cube.chargeFrames.forEach((frame) => frame.material.dispose());
    cube.orbitRings.forEach((ring) => ring.material.dispose());
    cube.coreMaterial.dispose();
    cube.gripHandles.forEach((handle) => handle.material.dispose());
    [...cube.lightningBolts, ...cube.tetherBolts].forEach((stroke) => {
      stroke.core.geometry.dispose();
      stroke.glow.geometry.dispose();
      stroke.coreMaterial.dispose();
      stroke.glowMaterial.dispose();
    });
    this.cosmicCubes.delete(key);
  }

  step(timestamp: number): void {
    if (!this.active) {
      return;
    }

    this.resize();
    const deltaSeconds = this.lastStepAt === null
      ? 1 / 60
      : clamp((timestamp - this.lastStepAt) / 1000, 1 / 240, 0.05);
    this.lastStepAt = timestamp;

    this.updateParticles(deltaSeconds);
    this.updateShockwaves(deltaSeconds);
    this.updateBeams(deltaSeconds);
    this.updateTexts(deltaSeconds);
    this.updateCosmicCubes(deltaSeconds, timestamp);
    this.removeStaleOrbs(timestamp);
    this.uploadParticles();
    this.composer.render(deltaSeconds);
  }

  clear(): void {
    this.particles.length = 0;
    this.particleGeometry.setDrawRange(0, 0);

    while (this.shockwaves.length > 0) {
      this.removeShockwave(0);
    }
    while (this.beams.length > 0) {
      this.removeBeam(0);
    }
    while (this.texts.length > 0) {
      this.removeText(0);
    }
    for (const key of [...this.chargeOrbs.keys()]) {
      this.clearChargeOrb(key);
    }
    for (const key of [...this.cosmicCubes.keys()]) {
      this.clearCosmicCube(key, false);
    }

    this.renderer.clear();
  }

  dispose(): void {
    this.clear();
    this.scene.remove(this.particlePoints);
    this.particleGeometry.dispose();
    this.particleMaterial.dispose();
    this.ringGeometry.dispose();
    this.beamGeometry.dispose();
    this.cubeEdgesGeometry.dispose();
    this.cubeGeometry.dispose();
    this.edgeBeamGeometry.dispose();
    this.energyNodeGeometry.dispose();
    this.energyCoreGeometry.dispose();
    this.coreRayGeometry.dispose();
    this.squareEdgesGeometry.dispose();
    this.radialTexture.dispose();
    this.bloomPass.dispose();
    this.alphaPass.dispose();
    this.composer.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
  }

  private ensureCosmicCube(
    key: string,
    x: number,
    y: number,
    palmScale: number,
    timestamp: number,
  ): CosmicCubeRecord {
    const existing = this.cosmicCubes.get(key);

    if (existing) {
      return existing;
    }

    const shellMaterial = createCosmicCubeMaterial(FrontSide, 1);
    const shell = new Mesh(this.cubeGeometry, shellMaterial);
    const backShellMaterial = createCosmicCubeMaterial(BackSide, 0.46);
    const backShell = new Mesh(this.cubeGeometry, backShellMaterial);
    backShell.renderOrder = 0;
    shell.renderOrder = 1;
    const innerMaterial = new MeshBasicMaterial({
      color: '#39c8ff',
      transparent: true,
      opacity: 0,
      blending: AdditiveBlending,
      depthTest: false,
      depthWrite: false,
      wireframe: true,
      toneMapped: false,
    });
    const innerCube = new Mesh(this.cubeGeometry, innerMaterial);
    innerCube.scale.setScalar(0.44);
    const innerCageMaterial = new MeshBasicMaterial({
      color: '#776cff',
      transparent: true,
      opacity: 0,
      blending: AdditiveBlending,
      depthTest: false,
      depthWrite: false,
      wireframe: true,
      toneMapped: false,
    });
    const innerCage = new Mesh(this.cubeGeometry, innerCageMaterial);
    innerCage.scale.setScalar(0.68);
    innerCage.rotation.set(0.48, -0.36, 0.18);
    const energyCore = new Mesh(this.energyCoreGeometry, createEnergyCoreMaterial());
    energyCore.scale.setScalar(0.16);
    const energyCoreWireMaterial = new MeshBasicMaterial({
      color: '#dffcff',
      transparent: true,
      opacity: 0,
      blending: AdditiveBlending,
      depthTest: false,
      depthWrite: false,
      wireframe: true,
      toneMapped: false,
    });
    const energyCoreWire = new Mesh(this.energyCoreGeometry, energyCoreWireMaterial);
    energyCoreWire.scale.setScalar(0.235);
    const coreRayMaterial = new LineBasicMaterial({
      color: '#66d9ff',
      transparent: true,
      opacity: 0,
      blending: AdditiveBlending,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    });
    const coreRays = new LineSegments(this.coreRayGeometry, coreRayMaterial);
    coreRays.scale.setScalar(0.47);
    const edgeMaterial = new LineBasicMaterial({
      color: '#52d8ff',
      transparent: true,
      opacity: 0,
      blending: AdditiveBlending,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    });
    const cubeEdges = new LineSegments(this.cubeEdgesGeometry, edgeMaterial);
    const energyEdgeMaterial = new MeshBasicMaterial({
      color: '#8cecff',
      transparent: true,
      opacity: 0,
      blending: AdditiveBlending,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    });
    const edgeBeams = createCubeEdgeBeams(this.edgeBeamGeometry, energyEdgeMaterial);
    const cornerNodeMaterial = new MeshBasicMaterial({
      color: '#ecfeff',
      transparent: true,
      opacity: 0,
      blending: AdditiveBlending,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    });
    const cornerNodes = createCubeCornerNodes(
      this.energyNodeGeometry,
      cornerNodeMaterial,
    );
    const cubeRoot = new Group();
    cubeRoot.visible = false;
    cubeRoot.add(
      backShell,
      shell,
      innerCage,
      innerCube,
      coreRays,
      energyCoreWire,
      energyCore,
      cubeEdges,
      ...edgeBeams,
      ...cornerNodes,
    );

    const frameColors = ['#46cfff', '#7dd3fc', '#d8f8ff'];
    const chargeFrames = frameColors.map((color, index) => {
      const material = new LineBasicMaterial({
        color,
        transparent: true,
        opacity: 0,
        blending: AdditiveBlending,
        depthTest: false,
        depthWrite: false,
      });
      const frame = new LineSegments(this.squareEdgesGeometry, material);
      frame.position.z = index * 0.08;
      return frame;
    });
    const orbitRings = ['#48d7ff', '#8073ff'].map((color, index) => {
      const material = new MeshBasicMaterial({
        color,
        transparent: true,
        opacity: 0,
        blending: AdditiveBlending,
        depthTest: false,
        depthWrite: false,
        side: DoubleSide,
        toneMapped: false,
      });
      const ring = new Mesh(this.ringGeometry, material);
      ring.position.z = 1.2 + index * 0.2;
      ring.rotation.x = index === 0 ? 0.92 : -0.68;
      ring.rotation.y = index === 0 ? 0.28 : 0.84;
      return ring;
    });
    const coreMaterial = new SpriteMaterial({
      map: this.radialTexture,
      color: '#74ddff',
      transparent: true,
      opacity: 0,
      blending: AdditiveBlending,
      depthTest: false,
      depthWrite: false,
    });
    const core = new Sprite(coreMaterial);
    core.position.z = 2;
    const gripHandles = ['#dffbff', '#70dcff'].map((color) => {
      const material = new MeshBasicMaterial({
        color,
        transparent: true,
        opacity: 0,
        blending: AdditiveBlending,
        depthTest: false,
        depthWrite: false,
        side: DoubleSide,
      });
      const handle = new Mesh(this.ringGeometry, material);
      handle.position.z = 9;
      handle.visible = false;
      return handle;
    });
    const lightningBolts = Array.from(
      { length: LIGHTNING_BOLT_COUNT },
      (_, index) => createLightningStroke(
        index % 3 === 0 ? '#ffffff' : index % 2 === 0 ? '#85edff' : '#668cff',
      ),
    );
    const tetherBolts = [
      createLightningStroke('#ffffff'),
      createLightningStroke('#a7f4ff'),
    ];
    for (const stroke of [...lightningBolts, ...tetherBolts]) {
      stroke.coreMaterial.resolution.set(this.width, this.height);
      stroke.glowMaterial.resolution.set(this.width, this.height);
    }
    const group = new Group();
    group.position.set(x, y, 6);
    group.add(
      ...chargeFrames,
      ...orbitRings,
      cubeRoot,
      core,
      ...gripHandles,
      ...lightningBolts.flatMap((stroke) => [stroke.glow, stroke.core]),
      ...tetherBolts.flatMap((stroke) => [stroke.glow, stroke.core]),
    );
    this.scene.add(group);

    const scale = palmScale * 0.78;
    const cube: CosmicCubeRecord = {
      group,
      cubeRoot,
      shell,
      backShell,
      innerCube,
      innerCage,
      energyCore,
      energyCoreWire,
      coreRays,
      cubeEdges,
      edgeBeams,
      cornerNodes,
      chargeFrames,
      orbitRings,
      core,
      coreMaterial,
      gripHandles,
      lightningBolts,
      tetherBolts,
      phase: 'charging',
      interactionMode: 'floating',
      progress: 0,
      revealProgress: 0,
      targetX: x,
      targetY: y,
      targetScale: scale,
      currentX: x,
      currentY: y,
      currentScale: scale,
      gripTargetA: null,
      gripTargetB: null,
      fingerA: null,
      fingerB: null,
      hoverA: 0,
      hoverB: 0,
      motionEnergy: 0,
      lightningStrength: 0,
      handleStrength: 0,
      lastLightningAt: Number.NEGATIVE_INFINITY,
      dismissStartedAt: null,
      lastUpdatedAt: timestamp,
    };
    this.cosmicCubes.set(key, cube);
    return cube;
  }

  private updateCosmicCubes(deltaSeconds: number, timestamp: number): void {
    for (const [key, cube] of this.cosmicCubes) {
      if (cube.phase === 'charging' && timestamp - cube.lastUpdatedAt > 420) {
        this.clearCosmicCube(key, false);
        continue;
      }

      if (
        cube.phase === 'dismissing'
        && cube.dismissStartedAt !== null
        && (timestamp - cube.dismissStartedAt) / 1000 >= CUBE_DISMISS_SECONDS
      ) {
        this.clearCosmicCube(key, false);
        continue;
      }

      const positionRate = cube.interactionMode === 'grabbed' ? 19 : 12;
      const scaleRate = cube.interactionMode === 'grabbed' ? 15 : 11;
      const positionAlpha = 1 - Math.exp(-positionRate * deltaSeconds);
      const scaleAlpha = 1 - Math.exp(-scaleRate * deltaSeconds);
      cube.currentX += (cube.targetX - cube.currentX) * positionAlpha;
      cube.currentY += (cube.targetY - cube.currentY) * positionAlpha;
      cube.currentScale += (cube.targetScale - cube.currentScale) * scaleAlpha;
      cube.group.position.set(cube.currentX, cube.currentY, 6);

      if (cube.phase === 'charging') {
        this.updateCubeChargingVisual(cube, deltaSeconds, timestamp);
      } else if (cube.phase === 'dismissing') {
        this.updateCubeDismissVisual(cube, deltaSeconds, timestamp);
      } else {
        this.updateCubeActiveVisual(cube, deltaSeconds, timestamp);
      }
    }
  }

  private updateCubeChargingVisual(
    cube: CosmicCubeRecord,
    deltaSeconds: number,
    timestamp: number,
  ): void {
    cube.core.visible = true;
    const progress = clamp(cube.progress, 0, 1);
    const easedProgress = smoothstep(0, 1, progress);
    const assembly = smoothstep(0.32, 0.96, progress);

    cube.chargeFrames.forEach((frame, index) => {
      const direction = index % 2 === 0 ? 1 : -1;
      const speed = 0.42 + index * 0.18 + easedProgress * 0.92;
      const size = cube.currentScale * (1.5 - easedProgress * 0.58 + index * 0.16);
      const baseOpacity = 0.1 + easedProgress * (0.28 + index * 0.055);
      frame.visible = true;
      frame.rotation.z += direction * speed * deltaSeconds;
      frame.rotation.x = Math.sin(easedProgress * Math.PI + index * 0.8) * 0.16;
      frame.rotation.y = Math.cos(easedProgress * Math.PI * 0.7 + index) * 0.1;
      frame.scale.setScalar(size);
      frame.material.opacity = baseOpacity * (1 - assembly * 0.76);
    });

    const coreSize = cube.currentScale * (0.12 + easedProgress * 0.24);
    cube.core.scale.setScalar(coreSize);
    cube.coreMaterial.opacity = 0.18 + easedProgress * 0.37;
    cube.coreMaterial.color.set(progress > 0.86 ? '#b8f2ff' : '#63d8ff');

    cube.cubeRoot.visible = assembly > 0.005;
    const previewSize = cube.currentScale * (0.42 + assembly * 0.48);
    cube.cubeRoot.scale.setScalar(previewSize);
    cube.cubeRoot.rotation.x += deltaSeconds * (0.12 + easedProgress * 0.36);
    cube.cubeRoot.rotation.y += deltaSeconds * (0.18 + easedProgress * 0.54);
    cube.cubeRoot.rotation.z += deltaSeconds * (0.06 + easedProgress * 0.12);
    cube.innerCube.rotation.x -= deltaSeconds * 0.5;
    cube.innerCube.rotation.y += deltaSeconds * 0.68;
    cube.innerCage.rotation.x += deltaSeconds * 0.28;
    cube.innerCage.rotation.z -= deltaSeconds * 0.42;
    cube.innerCube.material.opacity = assembly * 0.18;
    cube.innerCage.material.opacity = assembly * 0.11;
    cube.energyCore.rotation.x += deltaSeconds * 1.2;
    cube.energyCore.rotation.y -= deltaSeconds * 1.55;
    cube.energyCoreWire.rotation.y += deltaSeconds * 1.8;
    cube.energyCoreWire.rotation.z -= deltaSeconds * 1.1;
    const corePulse = 1 + Math.sin(timestamp * 0.012) * 0.09;
    cube.energyCore.scale.setScalar(0.16 * corePulse);
    cube.energyCoreWire.scale.setScalar(0.235 * (2 - corePulse));
    cube.energyCoreWire.material.opacity = assembly * 0.46;
    cube.coreRays.material.opacity = assembly * 0.26;
    cube.cubeEdges.material.opacity = assembly * 0.16;
    cube.edgeBeams[0]!.material.opacity = assembly * 0.42;
    cube.cornerNodes[0]!.material.opacity = assembly * 0.62;
    cube.orbitRings.forEach((ring, index) => {
      ring.visible = assembly > 0.08;
      ring.scale.setScalar(cube.currentScale * (0.66 + index * 0.12));
      ring.rotation.z += deltaSeconds * (index === 0 ? 0.42 : -0.31);
      ring.material.opacity = assembly * (0.055 + index * 0.018);
    });

    const timeUniform = cube.shell.material.uniforms.uTime;
    const opacityUniform = cube.shell.material.uniforms.uOpacity;
    const energyUniform = cube.shell.material.uniforms.uEnergy;
    const backTimeUniform = cube.backShell.material.uniforms.uTime;
    const backOpacityUniform = cube.backShell.material.uniforms.uOpacity;
    const backEnergyUniform = cube.backShell.material.uniforms.uEnergy;
    const coreTimeUniform = cube.energyCore.material.uniforms.uTime;
    const coreOpacityUniform = cube.energyCore.material.uniforms.uOpacity;

    if (timeUniform) {
      timeUniform.value = timestamp / 1000;
    }
    if (opacityUniform) {
      opacityUniform.value = assembly * 0.34;
    }
    if (energyUniform) {
      energyUniform.value = easedProgress;
    }
    if (backTimeUniform) {
      backTimeUniform.value = timestamp / 1000;
    }
    if (backOpacityUniform) {
      backOpacityUniform.value = assembly * 0.23;
    }
    if (backEnergyUniform) {
      backEnergyUniform.value = easedProgress * 0.72;
    }
    if (coreTimeUniform) {
      coreTimeUniform.value = timestamp / 1000;
    }
    if (coreOpacityUniform) {
      coreOpacityUniform.value = assembly * 0.9;
    }
  }

  private updateCubeActiveVisual(
    cube: CosmicCubeRecord,
    deltaSeconds: number,
    timestamp: number,
  ): void {
    if (cube.phase === 'revealing') {
      cube.revealProgress = clamp(cube.revealProgress + deltaSeconds / 0.42, 0, 1);

      if (cube.revealProgress >= 1) {
        cube.phase = 'active';
      }
    }

    const rawReveal = cube.phase === 'active'
      ? 1
      : cube.revealProgress;
    const reveal = smoothstep(0, 1, rawReveal);
    const interactionEnergy = cube.interactionMode === 'grabbed'
      ? 1
      : cube.interactionMode === 'targeting'
        ? 0.46
        : 0.12;
    const frameOpacity = (1 - reveal) * 0.11
      + reveal * (0.045 + interactionEnergy * 0.085);
    cube.chargeFrames.forEach((frame, index) => {
      const direction = index % 2 === 0 ? 1 : -1;
      frame.rotation.z += direction
        * deltaSeconds
        * (0.3 + interactionEnergy * 0.82 + index * 0.08);
      frame.rotation.x = Math.sin(timestamp * 0.0007 + index * 1.2) * 0.2;
      frame.rotation.y = Math.cos(timestamp * 0.00055 + index * 0.9) * 0.16;
      frame.material.opacity = frameOpacity * (1 - index * 0.11);
      frame.scale.setScalar(cube.currentScale * (1.03 + index * 0.12));
      frame.visible = frameOpacity > 0.008;
    });
    cube.orbitRings.forEach((ring, index) => {
      const direction = index === 0 ? 1 : -1;
      ring.visible = reveal > 0.04;
      ring.scale.setScalar(
        cube.currentScale * (0.68 + index * 0.14 + interactionEnergy * 0.045),
      );
      ring.rotation.z += direction
        * deltaSeconds
        * (0.38 + interactionEnergy * 0.72 + index * 0.12);
      ring.rotation.y += direction * deltaSeconds * 0.08;
      ring.material.opacity = reveal * (0.045 + interactionEnergy * 0.075)
        * (index === 0 ? 1 : 0.78);
    });

    const bounce = 1 + Math.sin(reveal * Math.PI) * 0.025;
    const cubeSize = cube.currentScale * (0.9 + reveal * 0.1) * bounce;
    const isFloating = cube.interactionMode === 'floating';
    const spinMultiplier = isFloating
      ? 1
      : cube.interactionMode === 'targeting'
        ? 0.2
        : 0.045;
    const floatTarget = isFloating
      ? Math.sin(timestamp * 0.0021) * cube.currentScale * 0.038
      : 0;
    const floatAlpha = 1 - Math.exp(-9 * deltaSeconds);
    cube.cubeRoot.visible = true;
    cube.cubeRoot.scale.setScalar(cubeSize);
    cube.cubeRoot.position.y += (floatTarget - cube.cubeRoot.position.y) * floatAlpha;
    cube.cubeRoot.rotation.x += deltaSeconds * 0.48 * spinMultiplier;
    cube.cubeRoot.rotation.y += deltaSeconds * 0.72 * spinMultiplier;
    cube.cubeRoot.rotation.z += deltaSeconds * 0.18 * spinMultiplier;
    cube.innerCube.rotation.x -= deltaSeconds * (0.32 + interactionEnergy * 0.62);
    cube.innerCube.rotation.y += deltaSeconds * (0.54 + interactionEnergy * 0.74);
    cube.innerCage.rotation.x += deltaSeconds * (0.22 + interactionEnergy * 0.3);
    cube.innerCage.rotation.z -= deltaSeconds * (0.31 + interactionEnergy * 0.46);
    cube.innerCube.material.opacity = reveal * (0.14 + interactionEnergy * 0.08);
    cube.innerCage.material.opacity = reveal * (0.08 + interactionEnergy * 0.075);
    cube.cubeEdges.material.opacity = reveal * (0.12 + interactionEnergy * 0.08);
    cube.edgeBeams[0]!.material.opacity = reveal * (0.4 + interactionEnergy * 0.17);
    cube.cornerNodes[0]!.material.opacity = reveal * (0.43 + interactionEnergy * 0.18);

    const internalPulse = 1 + Math.sin(timestamp * 0.0075) * 0.085;
    cube.energyCore.rotation.x += deltaSeconds * (0.9 + interactionEnergy * 1.7);
    cube.energyCore.rotation.y -= deltaSeconds * (1.2 + interactionEnergy * 2.1);
    cube.energyCoreWire.rotation.y += deltaSeconds * (1.3 + interactionEnergy * 2.4);
    cube.energyCoreWire.rotation.z -= deltaSeconds * (0.8 + interactionEnergy * 1.8);
    cube.energyCore.scale.setScalar(
      (0.155 + interactionEnergy * 0.018) * internalPulse,
    );
    cube.energyCoreWire.scale.setScalar(
      (0.235 + interactionEnergy * 0.028) * (2 - internalPulse),
    );
    cube.energyCoreWire.material.opacity = reveal * (0.35 + interactionEnergy * 0.2);
    cube.coreRays.rotation.z += deltaSeconds * (0.18 + interactionEnergy * 0.46);
    cube.coreRays.material.opacity = reveal
      * (0.22 + interactionEnergy * 0.34)
      * (0.84 + Math.sin(timestamp * 0.011) * 0.16);

    const timeUniform = cube.shell.material.uniforms.uTime;
    const opacityUniform = cube.shell.material.uniforms.uOpacity;
    const energyUniform = cube.shell.material.uniforms.uEnergy;
    const backTimeUniform = cube.backShell.material.uniforms.uTime;
    const backOpacityUniform = cube.backShell.material.uniforms.uOpacity;
    const backEnergyUniform = cube.backShell.material.uniforms.uEnergy;
    const coreTimeUniform = cube.energyCore.material.uniforms.uTime;
    const coreOpacityUniform = cube.energyCore.material.uniforms.uOpacity;

    if (timeUniform) {
      timeUniform.value = timestamp / 1000;
    }
    if (opacityUniform) {
      opacityUniform.value = reveal * (0.46 + interactionEnergy * 0.08);
    }
    if (energyUniform) {
      energyUniform.value = 0.42 + interactionEnergy * 0.58;
    }
    if (backTimeUniform) {
      backTimeUniform.value = timestamp / 1000;
    }
    if (backOpacityUniform) {
      backOpacityUniform.value = reveal * (0.16 + interactionEnergy * 0.04);
    }
    if (backEnergyUniform) {
      backEnergyUniform.value = 0.28 + interactionEnergy * 0.32;
    }
    if (coreTimeUniform) {
      coreTimeUniform.value = timestamp / 1000;
    }
    if (coreOpacityUniform) {
      coreOpacityUniform.value = reveal * (0.62 + interactionEnergy * 0.14);
    }

    const pulse = 1 + Math.sin(timestamp * 0.0055) * 0.055;
    cube.core.visible = true;
    cube.core.scale.setScalar(
      cube.currentScale * (0.22 + interactionEnergy * 0.035) * pulse,
    );
    cube.coreMaterial.opacity = reveal * (0.12 + interactionEnergy * 0.08);
    cube.coreMaterial.color.set(interactionEnergy > 0.65 ? '#d8fbff' : '#64d8ff');
    this.updateCubeInteractionVisual(cube, deltaSeconds, timestamp);
  }

  private updateCubeDismissVisual(
    cube: CosmicCubeRecord,
    deltaSeconds: number,
    timestamp: number,
  ): void {
    const startedAt = cube.dismissStartedAt ?? timestamp;
    const progress = clamp(
      (timestamp - startedAt) / (CUBE_DISMISS_SECONDS * 1000),
      0,
      1,
    );
    const verticalCollapse = 1 - smoothstep(0.08, 0.66, progress);
    const horizontalCollapse = 1 - smoothstep(0.56, 0.98, progress);
    const flash = 1 + Math.sin(smoothstep(0, 0.24, progress) * Math.PI) * 0.08;
    const opacity = 1 - smoothstep(0.68, 1, progress);

    cube.chargeFrames.forEach((frame) => {
      frame.visible = false;
    });
    cube.orbitRings.forEach((ring) => {
      ring.visible = false;
    });
    cube.cubeRoot.visible = true;
    cube.cubeRoot.position.y *= Math.max(0, 1 - deltaSeconds * 18);
    cube.cubeRoot.rotation.x += deltaSeconds * (1.1 + progress * 5.2);
    cube.cubeRoot.rotation.y += deltaSeconds * (1.4 + progress * 6.4);
    cube.cubeRoot.scale.set(
      cube.currentScale * horizontalCollapse * flash,
      cube.currentScale * Math.max(0.012, verticalCollapse) * flash,
      cube.currentScale * Math.max(0.012, verticalCollapse) * flash,
    );
    cube.innerCube.material.opacity = 0.08 * opacity;
    cube.innerCage.material.opacity = 0.065 * opacity;
    cube.cubeEdges.material.opacity = (0.6 + (1 - progress) * 0.18) * opacity;
    cube.edgeBeams[0]!.material.opacity = (0.72 + progress * 0.26) * opacity;
    cube.cornerNodes[0]!.material.opacity = (0.8 + progress * 0.2) * opacity;
    cube.energyCore.rotation.x += deltaSeconds * (3 + progress * 9);
    cube.energyCore.rotation.y -= deltaSeconds * (4 + progress * 11);
    cube.energyCore.scale.setScalar(0.17 * Math.max(0.08, horizontalCollapse));
    cube.energyCoreWire.scale.setScalar(0.25 * Math.max(0.08, horizontalCollapse));
    cube.energyCoreWire.material.opacity = opacity;
    cube.coreRays.material.opacity = (0.42 + progress * 0.5) * opacity;

    const opacityUniform = cube.shell.material.uniforms.uOpacity;
    const timeUniform = cube.shell.material.uniforms.uTime;
    const energyUniform = cube.shell.material.uniforms.uEnergy;
    const backOpacityUniform = cube.backShell.material.uniforms.uOpacity;
    const backTimeUniform = cube.backShell.material.uniforms.uTime;
    const backEnergyUniform = cube.backShell.material.uniforms.uEnergy;
    const coreTimeUniform = cube.energyCore.material.uniforms.uTime;
    const coreOpacityUniform = cube.energyCore.material.uniforms.uOpacity;

    if (opacityUniform) {
      opacityUniform.value = (0.62 + (1 - progress) * 0.18) * opacity;
    }
    if (timeUniform) {
      timeUniform.value = timestamp / 1000;
    }
    if (energyUniform) {
      energyUniform.value = 1 + progress * 0.8;
    }
    if (backOpacityUniform) {
      backOpacityUniform.value = 0.16 * opacity;
    }
    if (backTimeUniform) {
      backTimeUniform.value = timestamp / 1000;
    }
    if (backEnergyUniform) {
      backEnergyUniform.value = 0.5 + progress;
    }
    if (coreTimeUniform) {
      coreTimeUniform.value = timestamp / 1000;
    }
    if (coreOpacityUniform) {
      coreOpacityUniform.value = opacity;
    }

    const coreScale = cube.currentScale
      * (0.2 + (1 - progress) * 0.13)
      * Math.max(0.04, horizontalCollapse);
    cube.core.visible = true;
    cube.core.scale.setScalar(coreScale);
    cube.coreMaterial.opacity = (0.32 + (1 - progress) * 0.34) * opacity;
    cube.coreMaterial.color.set(progress < 0.5 ? '#e8fdff' : '#64d8ff');
    this.updateCubeInteractionVisual(cube, deltaSeconds, timestamp, progress);
  }

  private updateCubeInteractionVisual(
    cube: CosmicCubeRecord,
    deltaSeconds: number,
    timestamp: number,
    dismissProgress = 0,
  ): void {
    const handleTarget = cube.interactionMode === 'targeting'
      ? Math.max(cube.hoverA, cube.hoverB)
      : cube.interactionMode === 'grabbed'
        ? 1
        : 0;
    const lightningTarget = cube.interactionMode === 'grabbed'
      || cube.interactionMode === 'dismissing'
      ? 1
      : cube.interactionMode === 'targeting'
        ? 0.34
        : 0.12;
    const handleAlpha = 1 - Math.exp(-14 * deltaSeconds);
    const lightningRate = lightningTarget > cube.lightningStrength ? 18 : 11;
    const lightningAlpha = 1 - Math.exp(-lightningRate * deltaSeconds);
    cube.handleStrength += (handleTarget - cube.handleStrength) * handleAlpha;
    cube.lightningStrength += (
      lightningTarget - cube.lightningStrength
    ) * lightningAlpha;

    this.updateGripHandles(cube, timestamp);

    if (cube.lightningStrength <= 0.015) {
      [...cube.lightningBolts, ...cube.tetherBolts].forEach((stroke) => {
        setLightningStrokeVisible(stroke, false);
      });
      return;
    }

    const refreshInterval = cube.interactionMode === 'grabbed'
      || cube.interactionMode === 'dismissing'
      ? 72 - cube.motionEnergy * 30
      : cube.interactionMode === 'targeting'
        ? 135
        : 230;

    if (timestamp - cube.lastLightningAt >= refreshInterval) {
      cube.lastLightningAt = timestamp;
      this.refreshCubeLightning(cube, dismissProgress);
    }

    const flicker = 0.86
      + Math.sin(timestamp * 0.051) * 0.08
      + Math.sin(timestamp * 0.019 + 1.3) * 0.05;
    const visibleBoltCount = cube.interactionMode === 'grabbed'
      || cube.interactionMode === 'dismissing'
      ? LIGHTNING_BOLT_COUNT
      : cube.interactionMode === 'targeting'
        ? 4
        : 2;
    cube.lightningBolts.forEach((stroke, index) => {
      const visible = index < visibleBoltCount;
      setLightningStrokeVisible(stroke, visible);
      const opacity = cube.lightningStrength
        * flicker
        * (0.31 + cube.motionEnergy * 0.34)
        * (1 - index * 0.038);
      stroke.coreMaterial.opacity = opacity;
      stroke.glowMaterial.opacity = opacity * 0.34;
    });
    cube.tetherBolts.forEach((stroke, index) => {
      const visible = cube.interactionMode === 'grabbed';
      setLightningStrokeVisible(stroke, visible);
      const opacity = cube.lightningStrength
        * (0.5 + cube.motionEnergy * 0.35)
        * (index === 0 ? 1 : 0.82);
      stroke.coreMaterial.opacity = opacity;
      stroke.glowMaterial.opacity = opacity * 0.42;
    });
  }

  private updateGripHandles(cube: CosmicCubeRecord, timestamp: number): void {
    const targets = [cube.gripTargetA, cube.gripTargetB];
    const hovers = [cube.hoverA, cube.hoverB];
    const handleRadius = clamp(cube.currentScale * 0.095, 6, 16);

    cube.gripHandles.forEach((handle, index) => {
      const target = targets[index];

      if (!target || cube.handleStrength <= 0.01) {
        handle.visible = false;
        return;
      }

      handle.visible = true;
      handle.position.set(
        target.x - cube.currentX,
        target.y - cube.currentY,
        9,
      );
      const pulse = 1 + Math.sin(timestamp * 0.009 + index * 1.7) * 0.12;
      handle.scale.setScalar(handleRadius * pulse);
      handle.rotation.z += index === 0 ? 0.035 : -0.035;
      handle.material.opacity = cube.handleStrength * (0.22 + (hovers[index] ?? 0) * 0.62);
    });
  }

  private refreshCubeLightning(cube: CosmicCubeRecord, dismissProgress: number): void {
    const convergence = 1 - smoothstep(0.08, 0.94, dismissProgress);
    const radius = cube.currentScale * (0.62 + cube.motionEnergy * 0.08) * convergence;
    const amplitude = cube.currentScale
      * (0.055 + cube.motionEnergy * 0.055)
      * Math.max(0.18, convergence);

    cube.lightningBolts.forEach((stroke, index) => {
      const startAngle = Math.random() * Math.PI * 2 + index * 0.35;
      const span = (0.54 + Math.random() * 1.12) * (index % 2 === 0 ? 1 : -1);
      setLightningArc(stroke, radius, startAngle, span, amplitude);
    });

    const targets = [cube.gripTargetA, cube.gripTargetB];
    const fingers = [cube.fingerA, cube.fingerB];
    cube.tetherBolts.forEach((stroke, index) => {
      const target = targets[index];
      const finger = fingers[index];

      if (!target || !finger) {
        setLightningStrokeVisible(stroke, false);
        return;
      }

      setLightningPath(
        stroke,
        {
          x: target.x - cube.currentX,
          y: target.y - cube.currentY,
        },
        {
          x: finger.x - cube.currentX,
          y: finger.y - cube.currentY,
        },
        cube.currentScale * (0.035 + cube.motionEnergy * 0.035),
      );
    });
  }

  private configureParticleGeometry(): void {
    this.particleGeometry.setAttribute('position', dynamicAttribute(this.positions, 3));
    this.particleGeometry.setAttribute('color', dynamicAttribute(this.colors, 3));
    this.particleGeometry.setAttribute('aSize', dynamicAttribute(this.sizes, 1));
    this.particleGeometry.setAttribute('aAlpha', dynamicAttribute(this.alphas, 1));
    this.particleGeometry.setAttribute('aRotation', dynamicAttribute(this.rotations, 1));
    this.particleGeometry.setAttribute('aShape', dynamicAttribute(this.shapes, 1));
    this.particleGeometry.setDrawRange(0, 0);
  }

  private addParticle(options: {
    x: number;
    y: number;
    vx: number;
    vy: number;
    life: number;
    size: number;
    color: string;
    gravity: number;
    drag: number;
    shape: ParticleShape;
    rotation?: number;
    spin?: number;
    targetX?: number;
    targetY?: number;
    attraction?: number;
  }): void {
    if (this.particles.length >= MAX_PARTICLES) {
      this.particles.shift();
    }

    this.particles.push({
      x: options.x,
      y: options.y,
      vx: options.vx,
      vy: options.vy,
      life: options.life,
      maxLife: options.life,
      size: options.size,
      color: new Color(options.color),
      gravity: options.gravity,
      drag: options.drag,
      rotation: options.rotation ?? 0,
      spin: options.spin ?? 0,
      shape: options.shape,
      targetX: options.targetX ?? null,
      targetY: options.targetY ?? null,
      attraction: options.attraction ?? 0,
    });
  }

  private updateParticles(deltaSeconds: number): void {
    const dragFrames = deltaSeconds * 60;

    for (let index = this.particles.length - 1; index >= 0; index -= 1) {
      const particle = this.particles[index];

      if (!particle) {
        continue;
      }

      if (particle.targetX !== null && particle.targetY !== null) {
        particle.vx += (particle.targetX - particle.x) * particle.attraction * deltaSeconds;
        particle.vy += (particle.targetY - particle.y) * particle.attraction * deltaSeconds;
      }

      const drag = Math.pow(particle.drag, dragFrames);
      particle.vx *= drag;
      particle.vy = particle.vy * drag + particle.gravity * deltaSeconds;
      particle.x += particle.vx * deltaSeconds;
      particle.y += particle.vy * deltaSeconds;
      particle.rotation += particle.spin * deltaSeconds;
      particle.life -= deltaSeconds;

      if (particle.life <= 0) {
        this.particles.splice(index, 1);
      }
    }
  }

  private uploadParticles(): void {
    this.particles.forEach((particle, index) => {
      const offset = index * 3;
      this.positions[offset] = particle.x;
      this.positions[offset + 1] = particle.y;
      this.positions[offset + 2] = PARTICLE_Z;
      this.colors[offset] = particle.color.r;
      this.colors[offset + 1] = particle.color.g;
      this.colors[offset + 2] = particle.color.b;
      this.sizes[index] = particle.size;
      this.alphas[index] = clamp(particle.life / particle.maxLife, 0, 1);
      this.rotations[index] = particle.rotation;
      this.shapes[index] = particle.shape;
    });

    for (const name of ['position', 'color', 'aSize', 'aAlpha', 'aRotation', 'aShape']) {
      const attribute = this.particleGeometry.getAttribute(name);
      attribute.needsUpdate = true;
    }
    this.particleGeometry.setDrawRange(0, this.particles.length);
  }

  private updateShockwaves(deltaSeconds: number): void {
    for (let index = this.shockwaves.length - 1; index >= 0; index -= 1) {
      const shockwave = this.shockwaves[index];

      if (!shockwave) {
        continue;
      }

      shockwave.life -= deltaSeconds;
      const elapsed = 1 - clamp(shockwave.life / shockwave.maxLife, 0, 1);
      const radius = shockwave.maxRadius * (1 - Math.pow(1 - elapsed, 2.4));
      shockwave.mesh.scale.setScalar(Math.max(3, radius));
      shockwave.mesh.material.opacity = Math.pow(1 - elapsed, 1.5);

      if (shockwave.life <= 0) {
        this.removeShockwave(index);
      }
    }
  }

  private updateBeams(deltaSeconds: number): void {
    for (let index = this.beams.length - 1; index >= 0; index -= 1) {
      const beam = this.beams[index];

      if (!beam) {
        continue;
      }

      beam.life -= deltaSeconds;
      const opacity = clamp(beam.life / beam.maxLife, 0, 1);
      const uniform = beam.mesh.material.uniforms.uOpacity;

      if (uniform) {
        uniform.value = opacity;
      }

      if (beam.life <= 0) {
        this.removeBeam(index);
      }
    }
  }

  private updateTexts(deltaSeconds: number): void {
    for (let index = this.texts.length - 1; index >= 0; index -= 1) {
      const text = this.texts[index];

      if (!text) {
        continue;
      }

      text.life -= deltaSeconds;
      text.sprite.position.y += text.velocityY * deltaSeconds;
      text.material.opacity = clamp(text.life / text.maxLife, 0, 1);

      if (text.life <= 0) {
        this.removeText(index);
      }
    }
  }

  private removeStaleOrbs(timestamp: number): void {
    for (const [key, orb] of this.chargeOrbs) {
      if (timestamp - orb.lastUpdatedAt > 140) {
        this.clearChargeOrb(key);
      }
    }
  }

  private trimShockwaves(): void {
    while (this.shockwaves.length > MAX_SHOCKWAVES) {
      this.removeShockwave(0);
    }
  }

  private trimBeams(): void {
    while (this.beams.length > MAX_BEAMS) {
      this.removeBeam(0);
    }
  }

  private trimTexts(): void {
    while (this.texts.length > MAX_TEXTS) {
      this.removeText(0);
    }
  }

  private removeShockwave(index: number): void {
    const [shockwave] = this.shockwaves.splice(index, 1);

    if (shockwave) {
      this.scene.remove(shockwave.mesh);
      shockwave.mesh.material.dispose();
    }
  }

  private removeBeam(index: number): void {
    const [beam] = this.beams.splice(index, 1);

    if (beam) {
      this.scene.remove(beam.mesh);
      beam.mesh.material.dispose();
    }
  }

  private removeText(index: number): void {
    const [text] = this.texts.splice(index, 1);

    if (text) {
      this.scene.remove(text.sprite);
      text.material.dispose();
      text.texture.dispose();
    }
  }

  private resize(): void {
    const bounds = this.canvas.getBoundingClientRect();
    const width = Math.max(1, bounds.width);
    const height = Math.max(1, bounds.height);
    const pixelRatio = Math.min(window.devicePixelRatio, 2);

    if (width === this.width && height === this.height && pixelRatio === this.pixelRatio) {
      return;
    }

    this.width = width;
    this.height = height;
    this.pixelRatio = pixelRatio;
    this.renderer.setPixelRatio(pixelRatio);
    this.renderer.setSize(width, height, false);
    this.composer.setPixelRatio(pixelRatio);
    this.composer.setSize(width, height);
    for (const cube of this.cosmicCubes.values()) {
      for (const stroke of [...cube.lightningBolts, ...cube.tetherBolts]) {
        stroke.coreMaterial.resolution.set(width, height);
        stroke.glowMaterial.resolution.set(width, height);
      }
    }
    this.camera.left = 0;
    this.camera.right = width;
    this.camera.top = 0;
    this.camera.bottom = height;
    this.camera.position.z = 500;
    this.camera.updateProjectionMatrix();
    const uniform = this.particleMaterial.uniforms.uPixelRatio;

    if (uniform) {
      uniform.value = pixelRatio;
    }
  }
}

function createCoreRayGeometry(): BufferGeometry {
  const geometry = new BufferGeometry();
  const directions = [
    [1, 0, 0],
    [-1, 0, 0],
    [0, 1, 0],
    [0, -1, 0],
    [0, 0, 1],
    [0, 0, -1],
  ] as const;
  const positions = directions.flatMap(([x, y, z]) => [0, 0, 0, x, y, z]);
  geometry.setAttribute('position', new BufferAttribute(new Float32Array(positions), 3));
  return geometry;
}

function createCubeEdgeBeams(
  geometry: CylinderGeometry,
  material: MeshBasicMaterial,
): Array<Mesh<CylinderGeometry, MeshBasicMaterial>> {
  const beams: Array<Mesh<CylinderGeometry, MeshBasicMaterial>> = [];
  const sides = [-0.5, 0.5] as const;

  for (const y of sides) {
    for (const z of sides) {
      const beam = new Mesh(geometry, material);
      beam.position.set(0, y, z);
      beam.rotation.z = Math.PI / 2;
      beams.push(beam);
    }
  }
  for (const x of sides) {
    for (const z of sides) {
      const beam = new Mesh(geometry, material);
      beam.position.set(x, 0, z);
      beams.push(beam);
    }
  }
  for (const x of sides) {
    for (const y of sides) {
      const beam = new Mesh(geometry, material);
      beam.position.set(x, y, 0);
      beam.rotation.x = Math.PI / 2;
      beams.push(beam);
    }
  }
  return beams;
}

function createCubeCornerNodes(
  geometry: OctahedronGeometry,
  material: MeshBasicMaterial,
): Array<Mesh<OctahedronGeometry, MeshBasicMaterial>> {
  const nodes: Array<Mesh<OctahedronGeometry, MeshBasicMaterial>> = [];
  const sides = [-0.5, 0.5] as const;

  for (const x of sides) {
    for (const y of sides) {
      for (const z of sides) {
        const node = new Mesh(geometry, material);
        node.position.set(x, y, z);
        nodes.push(node);
      }
    }
  }
  return nodes;
}

function createLightningStroke(color: string): LightningStroke {
  const coreGeometry = new LineGeometry();
  const glowGeometry = new LineGeometry();
  const coreMaterial = new LineMaterial({
    color,
    linewidth: 1.45,
    transparent: true,
    opacity: 0,
    worldUnits: false,
  });
  const glowMaterial = new LineMaterial({
    color,
    linewidth: 5.2,
    transparent: true,
    opacity: 0,
    worldUnits: false,
  });

  for (const material of [coreMaterial, glowMaterial]) {
    material.blending = AdditiveBlending;
    material.depthTest = false;
    material.depthWrite = false;
    material.toneMapped = false;
    material.resolution.set(1, 1);
  }

  const core = new Line2(coreGeometry, coreMaterial);
  const glow = new Line2(glowGeometry, glowMaterial);

  for (const line of [core, glow]) {
    line.frustumCulled = false;
    line.visible = false;
    line.position.z = 10;
  }

  return { core, glow, coreMaterial, glowMaterial };
}

function setLightningStrokeVisible(stroke: LightningStroke, visible: boolean): void {
  stroke.core.visible = visible;
  stroke.glow.visible = visible;
}

function setLightningPath(
  stroke: LightningStroke,
  start: ScreenPoint,
  end: ScreenPoint,
  amplitude: number,
): void {
  const positions: number[] = [];
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = Math.max(1, Math.hypot(dx, dy));
  const normalX = -dy / length;
  const normalY = dx / length;

  for (let index = 0; index <= LIGHTNING_SEGMENTS; index += 1) {
    const progress = index / LIGHTNING_SEGMENTS;
    const envelope = Math.sin(progress * Math.PI);
    const offset = (Math.random() - 0.5) * 2 * amplitude * envelope;
    positions.push(
      start.x + dx * progress + normalX * offset,
      start.y + dy * progress + normalY * offset,
      10 + (Math.random() - 0.5) * 0.4,
    );
  }
  stroke.core.geometry.setPositions(positions);
  stroke.glow.geometry.setPositions(positions);
  stroke.core.computeLineDistances();
  stroke.glow.computeLineDistances();
}

function setLightningArc(
  stroke: LightningStroke,
  radius: number,
  startAngle: number,
  span: number,
  amplitude: number,
): void {
  const positions: number[] = [];

  for (let index = 0; index <= LIGHTNING_SEGMENTS; index += 1) {
    const progress = index / LIGHTNING_SEGMENTS;
    const angle = startAngle + span * progress;
    const envelope = Math.sin(progress * Math.PI);
    const noisyRadius = Math.max(
      0,
      radius + (Math.random() - 0.5) * 2 * amplitude * envelope,
    );
    positions.push(
      Math.cos(angle) * noisyRadius,
      Math.sin(angle) * noisyRadius * 0.82,
      10 + (Math.random() - 0.5) * 0.8,
    );
  }
  stroke.core.geometry.setPositions(positions);
  stroke.glow.geometry.setPositions(positions);
  stroke.core.computeLineDistances();
  stroke.glow.computeLineDistances();
}

function createCosmicCubeMaterial(
  side: typeof FrontSide | typeof BackSide,
  layerWeight: number,
): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uOpacity: { value: 0 },
      uEnergy: { value: 0 },
      uLayerWeight: { value: layerWeight },
    },
    vertexShader: `
      varying vec3 vLocalPosition;
      varying vec3 vViewNormal;
      varying vec3 vObjectNormal;

      void main() {
        vLocalPosition = position;
        vViewNormal = normalize(normalMatrix * normal);
        vObjectNormal = normal;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform float uTime;
      uniform float uOpacity;
      uniform float uEnergy;
      uniform float uLayerWeight;
      varying vec3 vLocalPosition;
      varying vec3 vViewNormal;
      varying vec3 vObjectNormal;

      float hash21(vec2 point) {
        return fract(sin(dot(point, vec2(127.1, 311.7))) * 43758.5453);
      }

      vec2 hash22(vec2 point) {
        return fract(sin(vec2(
          dot(point, vec2(127.1, 311.7)),
          dot(point, vec2(269.5, 183.3))
        )) * 43758.5453);
      }

      vec2 cubeFaceUv(vec3 position, vec3 normal) {
        vec3 axis = abs(normal);
        if (axis.x > axis.y && axis.x > axis.z) {
          return position.zy + 0.5;
        }
        if (axis.y > axis.z) {
          return position.xz + 0.5;
        }
        return position.xy + 0.5;
      }

      void main() {
        vec2 uv = cubeFaceUv(vLocalPosition, vObjectNormal);
        vec2 circuitUv = uv * 7.0;
        vec2 circuitCell = floor(circuitUv);
        vec2 circuitLocal = fract(circuitUv) - 0.5;
        float selectorA = hash21(circuitCell + vec2(2.3, 7.1));
        float selectorB = hash21(circuitCell + vec2(13.7, 3.9));
        float vertical = (1.0 - smoothstep(0.025, 0.085, abs(circuitLocal.x)))
          * step(0.43, selectorA);
        float horizontal = (1.0 - smoothstep(0.025, 0.085, abs(circuitLocal.y)))
          * step(0.52, selectorB);
        float circuit = max(vertical, horizontal);
        float flow = 0.5 + 0.5 * sin(
          uTime * (2.4 + uEnergy * 1.8)
          - dot(circuitCell, vec2(0.84, 1.37))
        );
        circuit *= 0.42 + smoothstep(0.45, 0.95, flow) * (0.6 + uEnergy * 0.4);

        vec2 starUv = uv * 12.0;
        vec2 starCell = floor(starUv);
        vec2 starPoint = hash22(starCell + vec2(19.2, 4.7));
        float star = 1.0 - smoothstep(
          0.025,
          0.115,
          length(fract(starUv) - starPoint)
        );
        star *= step(0.82, hash21(starCell + vec2(5.4, 21.6)));
        float starPulse = 0.58 + 0.42 * sin(
          uTime * 3.1 + hash21(starCell) * 6.28318
        );
        star *= max(0.16, starPulse);

        float fresnel = pow(1.0 - abs(vViewNormal.z), 2.35);
        vec3 deepCrystal = vec3(0.004, 0.018, 0.075);
        vec3 circuitColor = mix(
          vec3(0.02, 0.65, 1.55),
          vec3(0.38, 0.2, 1.45),
          selectorB * 0.24
        );
        vec3 color = deepCrystal;
        color += fresnel * vec3(0.035, 0.24, 0.72);
        color += circuit * circuitColor * (0.76 + uEnergy * 0.5);
        color += star * vec3(1.15, 1.7, 2.15);
        float alpha = (
          0.075
          + fresnel * 0.32
          + circuit * 0.52
          + star * 0.7
        ) * uOpacity * uLayerWeight;
        gl_FragColor = vec4(color, clamp(alpha, 0.0, 0.82));
      }
    `,
    transparent: true,
    depthTest: false,
    depthWrite: false,
    side,
    toneMapped: false,
  });
}

function createEnergyCoreMaterial(): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uOpacity: { value: 0 },
    },
    vertexShader: `
      varying vec3 vNormal;
      varying vec3 vPosition;
      void main() {
        vNormal = normalize(normalMatrix * normal);
        vPosition = position;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform float uTime;
      uniform float uOpacity;
      varying vec3 vNormal;
      varying vec3 vPosition;
      void main() {
        float pulse = 0.72 + 0.28 * sin(uTime * 5.4 + length(vPosition) * 8.0);
        float fresnel = pow(1.0 - abs(vNormal.z), 1.7);
        float facets = 0.55 + 0.45 * abs(
          sin(vPosition.x * 9.0 + uTime * 2.1)
          * cos(vPosition.y * 8.0 - uTime * 1.7)
        );
        vec3 color = mix(
          vec3(0.06, 0.58, 1.3),
          vec3(0.92, 1.34, 1.64),
          clamp(fresnel + facets * 0.5, 0.0, 1.0)
        );
        float alpha = (0.5 + fresnel * 0.38 + facets * 0.18) * pulse * uOpacity;
        gl_FragColor = vec4(color, clamp(alpha, 0.0, 1.0));
      }
    `,
    transparent: true,
    blending: AdditiveBlending,
    depthTest: false,
    depthWrite: false,
    side: DoubleSide,
    toneMapped: false,
  });
}

function createSquareEdgesGeometry(): EdgesGeometry {
  const plane = new PlaneGeometry(1, 1);
  const edges = new EdgesGeometry(plane);
  plane.dispose();
  return edges;
}

function createParticleMaterial(texture: CanvasTexture): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: {
      uTexture: { value: texture },
      uPixelRatio: { value: 1 },
    },
    vertexShader: `
      attribute float aSize;
      attribute float aAlpha;
      attribute float aRotation;
      attribute float aShape;
      varying vec3 vColor;
      varying float vAlpha;
      varying float vRotation;
      varying float vShape;
      uniform float uPixelRatio;

      void main() {
        vColor = color;
        vAlpha = aAlpha;
        vRotation = aRotation;
        vShape = aShape;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = max(1.0, aSize * uPixelRatio);
      }
    `,
    fragmentShader: `
      uniform sampler2D uTexture;
      varying vec3 vColor;
      varying float vAlpha;
      varying float vRotation;
      varying float vShape;

      void main() {
        vec2 point = gl_PointCoord - 0.5;
        float sine = sin(vRotation);
        float cosine = cos(vRotation);
        point = mat2(cosine, -sine, sine, cosine) * point;
        float mask = 1.0;

        if (vShape < 0.5) {
          mask = texture2D(uTexture, gl_PointCoord).a;
        } else if (vShape < 1.5) {
          if (abs(point.x) > 0.28 || abs(point.y) > 0.48) discard;
          mask = 0.92;
        } else {
          vec2 heart = vec2(point.x * 2.15, point.y * 2.15 + 0.08);
          float base = heart.x * heart.x + heart.y * heart.y - 0.3;
          float field = base * base * base - heart.x * heart.x * heart.y * heart.y * heart.y;
          if (field > 0.0) discard;
          mask = smoothstep(0.08, -0.04, field);
        }

        gl_FragColor = vec4(vColor, vAlpha * mask);
      }
    `,
    transparent: true,
    vertexColors: true,
    blending: AdditiveBlending,
    depthTest: false,
    depthWrite: false,
  });
}

function createBeamMaterial(color: string): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: {
      uColor: { value: new Color(color) },
      uOpacity: { value: 1 },
    },
    vertexShader: `
      varying vec2 vUv;
      void main() {
        vUv = uv;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform vec3 uColor;
      uniform float uOpacity;
      varying vec2 vUv;
      void main() {
        float axial = smoothstep(1.0, 0.08, vUv.x) * smoothstep(0.0, 0.05, vUv.x);
        float core = pow(max(0.0, 1.0 - abs(vUv.y - 0.5) * 2.0), 2.5);
        gl_FragColor = vec4(uColor, axial * core * uOpacity);
      }
    `,
    transparent: true,
    blending: AdditiveBlending,
    depthTest: false,
    depthWrite: false,
    side: DoubleSide,
  });
}

function createRadialTexture(): CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 64;
  const context = canvas.getContext('2d');

  if (!context) {
    throw new Error('Unable to create the procedural particle texture.');
  }

  const gradient = context.createRadialGradient(32, 32, 0, 32, 32, 32);
  gradient.addColorStop(0, 'rgba(255,255,255,1)');
  gradient.addColorStop(0.16, 'rgba(255,255,255,0.98)');
  gradient.addColorStop(0.48, 'rgba(255,255,255,0.38)');
  gradient.addColorStop(1, 'rgba(255,255,255,0)');
  context.fillStyle = gradient;
  context.fillRect(0, 0, 64, 64);

  const texture = new CanvasTexture(canvas);
  texture.minFilter = LinearFilter;
  texture.magFilter = LinearFilter;
  texture.needsUpdate = true;
  return texture;
}

function createTextTexture(
  text: string,
  color: string,
  fontSize: number,
): { texture: CanvasTexture; width: number; height: number } {
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d');

  if (!context) {
    throw new Error('Unable to create a text effect texture.');
  }

  const font = `800 ${fontSize}px system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
  context.font = font;
  const width = Math.ceil(context.measureText(text).width + fontSize * 1.3);
  const height = Math.ceil(fontSize * 1.75);
  canvas.width = width;
  canvas.height = height;
  context.font = font;
  context.textAlign = 'center';
  context.textBaseline = 'middle';
  context.shadowColor = color;
  context.shadowBlur = fontSize * 0.32;
  context.lineWidth = Math.max(2, fontSize * 0.06);
  context.strokeStyle = 'rgba(5,8,11,0.7)';
  context.strokeText(text, width / 2, height / 2);
  context.fillStyle = color;
  context.fillText(text, width / 2, height / 2);

  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  texture.minFilter = LinearFilter;
  texture.magFilter = LinearFilter;
  texture.needsUpdate = true;
  return { texture, width, height };
}

function dynamicAttribute(array: Float32Array, itemSize: number): BufferAttribute {
  const attribute = new BufferAttribute(array, itemSize);
  attribute.setUsage(DynamicDrawUsage);
  return attribute;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function smoothstep(edge0: number, edge1: number, value: number): number {
  const normalized = clamp((value - edge0) / (edge1 - edge0), 0, 1);
  return normalized * normalized * (3 - 2 * normalized);
}
