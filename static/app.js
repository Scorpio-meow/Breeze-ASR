const $ = (id) => document.getElementById(id);

const MODEL_STATE_LABELS = {
  waiting: "等待準備",
  downloading: "下載中",
  converting: "轉換中",
  prepared: "待載入",
  loading: "載入中",
  ready: "已就緒",
  failed: "無法使用",
};

const STAGE_LABELS = {
  preparing: "準備模型",
  loading: "載入模型",
  transcribing: "辨識中",
  diarizing: "分辨說話者",
};

const CANDIDATE_STATUS = { ok: "可用", rejected: "錯誤率太高", failed: "無法執行" };

const STORAGE_KEYS = { model: "breeze-asr:model", view: "breeze-asr:view", speakers: "breeze-asr:speakers" };
const SPEAKER_COLORS = 8;
const CONFIRM_MS = 5000;
const CONFIG_REFRESH_MS = 4000;
const JOB_POLL_MS = 1000;
const WAVEFORM_MAX_SECONDS = 1800;
const WAVEFORM_MAX_BYTES = 512 * 1024 * 1024;
const WAVEFORM_SAMPLE_RATE = 8000;
const WAVEFORM_BUCKETS = 4000;
const RULER_MIN_GAP_PX = 72;
const SEEK_STEP_SECONDS = 5;
const SEEK_PAGE_SECONDS = 30;
const RULER_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];

const state = {
  config: null,
  audio: null,
  previewUrl: null,
  job: null,
  result: null,
  pollTimer: null,
  segments: [],
  speakers: [],
  activeIndex: -1,
  recording: null,
  uploading: false,
  peaks: null,
  waveToken: 0,
  frame: 0,
};

const nameSaves = new WeakMap();

function storageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(key, value) {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function announce(message) {
  const node = $("announcer");
  node.textContent = "";
  requestAnimationFrame(() => {
    node.textContent = message;
  });
}

function clock(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function timecode(seconds) {
  const centis = Math.max(0, Math.round(seconds * 100));
  const h = Math.floor(centis / 360000);
  const m = Math.floor((centis % 360000) / 6000);
  const s = Math.floor((centis % 6000) / 100);
  const c = centis % 100;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(c).padStart(2, "0")}`;
}

function duration(seconds) {
  const total = Math.round(seconds);
  if (total < 60) return `${total} 秒`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m < 60) return s ? `${m} 分 ${s} 秒` : `${m} 分`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} 小時 ${m % 60} 分` : `${h} 小時`;
}

function bytes(n) {
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

function uploadLimit(mb) {
  return mb >= 1024 && mb % 1024 === 0 ? `${mb / 1024} GB` : `${mb} MB`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(url, options = {}) {
  const init = { method: options.method ?? "GET", cache: "no-store" };
  if (options.body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(options.body);
  }
  const res = await fetch(url, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
  return data;
}

function twoStepButton(button, confirmLabel, onConfirm) {
  const label = button.textContent;
  let timer = 0;
  const disarm = () => {
    button.textContent = label;
    button.setAttribute("aria-pressed", "false");
  };
  button.setAttribute("aria-pressed", "false");
  button.addEventListener("click", () => {
    clearTimeout(timer);
    if (button.getAttribute("aria-pressed") !== "true") {
      button.textContent = confirmLabel;
      button.setAttribute("aria-pressed", "true");
      timer = setTimeout(disarm, CONFIRM_MS);
      return;
    }
    disarm();
    onConfirm();
  });
}

function setStatus(line, detail, tone) {
  const node = $("status-line");
  node.textContent = line;
  if (tone) node.dataset.tone = tone;
  else delete node.dataset.tone;
  $("status-detail").textContent = detail ?? "";
}

function setEta(ratio) {
  $("eta-wrap").hidden = ratio === null;
  if (ratio !== null) $("eta").style.transform = `scaleX(${Math.min(1, Math.max(0, ratio))})`;
}

async function refreshConfig() {
  let config;
  try {
    const res = await fetch("/api/config", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    config = await res.json();
  } catch {
    if (!state.config) setStatus("無法連線到服務", "", "error");
    return;
  }
  const first = !state.config;
  state.config = config;
  if (first) {
    renderModels(config);
    renderStaticInfo(config);
    renderSpeakersOption(config.speakers);
  } else {
    updateModelStates(config);
  }
  renderHardware(config.hardware);
  renderNotice(config);
}

function selectedModelId() {
  return $("model-select").value || null;
}

function modelById(id) {
  return state.config?.models.find((m) => m.id === id) || null;
}

function renderModels(config) {
  const select = $("model-select");
  select.replaceChildren(...config.models.map((model) => new Option(model.name, model.id)));
  const remembered = storageGet(STORAGE_KEYS.model);
  select.value = config.models.some((m) => m.id === remembered) ? remembered : config.default_model;
  select.addEventListener("change", () => {
    storageSet(STORAGE_KEYS.model, select.value);
    updateModelStatus();
  });
  updateModelStates(config);
}

function updateModelStates(config) {
  const select = $("model-select");
  for (const model of config.models) {
    const option = [...select.options].find((o) => o.value === model.id);
    if (!option) continue;
    const label = MODEL_STATE_LABELS[model.state] || model.state;
    option.textContent = model.state === "ready" ? model.name : `${model.name}（${label}）`;
    option.title = model.backend === "remote" ? `${model.description}\n${model.id}（轉呼叫既有服務）` : `${model.description}\n${model.id}`;
  }
  updateModelStatus();
}

function updateModelStatus() {
  const model = modelById(selectedModelId());
  const node = $("model-status");
  if (!model) {
    node.textContent = "";
    return;
  }
  $("model-select").title = `${model.tagline}\n${model.description}`;
  node.textContent = model.error
    ? `${model.name}：${model.error}`
    : `${model.name} ${MODEL_STATE_LABELS[model.state] || model.state}`;
  node.dataset.tone = model.error ? "error" : model.state === "ready" ? "" : "busy";
}

function renderStaticInfo(config) {
  $("open-btn").title =
    `mp3、wav、m4a、flac、ogg、webm、mp4\n上限 ${uploadLimit(config.max_upload_mb)}、${duration(config.max_audio_seconds)}`;
}

function renderSpeakersOption(info) {
  $("speakers-group").hidden = !info;
  $("voices-btn").hidden = !info;
  if (!info) return;
  const select = $("speakers-select");
  for (let n = 2; n <= info.max_speakers; n += 1) select.append(new Option(`${n} 人`, String(n)));
  const remembered = storageGet(STORAGE_KEYS.speakers);
  if ([...select.options].some((option) => option.value === remembered)) select.value = remembered;
  select.addEventListener("change", () => storageSet(STORAGE_KEYS.speakers, select.value));
  loadVoices();
}

function selectedSpeakers() {
  return $("speakers-group").hidden ? "off" : $("speakers-select").value;
}

function renderNotice(config) {
  const hardware = config.hardware;
  const speakers = config.speakers;
  let text = "";
  let tone = "";
  let title = "";
  if (hardware.state === "tuning") {
    const count = hardware.total ? `（${hardware.done}／${hardware.total}）` : "";
    text = `自動調校：${hardware.step ?? ""}${count}`;
    title = "正在為這台電腦挑選最快的設定，約 1–3 分鐘；第一次還要下載模型";
    tone = "busy";
  } else if (hardware.state === "failed") {
    text = `硬體設定失敗：${hardware.error}`;
    title = `設定檔：${hardware.path}`;
    tone = "error";
  } else if (speakers?.state === "downloading") {
    text = "下載說話者模型";
    tone = "busy";
  } else if (speakers?.state === "failed") {
    text = `說話者模型：${speakers.error}`;
    tone = "error";
  }
  const node = $("notice");
  node.textContent = text;
  node.title = title;
  node.dataset.tone = tone;
  node.hidden = !text;
}

function hardwareLabel(hardware) {
  switch (hardware.state) {
    case "ready":
      return hardware.summary;
    case "tuning":
      return "自動調校中";
    case "failed":
      return "硬體設定失敗";
    case "not_needed":
      return "外部服務";
    default:
      return "啟動中";
  }
}

function renderHardware(hardware) {
  $("runtime").textContent = hardwareLabel(hardware);
  $("hardware-btn").hidden = !(hardware.state === "ready" || hardware.state === "failed");
  $("retune-btn").disabled = hardware.state === "tuning";
  if (hardware.state !== "ready") {
    $("hardware-summary").textContent = hardware.state === "failed" ? "設定失敗" : "";
    $("hardware-facts").replaceChildren();
    $("hardware-tuning").hidden = true;
    $("hardware-manual").hidden = true;
    return;
  }
  $("hardware-summary").textContent = hardware.summary;
  renderHardwareFacts(hardware);
  const tuning = hardware.tuning;
  $("hardware-tuning").hidden = !tuning;
  $("hardware-manual").hidden = !!tuning;
  if (tuning) renderTuning(tuning, hardware.settings);
}

function renderProps(list, pairs) {
  list.replaceChildren(
    ...pairs.map(([term, value]) => {
      const row = el("div", "");
      row.append(el("dt", "", term), el("dd", "", value));
      return row;
    }),
  );
}

function renderHardwareFacts(hardware) {
  const facts = [
    ["設定", hardware.summary],
    ["常駐模型", `${hardware.settings.max_loaded_models} 個`],
  ];
  const info = hardware.tuning?.hardware;
  if (info) {
    facts.push(["處理器", `${info.cpu}（${info.usable_threads} 執行緒）`]);
    facts.push(["記憶體", `${info.memory_total_gb} GB`]);
    facts.push(["調校時間", new Date(hardware.tuning.generated_at).toLocaleString("zh-TW")]);
  }
  facts.push(["設定檔", hardware.path]);
  renderProps($("hardware-facts"), facts);
}

function benchRow(item, settings) {
  const chosen =
    item.engine === settings.engine &&
    item.device === settings.device &&
    item.weight_format === settings.weight_format &&
    item.torch_dtype === settings.torch_dtype;
  const row = el("tr", chosen ? "is-chosen" : "");
  const name = el("td", "");
  name.append(el("span", "", item.label));
  if (chosen) name.append(el("span", "chosen-badge", "使用中"));
  if (!chosen && item.transcribe_seconds !== undefined) {
    const use = el("button", "link-btn", "改用");
    use.type = "button";
    twoStepButton(use, "確定改用", () => useCandidate(item, use));
    name.append(use);
  }
  if (item.reason) name.append(el("span", "bench-reason", item.reason));
  row.append(
    name,
    el("td", "", CANDIDATE_STATUS[item.status] ?? item.status),
    el("td", "num", item.rtf !== undefined ? item.rtf.toFixed(2) : "—"),
    el("td", "num", item.cer !== undefined ? `${(item.cer * 100).toFixed(1)}%` : "—"),
    el("td", "num", item.memory_bytes ? `${(item.memory_bytes / 1024 ** 3).toFixed(1)} GB` : "—"),
  );
  return row;
}

async function useCandidate(item, button) {
  button.disabled = true;
  try {
    await api("/api/hardware/settings", { method: "POST", body: { label: item.label } });
    announce(`改用 ${item.label}，重新載入模型中`);
    refreshConfig();
  } catch (err) {
    $("retune-hint").textContent = `無法改用：${err.message}`;
    announce(`無法改用：${err.message}`);
    button.disabled = false;
  }
}

function renderTuning(tuning, settings) {
  const rows = $("bench-rows");
  const key = JSON.stringify([tuning.candidates, settings]);
  if (rows.dataset.key !== key) {
    rows.dataset.key = key;
    rows.replaceChildren(...tuning.candidates.map((item) => benchRow(item, settings)));
  }
  $("hardware-reasons").replaceChildren(...tuning.reasons.map((text) => el("li", "", text)));
  $("hardware-skipped").replaceChildren(
    ...tuning.skipped.map((item) => {
      const li = el("li", "");
      li.append(el("strong", "", item.label), `：${item.reason}`);
      return li;
    }),
  );
}

function bindRetune() {
  const button = $("retune-btn");
  const hint = $("retune-hint");
  const original = hint.textContent;
  const label = button.textContent;
  let armed = false;
  let disarmTimer = 0;
  const disarm = () => {
    armed = false;
    button.textContent = label;
    hint.textContent = original;
  };
  button.addEventListener("click", async () => {
    if (!armed) {
      armed = true;
      button.textContent = "確定重新調校";
      hint.textContent = "再按一次開始";
      clearTimeout(disarmTimer);
      disarmTimer = setTimeout(disarm, CONFIRM_MS);
      return;
    }
    clearTimeout(disarmTimer);
    armed = false;
    button.textContent = label;
    button.disabled = true;
    hint.textContent = "重新啟動推論行程…";
    try {
      await api("/api/hardware/retune", { method: "POST" });
      hint.textContent = original;
      announce("開始重新調校");
      refreshConfig();
    } catch (err) {
      hint.textContent = `無法重新調校：${err.message}`;
      button.disabled = false;
    }
  });
}

function bindDialogs() {
  $("voices-btn").addEventListener("click", () => {
    loadVoices();
    $("voices-dialog").showModal();
  });
  $("hardware-btn").addEventListener("click", () => $("hardware-dialog").showModal());
  for (const dialog of document.querySelectorAll("dialog")) {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  }
}

function audioEl() {
  return $("audio-preview");
}

function timelineDuration() {
  const audio = audioEl();
  if (state.audio && Number.isFinite(audio.duration) && audio.duration > 0) return audio.duration;
  if (state.audio?.seconds) return state.audio.seconds;
  if (previewHoldsJobAudio()) return state.job.audioSeconds ?? 0;
  return 0;
}

function previewHoldsJobAudio() {
  return !!state.job && !!state.audio && state.audio.blob === state.job.blob;
}

function setAudio(blob, name) {
  const limit = state.config ? state.config.max_upload_mb * 1024 * 1024 : null;
  if (limit !== null && blob.size > limit) {
    setStatus(`檔案 ${bytes(blob.size)} 超過上限 ${uploadLimit(state.config.max_upload_mb)}`, "", "error");
    return;
  }
  if (state.uploading) return setStatus("上傳中，請等上傳完成再換音訊", "", "error");
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.audio = { blob, name };
  state.previewUrl = URL.createObjectURL(blob);
  audioEl().src = state.previewUrl;
  $("selected-name").textContent = name;
  $("selected-size").textContent = bytes(blob.size);
  $("clear-audio").hidden = false;
  if (!state.job || state.job.done) setStatus("已載入音訊", name);
  state.peaks = null;
  setActive(-1);
  loadPeaks(blob);
  refreshTimeline();
  updateSubmit();
}

function updateSubmit() {
  const busy = !!state.job && !state.job.done && state.job.blob === state.audio?.blob;
  $("submit-btn").disabled = !!state.recording || state.uploading || busy;
}

function clearAudio() {
  if (state.uploading) return setStatus("上傳中，請等上傳完成再關閉音訊", "", "error");
  const audio = audioEl();
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.audio = null;
  state.previewUrl = null;
  state.peaks = null;
  state.waveToken += 1;
  updateSubmit();
  $("file-input").value = "";
  $("selected-name").textContent = "未載入音訊";
  $("selected-size").textContent = "";
  $("clear-audio").hidden = true;
  $("wave-note").textContent = "";
  setActive(-1);
  refreshTimeline();
}

function bindUpload() {
  const input = $("file-input");
  input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (file) setAudio(file, file.name);
  });
  const carriesFiles = (event) => [...(event.dataTransfer?.types ?? [])].includes("Files");
  let depth = 0;
  const reset = () => {
    depth = 0;
    document.body.classList.remove("is-dragover");
  };
  document.addEventListener("dragenter", (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    depth += 1;
    document.body.classList.add("is-dragover");
  });
  document.addEventListener("dragover", (event) => {
    if (carriesFiles(event)) event.preventDefault();
  });
  document.addEventListener("dragleave", (event) => {
    if (!carriesFiles(event)) return;
    depth -= 1;
    if (depth <= 0) reset();
  });
  document.addEventListener("drop", (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    reset();
    if (state.recording) return setStatus("請先停止錄音", "", "error");
    const file = event.dataTransfer.files[0];
    if (file) setAudio(file, file.name);
  });
  $("clear-audio").addEventListener("click", clearAudio);
}

function waitForMetadata(audio) {
  if (Number.isFinite(audio.duration)) return Promise.resolve(true);
  return new Promise((resolve) => {
    const done = (ok) => {
      audio.removeEventListener("loadedmetadata", onLoad);
      audio.removeEventListener("error", onError);
      resolve(ok);
    };
    const onLoad = () => done(true);
    const onError = () => done(false);
    audio.addEventListener("loadedmetadata", onLoad);
    audio.addEventListener("error", onError);
  });
}

async function loadPeaks(blob) {
  const token = ++state.waveToken;
  const note = $("wave-note");
  note.textContent = "讀取波形…";
  drawWaveform();
  const ok = await waitForMetadata(audioEl());
  if (token !== state.waveToken) return;
  if (!ok) {
    note.textContent = "無法讀取音訊";
    return;
  }
  if (blob.size > WAVEFORM_MAX_BYTES) {
    note.textContent = `檔案超過 ${bytes(WAVEFORM_MAX_BYTES)}，送出辨識後顯示波形`;
    return;
  }
  const length = audioEl().duration;
  if (Number.isFinite(length) && length > WAVEFORM_MAX_SECONDS) {
    note.textContent = `超過 ${duration(WAVEFORM_MAX_SECONDS)}，送出辨識後顯示波形`;
    return;
  }
  try {
    const data = await blob.arrayBuffer();
    const context = new OfflineAudioContext(1, 1, WAVEFORM_SAMPLE_RATE);
    const buffer = await context.decodeAudioData(data);
    if (token !== state.waveToken) return;
    state.audio.seconds = buffer.duration;
    state.peaks = computePeaks(buffer);
    note.textContent = "";
  } catch {
    if (token !== state.waveToken) return;
    state.peaks = null;
    note.textContent = "瀏覽器無法解碼，送出辨識後顯示波形";
  }
  refreshTimeline();
}

function applyServerPeaks(job, blob) {
  if (state.peaks || state.audio?.blob !== blob || !job.waveform?.length) return;
  state.waveToken += 1;
  state.peaks = Float32Array.from(job.waveform);
  state.audio.seconds = job.audio_seconds;
  $("wave-note").textContent = "";
  refreshTimeline();
}

function computePeaks(buffer) {
  const peaks = new Float32Array(WAVEFORM_BUCKETS);
  const length = buffer.length;
  const channels = [];
  for (let c = 0; c < buffer.numberOfChannels; c += 1) channels.push(buffer.getChannelData(c));
  let max = 0;
  for (let b = 0; b < WAVEFORM_BUCKETS; b += 1) {
    const from = Math.floor((b / WAVEFORM_BUCKETS) * length);
    const to = Math.max(from + 1, Math.floor(((b + 1) / WAVEFORM_BUCKETS) * length));
    let peak = 0;
    for (const data of channels) {
      for (let i = from; i < to && i < length; i += 1) {
        const v = Math.abs(data[i]);
        if (v > peak) peak = v;
      }
    }
    peaks[b] = peak;
    if (peak > max) max = peak;
  }
  if (max > 0) for (let b = 0; b < WAVEFORM_BUCKETS; b += 1) peaks[b] /= max;
  return peaks;
}

function drawWaveform() {
  const canvas = $("waveform");
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio;
  canvas.width = Math.max(1, Math.round(rect.width * ratio));
  canvas.height = Math.max(1, Math.round(rect.height * ratio));
  const g = canvas.getContext("2d");
  g.clearRect(0, 0, canvas.width, canvas.height);
  const peaks = state.peaks;
  if (!peaks) return;
  g.fillStyle = getComputedStyle(canvas).color;
  const w = canvas.width;
  const h = canvas.height;
  const mid = h / 2;
  const step = Math.max(1, Math.round(ratio));
  for (let x = 0; x < w; x += step) {
    const from = Math.floor((x / w) * peaks.length);
    const to = Math.max(from + 1, Math.floor(((x + step) / w) * peaks.length));
    let peak = 0;
    for (let i = from; i < to && i < peaks.length; i += 1) peak = Math.max(peak, peaks[i]);
    const bar = Math.max(step, peak * h * 0.92);
    g.fillRect(x, mid - bar / 2, step, bar);
  }
}

function renderRuler() {
  const ruler = $("ruler");
  ruler.replaceChildren();
  const total = timelineDuration();
  if (!(total > 0)) return;
  const width = ruler.getBoundingClientRect().width;
  const stepSeconds = RULER_STEPS.find((s) => (s / total) * width >= RULER_MIN_GAP_PX) ?? RULER_STEPS.at(-1);
  for (let t = 0; t <= total; t += stepSeconds) {
    const tick = el("span", "tick", clock(t));
    tick.style.left = `${(t / total) * 100}%`;
    ruler.append(tick);
  }
}

function speakerName(id) {
  return state.speakers.find((s) => s.id === id)?.name ?? null;
}

function speakerColor(id) {
  const index = state.speakers.findIndex((s) => s.id === id);
  return index >= 0 ? String((index % SPEAKER_COLORS) + 1) : null;
}

function renderLanes() {
  const lanes = $("timeline-lanes");
  lanes.replaceChildren();
  const total = timelineDuration();
  if (!state.result || !previewHoldsJobAudio() || !state.segments.length || !(total > 0)) return;
  const groups = state.speakers.length
    ? state.speakers.map((speaker) => ({ id: speaker.id, name: speaker.name }))
    : [{ id: null, name: "字幕" }];
  for (const group of groups) {
    const lane = el("div", "track lane");
    if (group.id) {
      lane.dataset.speakerColor = speakerColor(group.id);
      lane.dataset.laneSpeaker = group.id;
    }
    const body = el("div", "track-body");
    state.segments.forEach((segment, index) => {
      if (group.id && segment.speaker !== group.id) return;
      const block = el("span", "lane-block");
      block.dataset.index = String(index);
      block.style.left = `${(segment.start / total) * 100}%`;
      block.style.width = `${(Math.max(segment.end - segment.start, 0) / total) * 100}%`;
      if (index === state.activeIndex) block.classList.add("is-active");
      body.append(block);
    });
    lane.append(el("span", "track-head lane-name", group.name), body);
    lanes.append(lane);
  }
}

function refreshTimeline() {
  renderRuler();
  renderLanes();
  drawWaveform();
  updateTransport();
}

function setAttr(node, name, value) {
  if (node.getAttribute(name) !== value) node.setAttribute(name, value);
}

function updatePlayhead() {
  const total = timelineDuration();
  const head = $("playhead");
  head.hidden = !state.audio || !(total > 0);
  if (!head.hidden) head.style.setProperty("--at", String(Math.min(1, audioEl().currentTime / total)));
  const tracks = $("tracks");
  const now = head.hidden ? 0 : Math.min(audioEl().currentTime, total);
  setAttr(tracks, "tabindex", head.hidden ? "-1" : "0");
  setAttr(tracks, "aria-disabled", String(head.hidden));
  setAttr(tracks, "aria-valuemax", String(Math.floor(head.hidden ? 0 : total)));
  setAttr(tracks, "aria-valuenow", String(Math.floor(now)));
  setAttr(tracks, "aria-valuetext", head.hidden ? "未載入音訊" : `${clock(now)} / ${clock(total)}`);
}

function updateTransport() {
  const audio = audioEl();
  const toggle = $("player-toggle");
  const playing = !audio.paused;
  toggle.disabled = !state.audio;
  toggle.setAttribute("aria-pressed", String(playing));
  toggle.setAttribute("aria-label", playing ? "暫停" : "播放");
  $("player-time").textContent = timecode(state.audio ? audio.currentTime : 0);
  const total = timelineDuration();
  $("player-total").textContent = total > 0 ? timecode(total) : "";
  updatePlayhead();
}

function segmentIndexAt(t) {
  return state.segments.findIndex((s) => t >= s.start && t < s.end);
}

function setActive(index, follow) {
  if (index === state.activeIndex) return;
  for (const node of document.querySelectorAll(".is-active[data-index]")) {
    node.classList.remove("is-active");
    node.removeAttribute("aria-current");
  }
  state.activeIndex = index;
  if (index < 0) return;
  const row = $("segments").querySelector(`tr[data-index="${index}"]`);
  row?.classList.add("is-active");
  row?.setAttribute("aria-current", "true");
  $("timeline-lanes").querySelector(`.lane-block[data-index="${index}"]`)?.classList.add("is-active");
  if (follow && row) row.scrollIntoView({ block: "nearest" });
}

function syncPlayback(follow) {
  updateTransport();
  if (previewHoldsJobAudio() && state.segments.length) setActive(segmentIndexAt(audioEl().currentTime), follow);
}

function playbackLoop() {
  syncPlayback(true);
  if (!audioEl().paused) state.frame = requestAnimationFrame(playbackLoop);
}

function seek(seconds) {
  const audio = audioEl();
  if (!state.audio) return;
  audio.currentTime = Math.max(0, Math.min(seconds, timelineDuration()));
  syncPlayback(false);
}

function togglePlay() {
  const audio = audioEl();
  if (!state.audio) return;
  if (audio.paused) audio.play().catch(() => {});
  else audio.pause();
}

function bindPlayback() {
  const audio = audioEl();
  audio.addEventListener("play", () => {
    cancelAnimationFrame(state.frame);
    state.frame = requestAnimationFrame(playbackLoop);
  });
  audio.addEventListener("pause", () => {
    cancelAnimationFrame(state.frame);
    syncPlayback(false);
  });
  audio.addEventListener("seeked", () => syncPlayback(false));
  audio.addEventListener("loadedmetadata", refreshTimeline);
  audio.addEventListener("error", () => {
    if (state.audio) setStatus("瀏覽器無法播放這個格式", "仍可送出辨識", "error");
  });
  audio.addEventListener("emptied", updateTransport);
  $("player-toggle").addEventListener("click", togglePlay);

  const tracks = $("tracks");
  const body = () => $("ruler").getBoundingClientRect();
  const seekToX = (clientX) => {
    const rect = body();
    if (rect.width > 0) seek(((clientX - rect.left) / rect.width) * timelineDuration());
  };
  tracks.addEventListener("pointerdown", (event) => {
    if (!state.audio || event.button !== 0 || !event.target.closest(".track-body")) return;
    event.preventDefault();
    tracks.setPointerCapture(event.pointerId);
    tracks.classList.add("is-scrubbing");
    seekToX(event.clientX);
  });
  tracks.addEventListener("pointermove", (event) => {
    if (tracks.hasPointerCapture(event.pointerId)) seekToX(event.clientX);
  });
  const release = (event) => {
    if (tracks.hasPointerCapture(event.pointerId)) tracks.releasePointerCapture(event.pointerId);
    tracks.classList.remove("is-scrubbing");
  };
  tracks.addEventListener("pointerup", release);
  tracks.addEventListener("pointercancel", release);
  const steps = {
    ArrowLeft: -SEEK_STEP_SECONDS,
    ArrowDown: -SEEK_STEP_SECONDS,
    ArrowRight: SEEK_STEP_SECONDS,
    ArrowUp: SEEK_STEP_SECONDS,
    PageDown: -SEEK_PAGE_SECONDS,
    PageUp: SEEK_PAGE_SECONDS,
  };
  tracks.addEventListener("keydown", (event) => {
    if (!state.audio || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key in steps) seek(audio.currentTime + steps[event.key]);
    else if (event.key === "Home") seek(0);
    else if (event.key === "End") seek(timelineDuration());
    else return;
    event.preventDefault();
  });

  document.addEventListener("keydown", (event) => {
    if (event.code !== "Space" || event.repeat || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target.closest("input, select, textarea, button, a, [contenteditable], dialog")) return;
    if (!state.audio) return;
    event.preventDefault();
    togglePlay();
  });

  new ResizeObserver(() => {
    renderRuler();
    drawWaveform();
  }).observe(tracks);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawWaveform);
}

function recordingSupported() {
  return window.isSecureContext && !!navigator.mediaDevices?.getUserMedia && "MediaRecorder" in window;
}

function showRecordingUnsupported() {
  if (window.isSecureContext) {
    setStatus("這個瀏覽器不支援錄音", "請改用上傳檔案", "error");
    announce("這個瀏覽器不支援錄音，請改用上傳檔案");
    return;
  }
  const detail = `在 chrome://flags 搜尋 insecure origins，加入 ${location.origin}`;
  setStatus("瀏覽器只允許 HTTPS 或 localhost 錄音", detail, "error");
  announce(`瀏覽器只允許 HTTPS 或 localhost 錄音，${detail}`);
}

function bindRecorder() {
  $("record-btn").addEventListener("click", () => {
    if (!recordingSupported()) showRecordingUnsupported();
    else if (state.recording) stopRecording();
    else startRecording();
  });
}

function recordingName(type) {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
  return `錄音-${stamp}.${ext}`;
}

async function startRecording() {
  if (state.uploading) return setStatus("上傳中，請等上傳完成再錄音", "", "error");
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    setStatus(err?.name === "NotAllowedError" ? "沒有麥克風權限" : "無法開啟麥克風", "", "error");
    return;
  }
  const preferred = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find((t) =>
    MediaRecorder.isTypeSupported(t),
  );
  const recorder = new MediaRecorder(stream, preferred ? { mimeType: preferred } : undefined);
  const chunks = [];
  const audioContext = new AudioContext();
  const analyser = audioContext.createAnalyser();
  analyser.fftSize = 1024;
  audioContext.createMediaStreamSource(stream).connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  const startedAt = performance.now();
  const maxSeconds = state.config?.max_audio_seconds ?? Infinity;

  const rec = { recorder, stream, audioContext, frame: 0 };
  state.recording = rec;

  const tick = () => {
    analyser.getFloatTimeDomainData(samples);
    let peak = 0;
    for (const v of samples) peak = Math.max(peak, Math.abs(v));
    $("level-fill").style.transform = `scaleX(${Math.min(1, peak * 1.5)})`;
    const elapsed = (performance.now() - startedAt) / 1000;
    $("record-timer").textContent = clock(elapsed);
    if (elapsed >= maxSeconds) {
      stopRecording();
      return;
    }
    rec.frame = requestAnimationFrame(tick);
  };

  recorder.addEventListener("dataavailable", (event) => {
    if (event.data.size) chunks.push(event.data);
  });
  recorder.addEventListener("stop", () => {
    const type = recorder.mimeType || preferred || "audio/webm";
    const blob = new Blob(chunks, { type });
    if (blob.size) setAudio(blob, recordingName(type));
    announce("錄音完成");
  });

  recorder.start(1000);
  tick();
  $("record-btn").setAttribute("aria-pressed", "true");
  $("record-label").textContent = "停止";
  $("rec-meter").hidden = false;
  updateSubmit();
  $("open-btn").classList.add("is-disabled");
  $("file-input").disabled = true;
  setStatus("錄音中", "");
}

function stopRecording() {
  const rec = state.recording;
  if (!rec) return;
  state.recording = null;
  cancelAnimationFrame(rec.frame);
  if (rec.recorder.state !== "inactive") rec.recorder.stop();
  rec.stream.getTracks().forEach((track) => track.stop());
  rec.audioContext.close();
  $("level-fill").style.transform = "scaleX(0)";
  $("record-btn").setAttribute("aria-pressed", "false");
  $("record-label").textContent = "錄音";
  $("rec-meter").hidden = true;
  updateSubmit();
  $("open-btn").classList.remove("is-disabled");
  $("file-input").disabled = false;
}

function uploadJob(model, speakers, audio, onProgress) {
  return new Promise((resolve, reject) => {
    const body = new FormData();
    body.append("model", model);
    body.append("speakers", speakers);
    body.append("file", audio.blob, audio.name);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.responseType = "json";
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    });
    xhr.addEventListener("load", () => {
      const data = xhr.response || {};
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data.detail || `上傳失敗（HTTP ${xhr.status}）`));
    });
    xhr.addEventListener("error", () => reject(new Error("網路連線中斷")));
    xhr.send(body);
  });
}

