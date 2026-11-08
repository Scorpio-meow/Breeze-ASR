import { createMix, stringVoice } from "./mixer.js";
const root = document.documentElement;
const reduceMQ = matchMedia("(prefers-reduced-motion: reduce)");
const fineMQ = matchMedia("(hover: hover) and (pointer: fine)");
const STORE_KEY = "breeze-site:motion";
const motion = {
  paused: false,
  listeners: new Set(),
  init() {
    let stored = null;
    try {
      stored = localStorage.getItem(STORE_KEY);
    } catch { }
    this.paused = stored ? stored === "paused" : reduceMQ.matches;
    this.apply();
  },
  set(paused, persist) {
    this.paused = paused;
    if (persist) {
      try {
        localStorage.setItem(STORE_KEY, paused ? "paused" : "playing");
      } catch { }
    }
    this.apply();
    for (const fn of this.listeners) fn(paused);
  },
  apply() {
    if (this.paused) root.dataset.motion = "paused";
    else delete root.dataset.motion;
    for (const btn of document.querySelectorAll("[data-motion-toggle], [data-demo-toggle]")) {
      btn.setAttribute("aria-pressed", String(this.paused));
    }
  },
  on(fn) {
    this.listeners.add(fn);
  },
};
motion.init();
for (const btn of document.querySelectorAll("[data-motion-toggle], [data-demo-toggle]")) {
  btn.addEventListener("click", () => motion.set(!motion.paused, true));
}
const AUDIO_DIR = "audio/";
const STRING_DIR = "strings/";
const ONSET_LEVEL = 0.001;
const ONSET_LEAD = 0.002;
const BGM_TRACKS = [
  { file: "bgm-a.mp3", loopStart: 0.5, loopEnd: 75.5 },
  { file: "bgm-b.mp3", loopStart: 0.5, loopEnd: 80.5 },
];
const BGM_SEAM_FADE = 0.05;
const BGM_FADE_IN = 1.2;
const BGM_FADE_OUT = 0.6;
const SUSPEND_DELAY_MS = 900;
const SETTLE_FRAMES = 8;
const ZHENG_REPLUCK_MS = 70;
const SCRUB_SLOP = 6;
const GHOST_FLIP = 0.82;
const ZHENG_MIN_SPACING = 30;
const ZHENG_MIN_STRINGS = 5;
const ZHENG_DECAY_LOW = 1100;
const ZHENG_DECAY_HIGH = 520;
const ZHENG_FLOOR = 0.01;
const ZHENG_RAIL = 6;
const ZHENG_LABEL_BAND = 22;
const ZHENG_BRIDGE_LOW = 0.86;
const ZHENG_BRIDGE_HIGH = 0.4;
const ZHENG_AMP_RATIO = 0.3;
const ZHENG_AMP_MAX = 7;
const ZHENG_HZ_LOW = 7;
const ZHENG_HZ_HIGH = 15;
const ZHENG_WIDTH_LOW = 2.4;
const ZHENG_WIDTH_HIGH = 1;
const ZHENG_GLOW = 1.4;
const ZHENG_LABEL_LIT = 0.06;
const ZHENG_LABEL_ALL = 44;
const ZHENG_VEL_BASE = 0.35;
const ZHENG_VEL_GAIN = 0.45;
const ZHENG_TAP_VELOCITY = 0.7;
const ZHENG_KEY_VELOCITY = 0.75;
const ZHENG_GLISS_MS = 32;
const ZHENG_INVITE_VELOCITY = 0.4;
const ZHENG_INVITE_DELAY_MS = 500;
const ZHENG_INVITE_THRESHOLD = 0.6;
const sound = (() => {
  const wrap = document.querySelector("[data-sound]");
  const menuBtn = wrap.querySelector("[data-sound-menu]");
  const panel = document.getElementById(menuBtn.getAttribute("aria-controls"));
  const media = new Set();
  const zhengEl = document.querySelector("[data-zheng]");
  const stringCount = zhengEl.dataset.notes.split(" ").length;
  const strings = new Array(stringCount);
  const voices = new Array(stringCount);
  const zhengListeners = new Set();
  const music = { key: "breeze-site:music", on: false, btn: wrap.querySelector('[data-sound-toggle="music"]') };
  const zheng = { key: "breeze-site:zheng", on: false, btn: wrap.querySelector('[data-sound-toggle="zheng"]') };
  const channels = [music, zheng];
  let ctx = null;
  let mix = null;
  let bgmGain = null;
  let bgm = null;
  let musicLoading = null;
  let stringsLoading = null;
  let src = null;
  let srcStartedAt = 0;
  let srcOffset = 0;
  let track = null;
  let resumeAt = null;
  let pauseTimer = 0;
  let suspendTimer = 0;
  for (const ch of channels) {
    try {
      ch.on = localStorage.getItem(ch.key) === "on";
    } catch { }
  }
  const anyOn = () => music.on || zheng.on;
  function reflect() {
    for (const ch of channels) ch.btn.setAttribute("aria-pressed", String(ch.on));
    menuBtn.toggleAttribute("data-on", anyOn());
    for (const fn of zhengListeners) fn(zheng.on);
  }
  function persist(ch) {
    try {
      localStorage.setItem(ch.key, ch.on ? "on" : "off");
    } catch { }
  }
  function context() {
    if (!ctx) {
      ctx = new AudioContext({ latencyHint: "interactive" });
      mix = createMix(ctx);
      bgmGain = new GainNode(ctx, { gain: 0 });
      bgmGain.connect(mix.music);
    }
    if (ctx.state === "suspended") ctx.resume().catch(() => { });
  }
  async function fetchBuffer(file) {
    const res = await fetch(`${AUDIO_DIR}${file}`);
    if (!res.ok) throw new Error(`${file}: ${res.status}`);
    return ctx.decodeAudioData(await res.arrayBuffer());
  }
  function onset(buffer) {
    let first = buffer.length;
    for (let c = 0; c < buffer.numberOfChannels; c++) {
      const i = buffer.getChannelData(c).findIndex((v) => Math.abs(v) > ONSET_LEVEL);
      if (i !== -1) first = Math.min(first, i);
    }
    return Math.max(0, first / buffer.sampleRate - ONSET_LEAD);
  }
  function seamless(buffer) {
    const rate = buffer.sampleRate;
    const fade = Math.round(BGM_SEAM_FADE * rate);
    const head = Math.round(track.loopStart * rate) - fade;
    const tail = Math.round(track.loopEnd * rate) - fade;
    for (let c = 0; c < buffer.numberOfChannels; c++) {
      const data = buffer.getChannelData(c);
      for (let i = 0; i < fade; i++) data[tail + i] += (data[head + i] - data[tail + i]) * ((i + 1) / fade);
    }
    return buffer;
  }
  function loadStrings() {
    if (stringsLoading) return;
    stringsLoading = Promise.all(
      [...strings.keys()].map(async (i) => {
        const buffer = await fetchBuffer(`${STRING_DIR}string-${String(i + 1).padStart(2, "0")}.mp3`);
        strings[i] = { buffer, offset: onset(buffer) };
      }),
    ).catch((err) => {
      stringsLoading = null;
      console.warn("弦音載入失敗", err);
    });
  }
  function loadMusic() {
    if (musicLoading) return;
    if (!track) {
      track = BGM_TRACKS[Math.floor(Math.random() * BGM_TRACKS.length)];
      resumeAt = track.loopStart;
    }
    musicLoading = fetchBuffer(track.file)
      .then((buffer) => {
        bgm = seamless(buffer);
        sync();
      })
      .catch((err) => {
        musicLoading = null;
        console.warn("音樂載入失敗", err);
      });
  }
  function pluck(i, velocity, delay, position) {
    const item = strings[i];
    if (!zheng.on || document.hidden || !ctx || !item) return;
    const voice = stringVoice(ctx, mix.strings, item.buffer, item.offset, ctx.currentTime + delay, velocity, position, voices[i]);
    voices[i] = voice;
    voice.node.addEventListener("ended", () => {
      if (voices[i] === voice) voices[i] = null;
    });
  }
  function ramp(target, seconds) {
    const now = ctx.currentTime;
    bgmGain.gain.cancelScheduledValues(now);
    bgmGain.gain.setValueAtTime(bgmGain.gain.value, now);
    bgmGain.gain.linearRampToValueAtTime(target, now + seconds);
  }
  function position(time) {
    const p = srcOffset + (time - srcStartedAt);
    return p < track.loopEnd ? p : track.loopStart + ((p - track.loopStart) % (track.loopEnd - track.loopStart));
  }
  function bgmIn() {
    clearTimeout(pauseTimer);
    pauseTimer = 0;
    if (!src) {
      const now = ctx.currentTime;
      bgmGain.gain.cancelScheduledValues(now);
      bgmGain.gain.setValueAtTime(0, now);
      src = new AudioBufferSourceNode(ctx, { buffer: bgm, loop: true, loopStart: track.loopStart, loopEnd: track.loopEnd });
      src.connect(bgmGain);
      src.start(now, resumeAt);
      srcStartedAt = now;
      srcOffset = resumeAt;
    }
    ramp(1, BGM_FADE_IN);
  }
  function bgmOut() {
    if (!src || pauseTimer) return;
    ramp(0, BGM_FADE_OUT);
    pauseTimer = setTimeout(() => {
      pauseTimer = 0;
      resumeAt = position(ctx.currentTime);
      src.stop();
      src.disconnect();
      src = null;
    }, BGM_FADE_OUT * 1000);
  }
  function sync() {
    if (!ctx) return;
    clearTimeout(suspendTimer);
    if (anyOn() && !document.hidden) {
      if (ctx.state === "suspended") ctx.resume().catch(() => { });
    } else {
      suspendTimer = setTimeout(() => ctx.suspend(), SUSPEND_DELAY_MS);
    }
    if (!bgm) return;
    if (music.on && !document.hidden && media.size === 0) bgmIn();
    else bgmOut();
  }
  function wake() {
    if (!anyOn()) return;
    context();
    if (music.on) loadMusic();
    if (zheng.on) loadStrings();
  }
  function toggle(ch) {
    ch.on = !ch.on;
    persist(ch);
    reflect();
    wake();
    sync();
  }
  function gesture(e) {
    if (!anyOn() || navigator.userActivation?.isActive === false || e.target.closest?.("[data-sound]")) return;
    wake();
    sync();
  }
  function setOpen(open) {
    menuBtn.setAttribute("aria-expanded", String(open));
    panel.hidden = !open;
  }
  reflect();
  for (const ch of channels) ch.btn.addEventListener("click", () => toggle(ch));
  menuBtn.addEventListener("click", () => setOpen(panel.hidden));
  document.addEventListener("click", (e) => {
    if (!panel.hidden && !wrap.contains(e.target)) setOpen(false);
  });
  wrap.addEventListener("focusout", (e) => {
    if (!panel.hidden && e.relatedTarget && !wrap.contains(e.relatedTarget)) setOpen(false);
  });
  wrap.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden) {
      setOpen(false);
      menuBtn.focus();
    }
  });
  document.addEventListener("click", gesture, true);
  document.addEventListener("keydown", gesture, true);
  document.addEventListener("visibilitychange", sync);
  document.addEventListener(
    "play",
    (e) => {
      media.add(e.target);
      sync();
    },
    true,
  );
  for (const type of ["pause", "ended", "emptied"]) {
    document.addEventListener(
      type,
      (e) => {
        media.delete(e.target);
        sync();
      },
      true,
    );
  }
  return {
    pluck,
    zhengOn: () => zheng.on,
    enableZheng() {
      if (!zheng.on) toggle(zheng);
    },
    onZheng(fn) {
      zhengListeners.add(fn);
      fn(zheng.on);
    },
  };
})();
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const easeOutCubic = (t) => 1 - (1 - t) ** 3;
const easeOutExpo = (t) => (t >= 1 ? 1 : 1 - 2 ** (-10 * t));
function timecode(seconds) {
  const centis = Math.max(0, Math.round(seconds * 100));
  const h = Math.floor(centis / 360000);
  const m = Math.floor((centis % 360000) / 6000);
  const s = Math.floor((centis % 6000) / 100);
  const c = centis % 100;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(c).padStart(2, "0")}`;
}
function clock(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}
function parseTimecode(text) {
  const [h, m, s] = text.trim().split(":");
  return Number(h) * 3600 + Number(m) * 60 + Number(s);
}
const LATIN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
const CJK_POOL = [..."我們先確認這週的上線時程文件昨天更新了熱詞也加好那映像檔就下週發佈測試報告五前補上字幕匯出逐字稿午給大家版本一起對次進度聲音辨識標點說話者"];
const isCjk = (ch) => /[㐀-鿿]/.test(ch);
function scrambleText(target, progress, seed) {
  const chars = [...target];
  const solved = Math.floor(progress * chars.length);
  let out = "";
  for (let i = 0; i < chars.length; i++) {
    const ch = chars[i];
    if (i < solved || ch === " " || /[，。、：；,.\-—()（）]/.test(ch)) {
      out += ch;
      continue;
    }
    const n = Math.abs(Math.sin((seed + 1) * 12.9898 + i * 78.233) * 43758.5453) % 1;
    if (isCjk(ch)) out += CJK_POOL[Math.floor(n * CJK_POOL.length)];
    else if (/[A-Za-z]/.test(ch)) out += LATIN[Math.floor(n * LATIN.length)];
    else out += ch;
  }
  return out;
}
function withSrCopy(el) {
  if (el.dataset.srReady) return;
  const sr = document.createElement("span");
  sr.className = "sr";
  sr.textContent = el.textContent;
  el.after(sr);
  el.setAttribute("aria-hidden", "true");
  el.dataset.srReady = "1";
}
function animate(duration, step) {
  return new Promise((resolve) => {
    const start = performance.now();
    const tick = (now) => {
      const t = clamp((now - start) / duration, 0, 1);
      step(t);
      if (t < 1) requestAnimationFrame(tick);
      else resolve();
    };
    requestAnimationFrame(tick);
  });
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
function countUp(el) {
  const to = Number(el.dataset.countTo);
  const from = Number(el.dataset.countFrom);
  const decimals = Number(el.dataset.decimals);
  const suffix = el.dataset.suffix;
  const format = (v) => `${v.toFixed(decimals)}${suffix}`;
  withSrCopy(el);
  el.textContent = format(Math.max(from, to));
  el.style.minWidth = `${el.getBoundingClientRect().width}px`;
  if (motion.paused) {
    el.textContent = format(to);
    return;
  }
  animate(1500, (t) => {
    el.textContent = format(from + (to - from) * easeOutExpo(t));
  });
}
function scrambleIn(el, delay) {
  const target = el.textContent;
  withSrCopy(el);
  if (motion.paused) return;
  el.textContent = scrambleText(target, 0, 0);
  setTimeout(() => {
    let frame = 0;
    animate(900, (t) => {
      frame++;
      el.textContent = t >= 1 ? target : scrambleText(target, easeOutCubic(t), Math.floor(frame / 3));
    });
  }, delay);
}
function onReveal(el) {
  for (const node of el.querySelectorAll("[data-count-to]")) countUp(node);
  if (el.matches("[data-count-to]")) countUp(el);
  el.querySelectorAll("[data-scramble]").forEach((node, i) => scrambleIn(node, 250 + i * 260));
  const term = el.matches("[data-typewriter]") ? el : el.querySelector("[data-typewriter]");
  if (term) typewriter(term);
}
const revealObserver = new IntersectionObserver(
  (entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add("is-in");
      revealObserver.unobserve(entry.target);
      onReveal(entry.target);
    }
  },
  { rootMargin: "0px 0px -10% 0px", threshold: 0.15 },
);
for (const el of document.querySelectorAll("[data-reveal]")) revealObserver.observe(el);
root.classList.add("reveal-ready");
async function typewriter(panel) {
  if (panel.dataset.typed) return;
  panel.dataset.typed = "1";
  const pre = panel.querySelector(".term__pre");
  const cmd = pre.querySelector("[data-type-cmd]");
  const outs = [...pre.querySelectorAll(".out")];
  if (motion.paused) return;
  const full = cmd.textContent;
  panel.setAttribute("aria-busy", "true");
  pre.classList.add("is-typing");
  cmd.classList.add("is-caret");
  cmd.textContent = "";
  await wait(500);
  for (let i = 1; i <= full.length; i++) {
    if (motion.paused) break;
    cmd.textContent = full.slice(0, i);
    await wait(full[i - 1] === " " ? 34 : 16 + ((i * 7) % 11));
  }
  cmd.textContent = full;
  cmd.classList.remove("is-caret");
  const delays = [520, 650, 900, 1500, 260, 180];
  for (const [i, out] of outs.entries()) {
    if (!motion.paused) await wait(delays[i]);
    out.classList.add("is-shown");
  }
  pre.classList.remove("is-typing");
  panel.removeAttribute("aria-busy");
}
function heroDemo(frame) {
  const ed = frame.querySelector(".ed");
  const rows = [...ed.querySelectorAll(".ed-row")];
  const blocks = [...ed.querySelectorAll(".ed-block")];
  const segs = rows.map((row) => ({
    row,
    start: parseTimecode(row.querySelector(".t").textContent),
    end: parseTimecode(row.querySelector(".t2").textContent),
    text: row.querySelector(".x").textContent,
    spk: row.querySelector(".k").textContent,
    x: row.querySelector(".x"),
    blocks: blocks.filter((b) => b.dataset.seg === row.dataset.seg),
  }));
  const total = segs.at(-1).end;
  const tc = ed.querySelector("[data-tc]");
  const playhead = ed.querySelector("[data-playhead]");
  const status = ed.querySelector("[data-status]");
  const statusLabel = status.querySelector("b");
  const statusDetail = status.querySelector("span");
  const empty = ed.querySelector("[data-empty]");
  const count = ed.querySelector("[data-count]");
  const cueNo = ed.querySelector("[data-cue-no]");
  const cueStart = ed.querySelector("[data-cue-start]");
  const cueEnd = ed.querySelector("[data-cue-end]");
  const cueSpk = ed.querySelector("[data-cue-spk]");
  const finalLabel = statusLabel.textContent;
  const finalDetail = statusDetail.textContent;
  const finalCount = count.textContent;
  const RECOG = 2.8;
  const REVEAL = 2.1;
  const PLAY = 11;
  const HOLD = 1.8;
  const LOOP = RECOG + REVEAL + PLAY + HOLD;
  let elapsed = 0;
  let last = null;
  let visible = false;
  let raf = 0;
  let current = null;
  function setCurrent(index) {
    if (index === current) return;
    current = index;
    segs.forEach((s, i) => {
      s.row.classList.toggle("is-cur", i === index);
      s.row.classList.toggle("is-sel", i === index);
      for (const b of s.blocks) b.classList.toggle("is-cur", i === index);
    });
    if (index >= 0) {
      const s = segs[index];
      cueNo.textContent = `#${index + 1}`;
      cueStart.textContent = timecode(s.start);
      cueEnd.textContent = timecode(s.end);
      cueSpk.textContent = s.spk;
    }
  }
  function render(t) {
    if (t < RECOG) {
      frame.classList.add("is-busy");
      status.classList.add("is-busy");
      const est = 19;
      if (t < 1.9) {
        statusLabel.textContent = "辨識中";
        statusDetail.textContent = `${clock((t / 1.9) * est)} / 約 ${clock(est)}`;
      } else if (t < 2.35) {
        statusLabel.textContent = "加標點";
        statusDetail.textContent = "";
      } else {
        statusLabel.textContent = "分辨說話者";
        statusDetail.textContent = "";
      }
      empty.style.opacity = "1";
      count.textContent = "";
      for (const s of segs) {
        s.row.style.opacity = "0";
        s.row.style.translate = "0 6px";
      }
      for (const b of blocks) b.style.scale = "0 1";
      setCurrent(-1);
      playhead.style.setProperty("--at", "0");
      tc.textContent = timecode(0);
      return;
    }
    frame.classList.remove("is-busy");
    status.classList.remove("is-busy");
    statusLabel.textContent = finalLabel;
    statusDetail.textContent = finalDetail;
    count.textContent = finalCount;
    empty.style.opacity = "0";
    const r = t - RECOG;
    segs.forEach((s, i) => {
      const local = clamp((r - i * 0.17) / 0.32, 0, 1);
      s.row.style.opacity = String(easeOutCubic(local));
      s.row.style.translate = `0 ${(1 - easeOutCubic(local)) * 6}px`;
      const sp = clamp((r - i * 0.17) / 0.75, 0, 1);
      s.x.textContent = sp >= 1 ? s.text : scrambleText(s.text, easeOutCubic(sp), Math.floor(t * 22));
      for (const b of s.blocks) b.style.scale = `${easeOutCubic(clamp((r - 0.2 - i * 0.12) / 0.6, 0, 1))} 1`;
    });
    const p = clamp((t - RECOG - REVEAL) / PLAY, 0, 1);
    const audio = p * total;
    playhead.style.setProperty("--at", String(p));
    tc.textContent = timecode(audio);
    if (t < RECOG + REVEAL) {
      setCurrent(-1);
      return;
    }
    const index = segs.findIndex((s) => audio >= s.start && audio < s.end);
    setCurrent(index === -1 ? segs.length - 1 : index);
  }
  function tick(now) {
    if (last !== null) elapsed = (elapsed + (now - last) / 1000) % LOOP;
    last = now;
    render(elapsed);
    raf = requestAnimationFrame(tick);
  }
  function start() {
    if (raf || motion.paused || !visible) return;
    last = null;
    raf = requestAnimationFrame(tick);
  }
  function stop() {
    cancelAnimationFrame(raf);
    raf = 0;
  }
  function settle() {
    stop();
    elapsed = RECOG + REVEAL + PLAY * (segs[2].start + 0.6) / total;
    render(elapsed);
  }
  new IntersectionObserver((entries) => {
    visible = entries[0].isIntersecting;
    if (visible) start();
    else stop();
  }).observe(frame);
  motion.on((paused) => {
    if (paused) stop();
    else start();
  });
  if (motion.paused) settle();
  const tracks = ed.querySelector("[data-tracks]");
  const body = tracks.querySelector(".ed-wave__body");
  const ghost = tracks.querySelector("[data-ghost]");
  const ghostTc = ghost.querySelector("[data-ghost-tc]");
  let drag = null;
  const fractionAt = (clientX) => {
    const r = body.getBoundingClientRect();
    return clamp((clientX - r.left) / r.width, 0, 1);
  };
  function seek(audio) {
    elapsed = RECOG + REVEAL + PLAY * clamp(audio / total, 0, 1);
    render(elapsed);
  }
  function flash(row) {
    row.classList.remove("is-flash");
    void row.offsetWidth;
    row.classList.add("is-flash");
  }
  function jumpTo(index) {
    seek(segs[index].start);
    flash(segs[index].row);
  }
  for (const [i, s] of segs.entries()) s.row.addEventListener("click", () => jumpTo(i));
  for (const row of ed.querySelectorAll(".ed-row")) {
    row.addEventListener("animationend", () => row.classList.remove("is-flash"));
  }
  const hint = (seg, on) => {
    for (const el of ed.querySelectorAll(`[data-seg="${seg}"]`)) el.classList.toggle("is-hint", on);
  };
  ed.addEventListener("pointerover", (e) => {
    const el = e.target.closest("[data-seg]");
    if (el) hint(el.dataset.seg, true);
  });
  ed.addEventListener("pointerout", (e) => {
    const el = e.target.closest("[data-seg]");
    if (el && !el.contains(e.relatedTarget)) hint(el.dataset.seg, false);
  });
  function beginScrub(e) {
    drag.active = true;
    tracks.setPointerCapture(e.pointerId);
    tracks.classList.add("is-scrub");
    tracks.classList.remove("is-hover");
    stop();
    seek(fractionAt(e.clientX) * total);
  }
  function endScrub() {
    if (drag?.active) {
      tracks.classList.remove("is-scrub");
      start();
    }
    drag = null;
  }
  tracks.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    drag = { id: e.pointerId, x: e.clientX, active: false };
    if (e.pointerType !== "touch") beginScrub(e);
  });
  tracks.addEventListener("pointermove", (e) => {
    if (drag?.id === e.pointerId) {
      if (!drag.active && Math.abs(e.clientX - drag.x) > SCRUB_SLOP) beginScrub(e);
      if (drag.active) seek(fractionAt(e.clientX) * total);
      return;
    }
    if (e.pointerType !== "mouse") return;
    const f = fractionAt(e.clientX);
    tracks.classList.add("is-hover");
    ghost.style.setProperty("--at", f.toFixed(4));
    ghost.classList.toggle("is-flip", f > GHOST_FLIP);
    ghostTc.textContent = timecode(f * total);
  });
  tracks.addEventListener("pointerup", (e) => {
    if (drag?.id === e.pointerId && !drag.active) {
      seek(fractionAt(e.clientX) * total);
      drag = null;
      return;
    }
    endScrub();
  });
  tracks.addEventListener("pointercancel", endScrub);
  tracks.addEventListener("pointerleave", () => tracks.classList.remove("is-hover"));
  frame.addEventListener("keydown", (e) => {
    if (e.target !== frame || !["ArrowDown", "ArrowUp"].includes(e.key)) return;
    e.preventDefault();
    const at = current === null || current < 0 ? (e.key === "ArrowDown" ? -1 : segs.length) : current;
    jumpTo(clamp(at + (e.key === "ArrowDown" ? 1 : -1), 0, segs.length - 1));
  });
}
const demo = document.querySelector("[data-demo]");
if (demo) heroDemo(demo);
function story() {
  const list = document.querySelector("[data-steps]");
  const stage = document.querySelector("[data-stage]");
  if (!list || !stage) return;
  const steps = [...list.querySelectorAll(".step")];
  const stepNo = stage.querySelector("[data-stage-no]");
  const undo = stage.querySelector("[data-undo]");
  const typeEl = stage.querySelector("[data-type]");
  const narrow = matchMedia("(max-width: 899px)");
  let observer = null;
  let typed = false;
  function activate(n) {
    if (stage.dataset.active === String(n)) return;
    stage.dataset.active = String(n);
    stepNo.textContent = `0${n} / 0${steps.length}`;
    steps.forEach((s) => {
      const isOn = s.dataset.step === String(n);
      s.classList.toggle("is-on", isOn);
      const go = s.querySelector("[data-step-go]");
      if (isOn) go.setAttribute("aria-current", "step");
      else go.removeAttribute("aria-current");
    });
    if (n === 3) runTyping();
  }
  const lineAt = () => innerHeight * (narrow.matches ? 0.57 : 0.5);
  function offsetFor(step) {
    const r = step.getBoundingClientRect();
    const line = lineAt();
    if (!narrow.matches) return r.top + r.height / 2 - line;
    const wrap = stage.parentElement;
    const style = getComputedStyle(wrap);
    const cover = style.position === "sticky" ? parseFloat(style.top) + wrap.offsetHeight : document.querySelector("[data-nav]").offsetHeight;
    const gap = parseFloat(getComputedStyle(step).paddingTop) / 2;
    return r.top - Math.min(cover + gap, line - gap);
  }
  function activateAtLine() {
    const line = lineAt();
    const hit = steps.find((s) => {
      const r = s.getBoundingClientRect();
      return r.top <= line && r.bottom > line;
    });
    if (hit) activate(Number(hit.dataset.step));
  }
  function whenSettled(fn) {
    let last = NaN;
    let still = 0;
    const tick = () => {
      still = scrollY === last ? still + 1 : 0;
      last = scrollY;
      if (still >= SETTLE_FRAMES) fn();
      else requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
  for (const btn of list.querySelectorAll("[data-step-go]")) {
    btn.addEventListener("click", () => {
      const n = Number(btn.dataset.stepGo);
      const offset = offsetFor(steps[n - 1]);
      if (Math.abs(offset) < 2) {
        activate(n);
        return;
      }
      scrollTo({ top: scrollY + offset, behavior: motion.paused ? "instant" : "smooth" });
      whenSettled(activateAtLine);
    });
  }
  async function runTyping() {
    if (typed) return;
    typed = true;
    const from = typeEl.dataset.from;
    const to = typeEl.dataset.to;
    if (motion.paused) {
      typeEl.textContent = to;
      undo.classList.add("is-on");
      return;
    }
    typeEl.textContent = from;
    await wait(900);
    for (let i = from.length - 1; i >= 0; i--) {
      typeEl.textContent = from.slice(0, i);
      await wait(150);
    }
    await wait(250);
    for (let i = 1; i <= to.length; i++) {
      typeEl.textContent = to.slice(0, i);
      await wait(190);
    }
    undo.classList.add("is-on");
  }
  function observe() {
    observer?.disconnect();
    const margin = narrow.matches ? "-56% 0px -42% 0px" : "-46% 0px -46% 0px";
    observer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) if (e.isIntersecting) activate(Number(e.target.dataset.step));
      },
      { rootMargin: margin },
    );
    steps.forEach((s) => observer.observe(s));
  }
  observe();
  narrow.addEventListener("change", observe);
  steps[0].classList.add("is-on");
  return () => {
    const r = list.getBoundingClientRect();
    const p = clamp((lineAt() - r.top) / r.height, 0, 1);
    list.style.setProperty("--rail", p.toFixed(4));
    stage.style.setProperty("--rail", p.toFixed(4));
  };
}
const storyScroll = story();
const scrubFill = document.querySelector(".scrub__fill");
const nativeScrub = CSS.supports("(animation-timeline: scroll()) and (animation-range: 0% 100%)");
let scrollQueued = false;
function onScroll() {
  if (scrollQueued) return;
  scrollQueued = true;
  requestAnimationFrame(() => {
    scrollQueued = false;
    storyScroll?.();
    if (!nativeScrub && scrubFill) {
      const max = root.scrollHeight - innerHeight;
      scrubFill.style.setProperty("--p", max > 0 ? (scrollY / max).toFixed(4) : "0");
    }
  });
}
addEventListener("scroll", onScroll, { passive: true });
addEventListener("resize", onScroll);
onScroll();
function menu() {
  const btn = document.querySelector("[data-menu-toggle]");
  const panel = document.getElementById(btn.getAttribute("aria-controls"));
  const set = (open) => {
    if (panel.hasAttribute("data-open") === open) return;
    btn.setAttribute("aria-expanded", String(open));
    panel.toggleAttribute("data-open", open);
  };
  btn.addEventListener("click", () => set(btn.getAttribute("aria-expanded") !== "true"));
  panel.addEventListener("click", (e) => {
    if (e.target.closest("a")) set(false);
  });
  document.addEventListener("click", (e) => {
    if (btn.getAttribute("aria-expanded") === "true" && !panel.contains(e.target) && !btn.contains(e.target)) set(false);
  });
  addEventListener("keydown", (e) => {
    if (e.key === "Escape" && btn.getAttribute("aria-expanded") === "true") {
      set(false);
      btn.focus();
    }
  });
}
menu();
function navCurrent() {
  const links = new Map([...document.querySelectorAll(".nav__links a")].map((a) => [a.getAttribute("href").slice(1), a]));
  const observer = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        const link = links.get(e.target.id);
        if (!link) continue;
        if (e.isIntersecting) {
          for (const a of links.values()) a.removeAttribute("aria-current");
          link.setAttribute("aria-current", "true");
        } else if (link.getAttribute("aria-current")) {
          link.removeAttribute("aria-current");
        }
      }
    },
    { rootMargin: "-40% 0px -55% 0px" },
  );
  for (const id of links.keys()) {
    const section = document.getElementById(id);
    if (section) observer.observe(section);
  }
}
navCurrent();
function spotlight() {
  for (const group of document.querySelectorAll("[data-spotlight]")) {
    const cards = [...group.querySelectorAll(".card")];
    let queued = false;
    let point = null;
    group.addEventListener("pointermove", (e) => {
      if (!fineMQ.matches) return;
      point = e;
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        for (const card of cards) {
          const r = card.getBoundingClientRect();
          card.style.setProperty("--mx", `${point.clientX - r.left}px`);
          card.style.setProperty("--my", `${point.clientY - r.top}px`);
        }
      });
    });
  }
}
spotlight();
function magnetic() {
  for (const el of document.querySelectorAll("[data-magnetic]")) {
    el.addEventListener("pointermove", (e) => {
      if (!fineMQ.matches || motion.paused) return;
      const r = el.getBoundingClientRect();
      const dx = (e.clientX - (r.left + r.width / 2)) / (r.width / 2);
      const dy = (e.clientY - (r.top + r.height / 2)) / (r.height / 2);
      el.style.setProperty("--mx", `${(dx * 7).toFixed(2)}px`);
      el.style.setProperty("--my", `${(dy * 5).toFixed(2)}px`);
    });
    el.addEventListener("pointerleave", () => {
      el.style.setProperty("--mx", "0px");
      el.style.setProperty("--my", "0px");
    });
  }
}
magnetic();
function tabs() {
  for (const list of document.querySelectorAll("[data-tabs]")) {
    const items = [...list.querySelectorAll('[role="tab"]')];
    const select = (tab, focus) => {
      for (const t of items) {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        const panel = document.getElementById(t.getAttribute("aria-controls"));
        panel.hidden = !on;
        if (on && panel.matches("[data-typewriter]") && panel.closest(".is-in")) typewriter(panel);
      }
      if (focus) tab.focus();
    };
    items.forEach((tab, i) => {
      tab.addEventListener("click", () => select(tab, false));
      tab.addEventListener("keydown", (e) => {
        const keys = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: items.length - 1 };
        if (!(e.key in keys)) return;
        e.preventDefault();
        select(items[(keys[e.key] + items.length) % items.length], true);
      });
    });
    select(items.find((t) => t.getAttribute("aria-selected") === "true"), false);
  }
}
tabs();
function copyButtons() {
  const status = document.querySelector("[data-copy-status]");
  document.querySelectorAll("[data-copy]").forEach((btn, i) => {
    const code = btn.parentElement.querySelector("code");
    code.id = `cmd-${i + 1}`;
    btn.setAttribute("aria-describedby", code.id);
    const label = btn.textContent;
    btn.addEventListener("click", async () => {
      const text = btn.parentElement.querySelector("code").textContent;
      try {
        await navigator.clipboard.writeText(text);
        btn.textContent = "已複製";
        btn.classList.add("is-done");
        status.textContent = `已複製：${text}`;
        setTimeout(() => {
          btn.textContent = label;
          btn.classList.remove("is-done");
        }, 1800);
      } catch {
        status.textContent = "無法複製，請手動選取指令";
      }
    });
  });
}
copyButtons();
function barsChart() {
  const fig = document.querySelector(".bars");
  if (!fig) return;
  const chart = fig.querySelector("[data-bars-chart]");
  const table = fig.querySelector("[data-bars-table]");
  const tip = fig.querySelector("[data-bars-tip]");
  const [tipValue, tipName, tipMore] = tip.children;
  const buttons = [...fig.querySelectorAll("[data-bars-view]")];
  for (const btn of buttons) {
    btn.addEventListener("click", () => {
      for (const b of buttons) b.setAttribute("aria-pressed", String(b === btn));
      chart.hidden = btn.dataset.barsView !== "chart";
      table.hidden = btn.dataset.barsView !== "table";
    });
  }
  const hide = () => {
    tip.hidden = true;
  };
  for (const row of fig.querySelectorAll(".bars__row")) {
    const value = row.querySelector(".bars__val").textContent;
    row.setAttribute("aria-label", `${row.dataset.tipName}，速度 ${value}，${row.dataset.tipMore}`);
    const show = () => {
      tipValue.textContent = value;
      tipName.textContent = row.dataset.tipName;
      tipMore.textContent = row.dataset.tipMore;
      tip.hidden = false;
      const box = chart.getBoundingClientRect();
      const plot = row.querySelector(".bars__plot").getBoundingClientRect();
      const end = Number(getComputedStyle(row).getPropertyValue("--end"));
      const left = Math.min(plot.left - box.left + plot.width * end + 12, box.width - tip.offsetWidth);
      tip.style.left = `${Math.max(0, left)}px`;
      tip.style.top = `${row.getBoundingClientRect().top - box.top - tip.offsetHeight - 6}px`;
    };
    row.addEventListener("pointerenter", show);
    row.addEventListener("pointerleave", hide);
    row.addEventListener("focus", show);
    row.addEventListener("blur", hide);
  }
  fig.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hide();
  });
}
barsChart();
function film() {
  const frame = document.querySelector("[data-film]");
  if (!frame) return;
  const video = frame.querySelector("[data-film-video]");
  const cover = frame.querySelector("[data-film-play]");
  video.inert = true;
  cover.addEventListener("click", () => {
    cover.hidden = true;
    video.inert = false;
    video.focus();
    video.play().catch(() => {
      cover.hidden = false;
      video.inert = true;
    });
  });
}
film();
function zheng(root) {
  const canvas = root.querySelector("canvas");
  const g = canvas.getContext("2d");
  const notes = root.dataset.notes.split(" ");
  const range = document.querySelector("[data-zheng-range]");
  const hint = document.querySelector("[data-zheng-hint]");
  const all = notes.map((note, index) => ({ note, index, frac: index / (notes.length - 1), x: 0, v: 0, t0: -Infinity }));
  let shown = [];
  let width = 0;
  let height = 0;
  let spacing = 0;
  let colors = null;
  let focus = null;
  let raf = 0;
  let pointer = null;
  function readColors() {
    const cs = getComputedStyle(root);
    const v = (name) => cs.getPropertyValue(name).trim();
    colors = { string: v("--zheng-string"), hot: v("--zheng-hot"), rail: v("--zheng-rail"), bridge: v("--zheng-bridge"), label: v("--zheng-label"), font: `500 10px ${v("--mono")}` };
  }
  const energy = (s, now) => (now < s.t0 ? 0 : s.v * Math.exp(-(now - s.t0) / (ZHENG_DECAY_LOW - (ZHENG_DECAY_LOW - ZHENG_DECAY_HIGH) * s.frac)));
  function draw(now) {
    g.clearRect(0, 0, width, height);
    const top = ZHENG_RAIL;
    const bottom = height - ZHENG_LABEL_BAND;
    const still = motion.paused;
    const focused = root.matches(":focus-visible");
    let alive = false;
    g.lineWidth = 1;
    g.strokeStyle = colors.rail;
    g.beginPath();
    g.moveTo(0, top - 2.5);
    g.lineTo(width, top - 2.5);
    g.moveTo(0, bottom + 2.5);
    g.lineTo(width, bottom + 2.5);
    g.stroke();
    g.font = colors.font;
    g.textAlign = "center";
    for (const s of shown) {
      const e = energy(s, now);
      if (s.t0 > now || e > ZHENG_FLOOR) alive = true;
      const yb = top + (bottom - top) * (ZHENG_BRIDGE_LOW - (ZHENG_BRIDGE_LOW - ZHENG_BRIDGE_HIGH) * s.frac);
      const amp = still ? 0 : e * Math.min(spacing * ZHENG_AMP_RATIO, ZHENG_AMP_MAX);
      const d = amp * Math.sin((2 * Math.PI * (ZHENG_HZ_LOW + (ZHENG_HZ_HIGH - ZHENG_HZ_LOW) * s.frac) * (now - s.t0)) / 1000);
      g.lineWidth = ZHENG_WIDTH_LOW - (ZHENG_WIDTH_LOW - ZHENG_WIDTH_HIGH) * s.frac;
      for (const [color, alpha] of [
        [colors.string, 1],
        [colors.hot, Math.min(1, e * ZHENG_GLOW)],
      ]) {
        if (alpha <= ZHENG_FLOOR) continue;
        g.globalAlpha = alpha;
        g.strokeStyle = color;
        g.beginPath();
        g.moveTo(s.x, top);
        g.quadraticCurveTo(s.x + d * 2, (top + yb) / 2, s.x, yb);
        g.lineTo(s.x, bottom);
        g.stroke();
      }
      g.globalAlpha = 1;
      g.fillStyle = colors.bridge;
      g.beginPath();
      g.moveTo(s.x - 5, yb + 6);
      g.lineTo(s.x, yb - 1);
      g.lineTo(s.x + 5, yb + 6);
      g.closePath();
      g.fill();
      const lit = e > ZHENG_LABEL_LIT;
      if (lit || s.note.startsWith("D") || spacing >= ZHENG_LABEL_ALL || (focused && s === focus)) {
        g.fillStyle = lit ? colors.hot : colors.label;
        g.fillText(s.note, s.x, height - 6);
      }
      if (focused && s === focus) {
        g.fillStyle = colors.hot;
        g.fillRect(s.x - 8, height - 2, 16, 2);
      }
    }
    return alive;
  }
  function frame(now) {
    raf = 0;
    if (draw(now)) raf = requestAnimationFrame(frame);
  }
  function kick() {
    if (!raf) raf = requestAnimationFrame(frame);
  }
  function layout() {
    const r = root.getBoundingClientRect();
    width = r.width;
    height = r.height;
    const dpr = devicePixelRatio;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = clamp(Math.floor(width / ZHENG_MIN_SPACING), ZHENG_MIN_STRINGS, notes.length);
    const from = Math.floor((notes.length - n) / 2);
    shown = all.slice(from, from + n);
    spacing = width / n;
    shown.forEach((s, k) => {
      s.x = spacing * (k + 0.5);
    });
    if (!shown.includes(focus)) focus = shown[Math.floor(n / 2)];
    range.textContent = `D 五聲定弦・${shown[0].note}–${shown.at(-1).note}・${n} 弦`;
    draw(performance.now());
  }
  function vibrate(s, velocity, delay) {
    const at = performance.now() + delay;
    if (at - s.t0 < ZHENG_REPLUCK_MS) return false;
    s.t0 = at;
    s.v = velocity;
    kick();
    return true;
  }
  function pluck(s, velocity, delay) {
    if (!vibrate(s, velocity, delay)) return;
    sound.pluck(s.index, velocity, delay / 1000, s.x / width);
    if (!sound.zhengOn()) hint.hidden = false;
  }
  function sweep(x0, t0, x1, t1) {
    if (x0 === x1) return;
    const dt = Math.max(1, t1 - t0);
    const velocity = clamp(ZHENG_VEL_BASE + (Math.abs(x1 - x0) / dt) * ZHENG_VEL_GAIN, ZHENG_VEL_BASE, 1);
    const lo = Math.min(x0, x1);
    const hi = Math.max(x0, x1);
    for (const s of shown) if (s.x > lo && s.x <= hi) pluck(s, velocity, ((s.x - x0) / (x1 - x0)) * dt);
  }
  const nearest = (x) => shown.reduce((a, b) => (Math.abs(b.x - x) < Math.abs(a.x - x) ? b : a));
  root.addEventListener("pointerdown", (e) => {
    const x = e.clientX - canvas.getBoundingClientRect().left;
    pointer = { id: e.pointerId, x, t: e.timeStamp, x0: x, y0: e.clientY, touch: e.pointerType === "touch", moved: false };
    if (pointer.touch) return;
    focus = nearest(x);
    pluck(focus, ZHENG_TAP_VELOCITY, 0);
  });
  root.addEventListener("pointermove", (e) => {
    if (e.pointerType === "touch" && pointer?.id !== e.pointerId) return;
    const rect = canvas.getBoundingClientRect();
    const list = e.getCoalescedEvents ? e.getCoalescedEvents() : [];
    for (const ev of list.length ? list : [e]) {
      const x = ev.clientX - rect.left;
      if (pointer?.id === e.pointerId) {
        if (Math.abs(x - pointer.x0) > SCRUB_SLOP || Math.abs(ev.clientY - pointer.y0) > SCRUB_SLOP) pointer.moved = true;
        sweep(pointer.x, pointer.t, x, ev.timeStamp);
        pointer.x = x;
        pointer.t = ev.timeStamp;
      } else {
        pointer = { id: e.pointerId, x, t: ev.timeStamp, x0: x, y0: ev.clientY, touch: false, moved: false };
      }
    }
  });
  root.addEventListener("pointerup", (e) => {
    if (pointer?.touch && pointer.id === e.pointerId && !pointer.moved) pluck(nearest(pointer.x0), ZHENG_TAP_VELOCITY, 0);
    if (pointer?.touch) pointer = null;
  });
  root.addEventListener("pointercancel", () => {
    pointer = null;
  });
  root.addEventListener("pointerleave", (e) => {
    if (e.pointerType !== "touch") pointer = null;
  });
  root.addEventListener("keydown", (e) => {
    const k = shown.indexOf(focus);
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      focus = shown[clamp(k + (e.key === "ArrowRight" ? 1 : -1), 0, shown.length - 1)];
      pluck(focus, ZHENG_KEY_VELOCITY, 0);
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      const to = e.key === "End" ? shown.length - 1 : 0;
      const dir = Math.sign(to - k);
      for (let i = k, n = 0; dir !== 0 && i !== to + dir; i += dir, n++) pluck(shown[i], ZHENG_KEY_VELOCITY, n * ZHENG_GLISS_MS);
      focus = shown[to];
      if (dir === 0) pluck(focus, ZHENG_KEY_VELOCITY, 0);
    } else if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      pluck(focus, ZHENG_KEY_VELOCITY, 0);
    }
    kick();
  });
  root.addEventListener("focus", kick);
  root.addEventListener("blur", kick);
  document.querySelector("[data-zheng-sound]").addEventListener("click", () => {
    sound.enableZheng();
    root.focus();
  });
  sound.onZheng((on) => {
    if (on) hint.hidden = true;
  });
  new IntersectionObserver(
    (entries, io) => {
      if (!entries[0].isIntersecting) return;
      io.disconnect();
      if (motion.paused) return;
      setTimeout(() => shown.forEach((s, k) => vibrate(s, ZHENG_INVITE_VELOCITY, k * ZHENG_GLISS_MS)), ZHENG_INVITE_DELAY_MS);
    },
    { threshold: ZHENG_INVITE_THRESHOLD },
  ).observe(root);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    readColors();
    kick();
  });
  motion.on(kick);
  readColors();
  new ResizeObserver(layout).observe(root);
}
const zhengEl = document.querySelector("[data-zheng]");
if (zhengEl) zheng(zhengEl);