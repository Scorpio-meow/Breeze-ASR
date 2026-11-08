const STRING_LEVEL = 1.76;
const MUSIC_LEVEL = 1;
const REVERB_SECONDS = 1.6;
const REVERB_WET = 0.22;
const REVERB_SEED = 0x2f6b1d;
const COMPRESSOR_THRESHOLD = -18;
const COMPRESSOR_RATIO = 4;
const LIMIT_THRESHOLD = -3;
const LIMIT_RATIO = 20;
const LIMIT_ATTACK = 0.001;
const LIMIT_RELEASE = 0.1;
const LIMIT_TRIM = (10 ** ((LIMIT_THRESHOLD * (1 - 1 / LIMIT_RATIO)) / 20)) ** 0.6;
const STRING_PAN = 0.6;
const STRING_DAMP = 0.006;
function seeded(seed) {
  let s = seed;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function impulse(ctx) {
  const length = Math.floor(ctx.sampleRate * REVERB_SECONDS);
  const buffer = new AudioBuffer({ numberOfChannels: 2, length, sampleRate: ctx.sampleRate });
  const random = seeded(REVERB_SEED);
  for (let c = 0; c < 2; c++) {
    const data = buffer.getChannelData(c);
    for (let i = 0; i < length; i++) data[i] = (random() * 2 - 1) * (1 - i / length) ** 3;
  }
  return buffer;
}
export function createMix(ctx) {
  const limiter = new DynamicsCompressorNode(ctx, { threshold: LIMIT_THRESHOLD, knee: 0, ratio: LIMIT_RATIO, attack: LIMIT_ATTACK, release: LIMIT_RELEASE });
  const compressor = new DynamicsCompressorNode(ctx, { threshold: COMPRESSOR_THRESHOLD, ratio: COMPRESSOR_RATIO });
  const strings = new GainNode(ctx, { gain: STRING_LEVEL });
  const music = new GainNode(ctx, { gain: MUSIC_LEVEL });
  const wet = new GainNode(ctx, { gain: REVERB_WET });
  limiter.connect(new GainNode(ctx, { gain: LIMIT_TRIM })).connect(ctx.destination);
  compressor.connect(limiter);
  music.connect(limiter);
  strings.connect(compressor);
  strings.connect(new ConvolverNode(ctx, { buffer: impulse(ctx) })).connect(wet).connect(compressor);
  return { strings, music };
}
export function stringVoice(ctx, bus, buffer, offset, at, velocity, position, previous) {
  if (previous) {
    previous.gain.gain.setTargetAtTime(0, at, STRING_DAMP);
    previous.node.stop(at + STRING_DAMP * 8);
  }
  const node = new AudioBufferSourceNode(ctx, { buffer });
  const gain = new GainNode(ctx, { gain: velocity });
  const pan = new StereoPannerNode(ctx, { pan: (position * 2 - 1) * STRING_PAN });
  node.connect(gain).connect(pan).connect(bus);
  node.start(at, offset);
  node.addEventListener("ended", () => pan.disconnect());
  return { node, gain };
}