function bindForm() {
  const form = $("transcribe-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const model = selectedModelId();
    if (!model) return setStatus("沒有可用的模型", "", "error");
    if (state.recording) return setStatus("請先停止錄音", "", "error");
    if (!state.audio) return setStatus("請先開啟音訊", "", "error");

    const audio = state.audio;
    state.uploading = true;
    updateSubmit();
    setStatus("上傳中", "");
    setEta(0);
    try {
      const job = await uploadJob(model, selectedSpeakers(), audio, (ratio) => {
        if (ratio < 1) {
          setStatus("上傳中", `${Math.round(ratio * 100)}%`);
          setEta(ratio);
        } else {
          setStatus("解析音訊", "");
          setEta(null);
        }
      });
      startJob(job, audio.blob);
      applyServerPeaks(job, audio.blob);
    } catch (err) {
      setStatus(err.message, "", "error");
      setEta(null);
    } finally {
      state.uploading = false;
      updateSubmit();
    }
  });
}

function startJob(job, blob) {
  clearTimeout(state.pollTimer);
  state.job = {
    id: job.id,
    blob,
    modelName: modelById(job.model)?.name || job.model,
    filename: job.filename,
    audioSeconds: job.audio_seconds,
    done: false,
  };
  clearResult();
  setGridEmpty("辨識中…");
  renderJob(job);
  announce("已送出");
  state.pollTimer = setTimeout(pollJob, JOB_POLL_MS);
}

async function pollJob() {
  const current = state.job;
  if (!current) return;
  let res = null;
  let job = null;
  try {
    res = await fetch(`/api/jobs/${encodeURIComponent(current.id)}`, { cache: "no-store" });
    if (res.ok) job = await res.json();
  } catch {
    job = null;
  }
  if (state.job !== current) return;
  if (state.uploading) {
    state.pollTimer = setTimeout(pollJob, JOB_POLL_MS);
    return;
  }
  if (res?.status === 404) {
    showJobError("找不到這個工作，可能已過期或伺服器已重新啟動");
    return;
  }
  if (!job) {
    setStatus("連線中斷", "稍後重試", "error");
    state.pollTimer = setTimeout(pollJob, JOB_POLL_MS * 3);
    return;
  }
  renderJob(job);
  if (job.status === "queued" || job.status === "running") {
    state.pollTimer = setTimeout(pollJob, JOB_POLL_MS);
  }
}

function renderMeta(job) {
  const meta = [
    ["檔案", job.filename],
    ["模型", state.job.modelName],
    ["長度", timecode(job.audio_seconds)],
  ];
  if (job.result) {
    const ratio = job.result.processing_seconds / Math.max(job.result.audio_seconds, 0.001);
    meta.push(["處理", `${duration(job.result.processing_seconds)}（音長的 ${ratio.toFixed(2)} 倍）`]);
    meta.push(["段數", String(job.result.segments.length)]);
    if (job.result.speakers?.length) meta.push(["說話者", String(job.result.speakers.length)]);
  }
  renderProps($("result-meta"), meta);
  $("meta-empty").hidden = true;
}

function renderJob(job) {
  state.job.audioSeconds = job.audio_seconds;
  renderMeta(job);
  $("cancel-btn").hidden = job.status !== "queued" && job.status !== "running";
  setEta(null);

  switch (job.status) {
    case "queued":
      setStatus("排隊中", job.jobs_ahead > 0 ? `前面 ${job.jobs_ahead} 筆` : "即將開始", "busy");
      break;
    case "running": {
      if (!job.stage && state.config?.hardware?.state === "tuning") {
        setStatus("等待自動調校", "", "busy");
        break;
      }
      const line = STAGE_LABELS[job.stage] || "處理中";
      const elapsed = job.stage_elapsed_seconds ?? 0;
      let detail = "";
      if (job.stage === "preparing") {
        detail = "第一次使用需下載模型";
      } else if (job.stage === "transcribing") {
        if (job.progress !== null && job.progress !== undefined) {
          detail = `${Math.round(job.progress * 100)}%  ${clock(elapsed)}`;
          setEta(job.progress);
        } else if (job.estimated_seconds) {
          detail =
            elapsed > job.estimated_seconds
              ? `${clock(elapsed)}，已超過預估的 ${clock(job.estimated_seconds)}`
              : `${clock(elapsed)} / 約 ${clock(job.estimated_seconds)}`;
          setEta(Math.min(elapsed / job.estimated_seconds, 0.97));
        } else {
          detail = clock(elapsed);
        }
        if (job.speakers_progress !== undefined) detail += `  說話者 ${Math.round(job.speakers_progress * 100)}%`;
      } else if (job.stage === "diarizing" && job.speakers_progress !== undefined) {
        detail = `${Math.round(job.speakers_progress * 100)}%`;
        setEta(job.speakers_progress);
      }
      setStatus(line, detail, "busy");
      break;
    }
    case "succeeded":
      state.job.done = true;
      updateSubmit();
      setStatus("完成", `${job.result.segments.length} 段`);
      renderTranscript(job);
      announce(`辨識完成，共 ${job.result.segments.length} 段`);
      break;
    case "failed":
      showJobError(job.error || "辨識失敗");
      break;
    case "cancelled":
      showJobError("已取消");
      break;
  }
}

function showJobError(message) {
  clearTimeout(state.pollTimer);
  if (state.job) state.job.done = true;
  updateSubmit();
  $("cancel-btn").hidden = true;
  setEta(null);
  setStatus(message, "", "error");
  setGridEmpty(message);
}

function bindCancel() {
  twoStepButton($("cancel-btn"), "確定取消", async () => {
    if (!state.job) return;
    try {
      await api(`/api/jobs/${encodeURIComponent(state.job.id)}`, { method: "DELETE" });
      showJobError("已取消");
    } catch (err) {
      $("status-detail").textContent = err.message;
      announce(err.message);
    }
  });
}

function setGridEmpty(message) {
  const node = $("grid-empty");
  node.textContent = message;
  node.hidden = false;
}

function clearResult() {
  state.result = null;
  state.segments = [];
  state.speakers = [];
  state.activeIndex = -1;
  $("segments").replaceChildren();
  $("fulltext").textContent = "";
  $("segment-count").textContent = "";
  $("copy-btn").disabled = true;
  $("speaker-list").replaceChildren();
  $("speakers-empty").hidden = false;
  $("speakers-error").hidden = true;
  for (const fmt of ["txt", "srt", "vtt", "json"]) {
    const link = $(`dl-${fmt}`);
    link.removeAttribute("href");
    link.setAttribute("aria-disabled", "true");
  }
  renderLanes();
}

function fullText(result) {
  if (!result.segments.length) return result.text;
  if (!state.speakers.length) return result.segments.map((s) => s.text).join("\n");
  const blocks = [];
  let previous;
  for (const segment of result.segments) {
    if (blocks.length && segment.speaker === previous) blocks.at(-1).push(segment.text);
    else blocks.push([`${speakerName(segment.speaker) ?? ""}：${segment.text}`]);
    previous = segment.speaker;
  }
  return blocks.map((lines) => lines.join("\n")).join("\n\n");
}

function playSegment(index) {
  if (!previewHoldsJobAudio()) {
    setStatus("目前的音訊不是這份字幕的來源", "", "error");
    return;
  }
  seek(state.segments[index].start);
  audioEl().play().catch(() => {});
}

function renderTranscript(job) {
  const { result } = job;
  state.result = result;
  state.segments = result.segments;
  state.speakers = result.speakers ?? [];
  renderSpeakers(result);
  const rows = $("segments");
  rows.replaceChildren();
  for (const [index, segment] of result.segments.entries()) {
    const row = el("tr", "");
    row.dataset.index = String(index);
    const start = el("button", "cue-time", timecode(segment.start));
    start.type = "button";
    start.setAttribute("aria-label", `從 ${clock(segment.start)} 播放`);
    const speakerCell = el("td", "c-spk");
    if (segment.speaker) {
      row.dataset.speakerColor = speakerColor(segment.speaker);
      const tag = el("span", "cue-speaker", speakerName(segment.speaker));
      tag.dataset.speakerTag = segment.speaker;
      speakerCell.append(tag);
    }
    const startCell = el("td", "c-time");
    startCell.append(start);
    const textCell = el("td", "cue-text");
    textCell.append(cueEditor(index, segment.text));
    row.append(
      el("td", "c-num", String(index + 1)),
      startCell,
      el("td", "c-time cue-end", timecode(segment.end)),
      speakerCell,
      textCell,
    );
    row.addEventListener("click", (event) => {
      if (!event.target.closest(".cue-edit")) playSegment(index);
    });
    rows.append(row);
  }
  $("sub-grid").classList.toggle("no-speakers", !state.speakers.length);
  const text = fullText(result);
  $("fulltext").textContent = text || "（沒有辨識到語音）";
  $("segment-count").textContent = `${result.segments.length} 段`;
  $("copy-btn").disabled = !text;
  if (result.segments.length) $("grid-empty").hidden = true;
  else setGridEmpty("沒有辨識到語音");

  for (const fmt of ["txt", "srt", "vtt", "json"]) {
    const link = $(`dl-${fmt}`);
    link.href = `/api/jobs/${encodeURIComponent(job.id)}/export/${fmt}`;
    link.removeAttribute("aria-disabled");
  }
  applyView(storageGet(STORAGE_KEYS.view) === "full" ? "full" : "segments");
  renderLanes();
  syncPlayback(false);
}

function cueEditor(index, text) {
  const node = el("span", "cue-edit", text);
  node.contentEditable = "plaintext-only";
  node.spellcheck = false;
  node.setAttribute("role", "textbox");
  node.setAttribute("aria-label", `第 ${index + 1} 段文字`);
  node.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (event.key === "Enter") {
      event.preventDefault();
      node.blur();
    } else if (event.key === "Escape") {
      node.textContent = state.segments[index].text;
      node.blur();
    }
  });
  node.addEventListener("blur", () => saveSegmentText(index, node));
  return node;
}

async function saveSegmentText(index, node) {
  const saved = state.segments[index].text;
  const text = node.textContent.replace(/\s+/g, " ").trim();
  if (!text || text === saved) {
    node.textContent = saved;
    return;
  }
  const jobId = state.job.id;
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}/segments/${index}`, {
      method: "PATCH",
      body: { text },
    });
    if (state.job?.id !== jobId) return;
    state.result = job.result;
    state.segments = job.result.segments;
    if (document.activeElement !== node) node.textContent = state.segments[index].text;
    $("fulltext").textContent = fullText(state.result) || "（沒有辨識到語音）";
    announce(`已更新第 ${index + 1} 段`);
  } catch (err) {
    if (state.job?.id !== jobId) return;
    if (document.activeElement !== node) node.textContent = saved;
    setStatus(`無法更新第 ${index + 1} 段：${err.message}`, "", "error");
  }
}

function renderSpeakers(result) {
  const notice = $("speakers-error");
  const message = result.speakers_error
    ? `${result.speakers_error}（字幕不受影響）`
    : result.speakers && !result.speakers.length
      ? "沒有偵測到可分辨的說話聲音"
      : "";
  notice.textContent = message;
  notice.hidden = !message;
  $("speakers-empty").hidden = !!state.speakers.length || !!message;
  $("speaker-list").replaceChildren(...state.speakers.map(speakerItem));
}

function speakerItem(speaker) {
  const item = el("li", "speaker-item");
  item.dataset.speakerId = speaker.id;
  item.dataset.speakerColor = speakerColor(speaker.id);

  const swatch = el("span", "swatch");
  swatch.setAttribute("aria-hidden", "true");
  const input = el("input", "speaker-name");
  input.type = "text";
  input.maxLength = state.config.speakers.max_name_length;
  input.autocomplete = "off";
  input.spellcheck = false;
  input.enterKeyHint = "done";
  input.title = "點一下改名";
  input.setAttribute("aria-label", `第 ${speaker.id.slice(1)} 位說話者的名字`);
  const status = el("span", "speaker-status");
  input.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (event.key === "Enter") {
      event.preventDefault();
      input.blur();
    } else if (event.key === "Escape") {
      input.value = input.dataset.saved;
      input.blur();
    }
  });
  input.addEventListener("change", () => saveSpeakerName(speaker.id, input, status));

  const remember = el("button", "link-btn", "記住聲音");
  remember.type = "button";
  remember.title = "存進語者庫，之後自動認出";
  remember.addEventListener("click", () => rememberVoice(speaker.id, input, status, remember));

  const line = el("span", "speaker-line");
  line.append(el("span", "speaker-meta"), el("span", "speaker-badge"), remember);
  item.append(swatch, input, line, status);
  updateSpeakerItem(item, speaker);
  return item;
}

function updateSpeakerItem(item, speaker) {
  const input = item.querySelector(".speaker-name");
  input.dataset.saved = speaker.name;
  if (document.activeElement !== input) input.value = speaker.name;
  item.querySelector(".speaker-meta").textContent = clock(speaker.seconds);
  const badge = item.querySelector(".speaker-badge");
  if (speaker.voice_id && speaker.similarity !== null) {
    badge.textContent = "已認出";
    badge.title = `相似度 ${speaker.similarity.toFixed(2)}`;
  } else {
    badge.textContent = speaker.voice_id ? "已記住" : "";
    badge.removeAttribute("title");
  }
}

function applySpeakerUpdate(job) {
  if (state.job?.id !== job.id || !job.result) return;
  state.speakers = job.result.speakers ?? [];
  for (const speaker of state.speakers) {
    const item = $("speaker-list").querySelector(`[data-speaker-id="${CSS.escape(speaker.id)}"]`);
    if (item) updateSpeakerItem(item, speaker);
    for (const tag of $("segments").querySelectorAll(`[data-speaker-tag="${CSS.escape(speaker.id)}"]`)) {
      tag.textContent = speaker.name;
    }
    const laneName = $("timeline-lanes").querySelector(`[data-lane-speaker="${CSS.escape(speaker.id)}"] .lane-name`);
    if (laneName) laneName.textContent = speaker.name;
  }
  $("fulltext").textContent = fullText(job.result) || "（沒有辨識到語音）";
}

function setSpeakerStatus(status, message, tone) {
  status.textContent = message;
  if (tone) status.dataset.tone = tone;
  else delete status.dataset.tone;
  if (tone === "error") announce(message);
}

function saveSpeakerName(speakerId, input, status) {
  const name = input.value.trim();
  if (!name || name === input.dataset.saved) {
    input.value = input.dataset.saved;
    return Promise.resolve(true);
  }
  setSpeakerStatus(status, "儲存中…");
  const jobId = state.job.id;
  const saving = api(`/api/jobs/${encodeURIComponent(jobId)}/speakers/${encodeURIComponent(speakerId)}`, {
    method: "PATCH",
    body: { name },
  })
    .then((job) => {
      applySpeakerUpdate(job);
      setSpeakerStatus(status, "");
      announce(`已改名為 ${name}`);
      return true;
    })
    .catch((err) => {
      setSpeakerStatus(status, `無法改名：${err.message}`, "error");
      return false;
    })
    .finally(() => nameSaves.delete(input));
  nameSaves.set(input, saving);
  return saving;
}

async function rememberVoice(speakerId, input, status, button) {
  if (nameSaves.has(input) && !(await nameSaves.get(input))) return;
  if (input.value.trim() !== input.dataset.saved && !(await saveSpeakerName(speakerId, input, status))) return;
  button.disabled = true;
  setSpeakerStatus(status, "記住中…");
  try {
    const data = await api("/api/voices", { method: "POST", body: { job_id: state.job.id, speaker_id: speakerId } });
    applySpeakerUpdate(data.job);
    const message =
      data.voice.samples > 1 ? `已記住「${data.voice.name}」（第 ${data.voice.samples} 次）` : `已記住「${data.voice.name}」`;
    setSpeakerStatus(status, message);
    announce(message);
    loadVoices();
  } catch (err) {
    setSpeakerStatus(status, err.message, "error");
    input.focus();
    input.select();
  } finally {
    button.disabled = false;
  }
}

async function loadVoices() {
  try {
    const data = await api("/api/voices");
    renderVoices(data.voices);
  } catch (err) {
    $("voices-status").textContent = `無法讀取語者庫：${err.message}`;
  }
}

function renderVoices(voices) {
  $("voices-summary").textContent = voices.length ? String(voices.length) : "";
  $("voices-empty").hidden = voices.length > 0;
  $("voice-list").replaceChildren(...voices.map(voiceItem));
}

function voiceItem(voice) {
  const item = el("li", "voice-item");
  const updated = new Date(voice.updated_at * 1000).toLocaleString("zh-TW");
  const meta = voice.outdated ? "舊版聲紋，不會用來認人；請重新按一次「記住聲音」" : `${voice.samples} 次　${updated}`;
  item.append(el("span", "voice-name", voice.name), el("span", voice.outdated ? "voice-meta is-outdated" : "voice-meta", meta));
  const remove = el("button", "link-btn danger", "刪除");
  remove.type = "button";
  remove.setAttribute("aria-label", `刪除 ${voice.name} 的聲紋`);
  twoStepButton(remove, "確定刪除", () => forgetVoice(voice));
  item.append(remove);
  return item;
}

async function forgetVoice(voice) {
  const status = $("voices-status");
  try {
    const data = await api(`/api/voices/${encodeURIComponent(voice.id)}`, { method: "DELETE" });
    renderVoices(data.voices);
    status.textContent = `已刪除「${voice.name}」`;
    announce(status.textContent);
    const affected = state.speakers.filter((s) => s.voice_id === voice.id).map((s) => s.id);
    if (state.job && affected.length) {
      applySpeakerUpdate(await api(`/api/jobs/${encodeURIComponent(state.job.id)}`));
      for (const id of affected) {
        const item = $("speaker-list").querySelector(`[data-speaker-id="${CSS.escape(id)}"]`);
        if (item) setSpeakerStatus(item.querySelector(".speaker-status"), "");
      }
    }
  } catch (err) {
    status.textContent = `無法刪除：${err.message}`;
  }
}

function applyView(view) {
  $(view === "full" ? "view-full" : "view-segments").checked = true;
  $("sub-grid").hidden = view === "full";
  $("fulltext").hidden = view !== "full" || !state.result;
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    const copied = await navigator.clipboard.writeText(text).then(
      () => true,
      () => false,
    );
    if (copied) return;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  area.style.pointerEvents = "none";
  document.body.append(area);
  area.select();
  const ok = document.execCommand("copy");
  area.remove();
  if (!ok) throw new Error("copy failed");
}

function bindResultTools() {
  for (const input of document.querySelectorAll('input[name="view"]')) {
    input.addEventListener("change", () => {
      applyView(input.value);
      storageSet(STORAGE_KEYS.view, input.value);
    });
  }
  const label = $("copy-label");
  let resetTimer = 0;
  $("copy-btn").addEventListener("click", async () => {
    try {
      await copyText($("fulltext").textContent);
      label.textContent = "已複製";
      announce("已複製全文");
    } catch {
      label.textContent = "複製失敗";
    }
    clearTimeout(resetTimer);
    resetTimer = setTimeout(() => {
      label.textContent = "複製全文";
    }, 2000);
  });
}

bindUpload();
bindRecorder();
bindForm();
bindCancel();
bindResultTools();
bindPlayback();
bindRetune();
bindDialogs();
refreshTimeline();
refreshConfig();
setInterval(() => {
  if (document.visibilityState === "visible") refreshConfig();
}, CONFIG_REFRESH_MS);
