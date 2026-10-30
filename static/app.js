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
  punctuating: "加標點",
  diarizing: "分辨說話者",
};

const STATUS_LABELS = { queued: "排隊中", running: "處理中", succeeded: "完成", failed: "失敗", cancelled: "已取消" };
const RENDER_LABELS = { queued: "排隊中", running: "燒錄中", cancelling: "取消中", succeeded: "完成", failed: "失敗", cancelled: "已取消" };
const CANDIDATE_STATUS = { ok: "可用", rejected: "錯誤率太高", failed: "無法執行" };
const EXPORT_FORMATS = ["srt", "vtt", "ass", "txt", "md", "docx", "json"];

const STORAGE_KEYS = {
  model: "breeze-asr:model",
  view: "breeze-asr:view",
  speakers: "breeze-asr:speakers",
  punctuation: "breeze-asr:punctuation",
  hotwords: "breeze-asr:hotwords",
  job: "breeze-asr:job",
};
const NEW_SPEAKER = "__new__";
const LEADING_MARKS = /[\s，。？！、；：,.?!;:]/;
const SPEAKER_COLORS = 8;
const CONFIRM_MS = 5000;
const CONFIG_REFRESH_MS = 4000;
const JOB_POLL_MS = 1000;
const RENDER_POLL_MS = 1000;
const HOTWORDS_COUNT_DELAY_MS = 400;
const HISTORY_LIMIT = 100;
const DURATION_TOLERANCE_SECONDS = 2;
const SPLIT_MARGIN_SECONDS = 0.05;
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
  batch: null,
  previewUrl: null,
  job: null,
  result: null,
  revision: 0,
  pollTimer: null,
  segments: [],
  speakers: [],
  activeIndex: -1,
  selected: -1,
  caret: null,
  history: { past: [], future: [] },
  saving: false,
  pendingSave: null,
  find: { matches: [], current: -1 },
  blobs: new Map(),
  jobs: [],
  glossary: { hotwords: "", replacements: [] },
  burnFile: null,
  render: null,
  renderTimer: null,
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

function storageRemove(key) {
  try {
    localStorage.removeItem(key);
  } catch {
    return;
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

function parseTimecode(text) {
  const parts = text.trim().split(/[:：]/);
  if (parts.length > 3 || parts.some((part) => !/^\d+(\.\d+)?$/.test(part))) return NaN;
  return parts.reduce((total, part) => total * 60 + Number(part), 0);
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

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function api(url, options = {}) {
  const init = { method: options.method ?? "GET", cache: "no-store" };
  if (options.body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(options.body);
  }
  const res = await fetch(url, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
    error.status = res.status;
    throw error;
  }
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
    renderPunctuationOption(config.punctuation);
    loadGlossary();
    restoreLastJob();
  } else {
    updateModelStates(config);
  }
  renderHardware(config.hardware);
  renderNotice(config);
  renderWatch(config.watch);
  refreshJobs();
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
    updateHotwordsOption();
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
    `mp3、wav、m4a、flac、ogg、webm、mp4，可一次選多個檔案\n上限 ${uploadLimit(config.max_upload_mb)}、${duration(config.max_audio_seconds)}`;
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

function renderPunctuationOption(info) {
  $("punct-group").hidden = !info;
  if (!info) return;
  const check = $("punct-check");
  check.checked = storageGet(STORAGE_KEYS.punctuation) === "on";
  check.addEventListener("change", () => storageSet(STORAGE_KEYS.punctuation, check.checked ? "on" : "off"));
}

function selectedSpeakers() {
  return $("speakers-group").hidden ? "off" : $("speakers-select").value;
}

function selectedPunctuation() {
  return !$("punct-group").hidden && $("punct-check").checked ? "on" : "off";
}

function updateHotwordsOption() {
  const check = $("hotwords-check");
  const group = $("hotwords-group");
  const model = modelById(selectedModelId());
  const hotwords = state.glossary.hotwords;
  let reason = "";
  if (!hotwords) reason = "先在「詞彙」設定熱詞";
  else if (model && !model.hotwords) reason = `${model.name} 轉呼叫外部服務，不支援熱詞`;
  check.disabled = !!reason;
  check.checked = !reason && storageGet(STORAGE_KEYS.hotwords) !== "off";
  group.classList.toggle("is-disabled", !!reason);
  group.title = reason || `辨識時參考：${hotwords}`;
}

function selectedHotwords() {
  return $("hotwords-check").checked && !$("hotwords-check").disabled ? state.glossary.hotwords : "";
}

function renderNotice(config) {
  const hardware = config.hardware;
  const speakers = config.speakers;
  const punctuation = config.punctuation;
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
  } else if (punctuation?.state === "downloading") {
    text = "下載標點模型";
    tone = "busy";
  } else if (punctuation?.state === "failed") {
    text = `標點模型：${punctuation.error}`;
    tone = "error";
  } else if (config.watch?.error) {
    text = `監看資料夾：${config.watch.error}`;
    tone = "error";
  }
  const node = $("notice");
  node.textContent = text;
  node.title = title;
  node.dataset.tone = tone;
  node.hidden = !text;
}

function renderWatch(watch) {
  const node = $("watch-summary");
  node.textContent = watch ? `監看中${watch.active ? `（${watch.active} 個處理中）` : ""}` : "";
  node.title = watch
    ? `${watch.directory}\n格式：${watch.formats.join("、")}\n說話者：${watch.speakers}，標點：${watch.punctuation ? "開" : "關"}`
    : "";
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
    ...pairs.map(([term, value, title]) => {
      const row = el("div", "");
      const dd = el("dd", "", value);
      if (title) dd.title = title;
      row.append(el("dt", "", term), dd);
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

async function refreshJobs() {
  try {
    const data = await api("/api/jobs");
    state.jobs = data.jobs;
  } catch {
    return;
  }
  renderJobs();
}

function jobStatusText(job) {
  if (job.status === "running") return STAGE_LABELS[job.stage] || "處理中";
  if (job.status === "succeeded") return `${job.segments} 段`;
  return STATUS_LABELS[job.status] ?? job.status;
}

function jobItem(job) {
  const item = el("li", "");
  const button = el("button", "job-item");
  button.type = "button";
  if (state.job?.id === job.id) button.setAttribute("aria-current", "true");
  const when = new Date(job.created_at * 1000).toLocaleString("zh-TW", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  const status = el("span", "job-status", jobStatusText(job));
  if (job.status === "failed") status.dataset.tone = "error";
  else if (job.status === "queued" || job.status === "running") status.dataset.tone = "busy";
  button.append(el("span", "job-name", job.filename), el("span", "job-meta", `${clock(job.audio_seconds)}　${when}`), status);
  button.title = job.filename;
  button.addEventListener("click", () => openJob(job.id));
  item.append(button);
  return item;
}

function renderJobs() {
  const list = $("job-list");
  $("jobs-empty").hidden = state.jobs.length > 0;
  const key = JSON.stringify([state.jobs, state.job?.id ?? null]);
  if (list.dataset.key === key) return;
  list.dataset.key = key;
  list.replaceChildren(...state.jobs.map(jobItem));
}

async function openJob(id, quiet = false) {
  if (state.job?.id === id) return;
  if (state.uploading) return setStatus("上傳中，請等上傳完成再切換工作", "", "error");
  if (state.recording) return setStatus("請先停止錄音", "", "error");
  let job;
  try {
    job = await api(`/api/jobs/${encodeURIComponent(id)}`);
  } catch (err) {
    if (err.status === 404) storageRemove(STORAGE_KEYS.job);
    if (!quiet) setStatus(err.message, "", "error");
    return;
  }
  const known = state.blobs.get(id);
  if (known) {
    if (state.audio?.blob !== known.blob) setAudio(known.blob, known.name);
  } else {
    clearAudio();
  }
  startJob(job, known?.blob ?? null, true);
}

function restoreLastJob() {
  const id = storageGet(STORAGE_KEYS.job);
  if (id && !state.job) openJob(id, true);
}

function audioEl() {
  return $("media");
}

function hasVideo() {
  return audioEl().videoWidth > 0;
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
  state.batch = null;
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
  updateRelink();
}

function setFiles(files) {
  if (!files.length) return;
  if (files.length === 1) {
    setAudio(files[0], files[0].name);
    return;
  }
  const limit = state.config ? state.config.max_upload_mb * 1024 * 1024 : null;
  const large = files.find((file) => limit !== null && file.size > limit);
  if (large) {
    setStatus(`${large.name} 超過上限 ${uploadLimit(state.config.max_upload_mb)}`, "", "error");
    return;
  }
  if (state.uploading) return setStatus("上傳中，請等上傳完成再換檔案", "", "error");
  clearAudio();
  state.batch = files;
  $("selected-name").textContent = `${files.length} 個檔案`;
  $("selected-name").title = files.map((file) => file.name).join("\n");
  $("selected-size").textContent = bytes(files.reduce((total, file) => total + file.size, 0));
  $("clear-audio").hidden = false;
  setStatus(`已選擇 ${files.length} 個檔案`, "按「辨識」全部送出排隊");
  updateSubmit();
}

function updateSubmit() {
  const busy = !!state.job && !state.job.done && !!state.audio && state.job.blob === state.audio.blob;
  $("submit-btn").disabled = !!state.recording || state.uploading || busy;
  $("submit-btn").textContent = state.batch ? `辨識 ${state.batch.length} 個` : "辨識";
}

function clearAudio() {
  if (state.uploading) return setStatus("上傳中，請等上傳完成再關閉音訊", "", "error");
  const audio = audioEl();
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.audio = null;
  state.batch = null;
  state.previewUrl = null;
  state.peaks = null;
  state.waveToken += 1;
  updateSubmit();
  $("file-input").value = "";
  $("selected-name").textContent = "未載入音訊";
  $("selected-name").removeAttribute("title");
  $("selected-size").textContent = "";
  $("clear-audio").hidden = true;
  $("wave-note").textContent = "";
  $("preview-panel").hidden = true;
  setActive(-1);
  refreshTimeline();
  updateRelink();
}

function updateRelink() {
  const unlinked = !!state.result && !previewHoldsJobAudio();
  $("relink-btn").hidden = !unlinked;
  if (unlinked && !state.audio) $("selected-name").textContent = "這份字幕沒有對應的音訊";
  renderCuePanel();
}

async function linkSource(file) {
  const job = state.job;
  if (!job) return;
  setAudio(file, file.name);
  const ok = await waitForMetadata(audioEl());
  if (state.job !== job || state.audio?.blob !== file) return;
  if (!ok) {
    setStatus("瀏覽器無法播放這個檔案", "", "error");
    return;
  }
  const length = audioEl().duration;
  if (Number.isFinite(length) && job.audioSeconds && Math.abs(length - job.audioSeconds) > DURATION_TOLERANCE_SECONDS) {
    setStatus(`長度 ${clock(length)} 和這份字幕的 ${clock(job.audioSeconds)} 不符`, "請選擇原始檔", "error");
    return;
  }
  job.blob = file;
  state.blobs.set(job.id, { blob: file, name: file.name });
  setStatus("已對應原始檔", file.name);
  announce("已對應原始檔");
  refreshTimeline();
  syncPlayback(false);
  updateRelink();
  updateSubmit();
}

function bindUpload() {
  const input = $("file-input");
  input.addEventListener("change", () => setFiles([...(input.files ?? [])]));
  $("relink-input").addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) linkSource(file);
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
    setFiles([...event.dataTransfer.files]);
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
      if (index === state.selected) block.classList.add("is-selected");
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

function updateVideoCue() {
  const segment = previewHoldsJobAudio() ? state.segments[state.activeIndex] : null;
  const name = segment?.speaker ? speakerName(segment.speaker) : null;
  $("video-cue").textContent = segment ? `${name ? `${name}：` : ""}${segment.text}` : "";
}

function setActive(index, follow) {
  if (index === state.activeIndex) return;
  for (const node of document.querySelectorAll(".is-active[data-index]")) {
    node.classList.remove("is-active");
    node.removeAttribute("aria-current");
  }
  state.activeIndex = index;
  updateVideoCue();
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
  audio.addEventListener("loadedmetadata", () => {
    $("preview-panel").hidden = !hasVideo();
    refreshTimeline();
    renderCuePanel();
  });
  audio.addEventListener("error", () => {
    if (state.audio) setStatus("瀏覽器無法播放這個格式", "仍可送出辨識", "error");
  });
  audio.addEventListener("emptied", () => {
    $("preview-panel").hidden = true;
    updateTransport();
  });
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

function upload(url, fields, file, name, onProgress) {
  return new Promise((resolve, reject) => {
    const body = new FormData();
    for (const [key, value] of Object.entries(fields)) body.append(key, value);
    body.append("file", file, name);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.responseType = "json";
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    });
    xhr.addEventListener("load", () => {
      const data = xhr.response || {};
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(typeof data.detail === "string" ? data.detail : `上傳失敗（HTTP ${xhr.status}）`));
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
    const items = state.batch ? state.batch.map((file) => ({ blob: file, name: file.name })) : state.audio ? [state.audio] : [];
    if (!items.length) return setStatus("請先開啟音訊", "", "error");

    const fields = {
      model,
      speakers: selectedSpeakers(),
      punctuation: selectedPunctuation(),
      hotwords: selectedHotwords(),
    };
    const batch = items.length > 1;
    state.uploading = true;
    updateSubmit();
    setEta(0);
    let first = null;
    let failure = null;
    try {
      for (const [i, item] of items.entries()) {
        const label = batch ? `上傳 ${i + 1}／${items.length}：${item.name}` : "上傳中";
        setStatus(label, "");
        const job = await upload("/api/jobs", fields, item.blob, item.name, (ratio) => {
          if (ratio < 1) {
            setStatus(label, `${Math.round(ratio * 100)}%`);
            setEta(ratio);
          } else {
            setStatus("解析音訊", batch ? item.name : "");
            setEta(null);
          }
        });
        state.blobs.set(job.id, item);
        if (!first) first = { job, item };
      }
    } catch (err) {
      failure = err.message;
      setStatus(failure, "", "error");
      setEta(null);
    } finally {
      state.uploading = false;
      updateSubmit();
    }
    if (!first) return;
    if (batch) {
      setAudio(first.item.blob, first.item.name);
      if (!failure) announce(`已送出 ${items.length} 個檔案`);
    }
    startJob(first.job, first.item.blob, false);
    applyServerPeaks(first.job, first.item.blob);
    refreshJobs();
    if (failure) setStatus(failure, "前面的檔案已送出，可在「工作」查看", "error");
  });
}

function startJob(job, blob, opened) {
  clearTimeout(state.pollTimer);
  state.job = {
    id: job.id,
    blob,
    modelName: modelById(job.model)?.name || job.model,
    filename: job.filename,
    audioSeconds: job.audio_seconds,
    status: job.status,
    done: false,
  };
  storageSet(STORAGE_KEYS.job, job.id);
  clearResult();
  setGridEmpty(job.status === "queued" || job.status === "running" ? "辨識中…" : "");
  renderJob(job, opened);
  renderJobs();
  if (!opened) announce("已送出");
  if (job.status === "queued" || job.status === "running") state.pollTimer = setTimeout(pollJob, JOB_POLL_MS);
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
  renderJob(job, false);
  if (job.status === "queued" || job.status === "running") {
    state.pollTimer = setTimeout(pollJob, JOB_POLL_MS);
  } else {
    refreshJobs();
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
  const punctuationError = job.result?.punctuation_error;
  meta.push(["標點", job.punctuation ? (punctuationError ? "失敗" : "開") : "關", punctuationError ?? ""]);
  meta.push(["熱詞", job.hotwords ? `${job.hotwords.length} 字` : "無", job.hotwords]);
  renderProps($("result-meta"), meta);
  $("meta-empty").hidden = true;
}

function renderJob(job, opened) {
  state.job.audioSeconds = job.audio_seconds;
  state.job.status = job.status;
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
      setStatus(opened ? "已開啟" : "完成", `${job.filename}，${job.result.segments.length} 段`);
      renderTranscript(job);
      announce(opened ? `已開啟 ${job.filename}` : `辨識完成，共 ${job.result.segments.length} 段`);
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
      refreshJobs();
    } catch (err) {
      $("status-detail").textContent = err.message;
      announce(err.message);
    }
  });
}

function setGridEmpty(message) {
  const node = $("grid-empty");
  node.textContent = message;
  node.hidden = !message;
}

function clearResult() {
  state.result = null;
  state.revision = 0;
  state.segments = [];
  state.speakers = [];
  state.activeIndex = -1;
  state.selected = -1;
  state.caret = null;
  state.history = { past: [], future: [] };
  state.find = { matches: [], current: -1 };
  $("segments").replaceChildren();
  $("fulltext").textContent = "";
  $("segment-count").textContent = "";
  $("copy-btn").disabled = true;
  $("find-btn").disabled = true;
  closeFind();
  $("speaker-list").replaceChildren();
  $("speaker-list").dataset.key = "";
  $("speakers-empty").hidden = false;
  $("speakers-error").hidden = true;
  for (const fmt of EXPORT_FORMATS) {
    const link = $(`dl-${fmt}`);
    link.removeAttribute("href");
    link.setAttribute("aria-disabled", "true");
  }
  $("burn-btn").disabled = true;
  updateHistoryButtons();
  updateVideoCue();
  renderLanes();
  updateRelink();
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

function editable() {
  return !!state.result && !!state.job?.done && state.job.status === "succeeded";
}

function segmentRow(index) {
  const row = el("tr", "");
  row.dataset.index = String(index);
  const start = el("button", "cue-time");
  start.type = "button";
  const startCell = el("td", "c-time");
  startCell.append(start);
  const textCell = el("td", "cue-text");
  textCell.append(cueEditor(index));
  row.append(el("td", "c-num", String(index + 1)), startCell, el("td", "c-time cue-end"), el("td", "c-spk"), textCell);
  row.addEventListener("click", (event) => {
    selectSegment(index);
    if (!event.target.closest(".cue-edit") && previewHoldsJobAudio()) playSegment(index);
  });
  row.addEventListener("focusin", () => selectSegment(index));
  return row;
}

function fillRow(row, segment) {
  const start = row.querySelector(".cue-time");
  start.textContent = timecode(segment.start);
  start.setAttribute("aria-label", `第 ${Number(row.dataset.index) + 1} 段，從 ${clock(segment.start)} 開始`);
  row.querySelector(".cue-end").textContent = timecode(segment.end);
  const speakerCell = row.querySelector(".c-spk");
  if (segment.speaker) {
    row.dataset.speakerColor = speakerColor(segment.speaker);
    const tag = speakerCell.firstElementChild ?? speakerCell.appendChild(el("span", "cue-speaker"));
    tag.textContent = speakerName(segment.speaker);
    tag.dataset.speakerTag = segment.speaker;
  } else {
    delete row.dataset.speakerColor;
    speakerCell.replaceChildren();
  }
  const editor = row.querySelector(".cue-edit");
  if (document.activeElement !== editor && editor.textContent !== segment.text) editor.textContent = segment.text;
}

function renderRows() {
  const rows = $("segments");
  const existing = rows.children;
  let refocus = null;
  if (existing.length !== state.segments.length) {
    const focused = document.activeElement;
    const focusRow = focused?.closest?.("tr[data-index]")?.dataset.index;
    const focusClass = focused?.classList?.contains("cue-edit") ? ".cue-edit" : focused?.classList?.contains("cue-time") ? ".cue-time" : null;
    rows.replaceChildren(...state.segments.map((_, index) => segmentRow(index)));
    if (focusRow !== undefined && focusClass) refocus = `tr[data-index="${focusRow}"] ${focusClass}`;
  }
  state.segments.forEach((segment, index) => fillRow(existing[index], segment));
  if (refocus) rows.querySelector(refocus)?.focus();
  for (const row of rows.querySelectorAll(".is-active, .is-selected")) row.classList.remove("is-active", "is-selected");
  rows.querySelector(`tr[data-index="${state.activeIndex}"]`)?.classList.add("is-active");
  rows.querySelector(`tr[data-index="${state.selected}"]`)?.classList.add("is-selected");
}

function renderTranscript(job) {
  const { result } = job;
  state.result = result;
  state.revision = result.revision;
  state.segments = result.segments;
  state.speakers = result.speakers ?? [];
  if (state.selected >= state.segments.length) state.selected = state.segments.length - 1;
  if (state.activeIndex >= state.segments.length) state.activeIndex = -1;
  renderSpeakers(result);
  renderRows();
  $("sub-grid").classList.toggle("no-speakers", !state.speakers.length);
  const text = fullText(result);
  $("fulltext").textContent = text || "（沒有辨識到語音）";
  $("segment-count").textContent = `${result.segments.length} 段`;
  $("copy-btn").disabled = !text;
  $("find-btn").disabled = !result.segments.length;
  if (result.segments.length) $("grid-empty").hidden = true;
  else setGridEmpty("沒有辨識到語音");

  for (const fmt of EXPORT_FORMATS) {
    const link = $(`dl-${fmt}`);
    link.href = `/api/jobs/${encodeURIComponent(job.id)}/export/${fmt}`;
    link.removeAttribute("aria-disabled");
  }
  $("burn-btn").disabled = !result.segments.length;
  renderMeta(job);
  applyView(storageGet(STORAGE_KEYS.view) === "full" ? "full" : "segments");
  if (!$("find-bar").hidden) updateMatches();
  renderLanes();
  updateVideoCue();
  updateRelink();
  syncPlayback(false);
}

function playSegment(index) {
  seek(state.segments[index].start);
  audioEl().play().catch(() => {});
}

function selectSegment(index, scroll) {
  if (index === state.selected && !scroll) return;
  state.selected = index;
  for (const node of document.querySelectorAll(".is-selected[data-index]")) node.classList.remove("is-selected");
  const row = $("segments").querySelector(`tr[data-index="${index}"]`);
  row?.classList.add("is-selected");
  $("timeline-lanes").querySelector(`.lane-block[data-index="${index}"]`)?.classList.add("is-selected");
  if (scroll && row) row.scrollIntoView({ block: "nearest" });
  renderCuePanel();
}

function cueEditor(index) {
  const node = el("span", "cue-edit");
  node.contentEditable = "plaintext-only";
  node.spellcheck = false;
  node.setAttribute("role", "textbox");
  node.setAttribute("aria-label", `第 ${index + 1} 段文字`);
  node.addEventListener("keydown", async (event) => {
    if (event.isComposing) return;
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      rememberCaret();
      node.blur();
      await settle();
      splitCue();
    } else if (event.key === "Enter") {
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

function rememberCaret() {
  const selection = getSelection();
  const editor = selection?.anchorNode && (selection.anchorNode.nodeType === Node.TEXT_NODE ? selection.anchorNode.parentElement : selection.anchorNode)?.closest?.(".cue-edit");
  if (!editor) return;
  const index = Number(editor.closest("tr[data-index]").dataset.index);
  const offset =
    selection.anchorNode.nodeType === Node.TEXT_NODE
      ? selection.anchorOffset
      : selection.anchorOffset === 0
        ? 0
        : editor.textContent.length;
  state.caret = { index, offset };
}

function settle() {
  return state.pendingSave ?? Promise.resolve();
}

function saveSegmentText(index, node) {
  const saved = state.segments[index]?.text;
  if (saved === undefined || !editable()) return Promise.resolve();
  const text = node.textContent.replace(/\s+/g, " ").trim();
  if (!text || text === saved) {
    node.textContent = saved;
    return Promise.resolve();
  }
  const draft = currentDraft();
  draft.segments[index].text = text;
  const saving = commit(draft, `已更新第 ${index + 1} 段`)
    .then((ok) => {
      if (!ok && node.isConnected && document.activeElement !== node) node.textContent = saved;
    })
    .finally(() => {
      if (state.pendingSave === saving) state.pendingSave = null;
    });
  state.pendingSave = saving;
  return saving;
}

function currentDraft() {
  return {
    segments: state.segments.map((s) => ({ start: s.start, end: s.end, text: s.text, speaker: s.speaker })),
    speakers: state.result.speakers
      ? state.speakers.map((s) => ({ id: s.id, name: s.name, voice_id: s.voice_id, similarity: s.similarity }))
      : null,
  };
}

async function reloadResult() {
  const job = await api(`/api/jobs/${encodeURIComponent(state.job.id)}`);
  if (state.job?.id === job.id && job.result) renderTranscript(job);
}

async function saveDraft(draft, message) {
  if (state.saving) {
    setStatus("上一個修改還在儲存，請稍候", "", "error");
    return false;
  }
  const jobId = state.job.id;
  state.saving = true;
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}/transcript`, {
      method: "PUT",
      body: { revision: state.revision, segments: draft.segments, speakers: draft.speakers },
    });
    if (state.job?.id !== jobId) return false;
    renderTranscript(job);
    if (message) {
      setStatus(message, "");
      announce(message);
    }
    return true;
  } catch (err) {
    if (state.job?.id !== jobId) return false;
    if (err.status === 409) {
      state.history = { past: [], future: [] };
      updateHistoryButtons();
      await reloadResult().catch(() => {});
    }
    setStatus(`無法儲存：${err.message}`, "", "error");
    announce(`無法儲存：${err.message}`);
    return false;
  } finally {
    state.saving = false;
  }
}

function remember(before) {
  state.history.past.push(before);
  if (state.history.past.length > HISTORY_LIMIT) state.history.past.shift();
  state.history.future = [];
  updateHistoryButtons();
}

async function commit(draft, message) {
  const before = currentDraft();
  if (!(await saveDraft(draft, message))) return false;
  remember(before);
  return true;
}

async function undo() {
  await settle();
  if (!state.history.past.length || !editable()) return;
  const before = currentDraft();
  const draft = state.history.past.pop();
  if (await saveDraft(draft, "已復原")) state.history.future.push(before);
  else state.history.past.push(draft);
  updateHistoryButtons();
}

async function redo() {
  await settle();
  if (!state.history.future.length || !editable()) return;
  const before = currentDraft();
  const draft = state.history.future.pop();
  if (await saveDraft(draft, "已重做")) state.history.past.push(before);
  else state.history.future.push(draft);
  updateHistoryButtons();
}

function updateHistoryButtons() {
  $("undo-btn").disabled = !state.history.past.length;
  $("redo-btn").disabled = !state.history.future.length;
}

function renderCuePanel() {
  const segment = state.segments[state.selected];
  const shown = editable() && !!segment;
  $("cue-form").hidden = !shown;
  $("cue-empty").hidden = shown;
  $("cue-number").textContent = shown ? `#${state.selected + 1}` : "";
  if (!shown) return;
  if (document.activeElement !== $("cue-start")) $("cue-start").value = timecode(segment.start);
  if (document.activeElement !== $("cue-end")) $("cue-end").value = timecode(segment.end);
  const withSpeakers = !!state.result.speakers;
  const select = $("cue-speaker");
  select.hidden = !withSpeakers;
  $("cue-speaker-label").hidden = !withSpeakers;
  if (withSpeakers) {
    select.replaceChildren(...state.speakers.map((s) => new Option(s.name, s.id)), new Option("新增說話者", NEW_SPEAKER));
    select.value = segment.speaker;
  }
  const linked = previewHoldsJobAudio();
  $("cue-start-now").disabled = !linked;
  $("cue-end-now").disabled = !linked;
  $("cue-merge").disabled = state.selected >= state.segments.length - 1;
}

async function setCueTime(which, seconds) {
  await settle();
  const index = state.selected;
  const segment = state.segments[index];
  if (!segment) return;
  const value = Math.round(seconds * 100) / 100;
  const start = which === "start" ? value : segment.start;
  const end = which === "end" ? value : segment.end;
  if (!Number.isFinite(value) || start < 0 || end > state.job.audioSeconds + 0.01 || end <= start) {
    setStatus("時間不正確：結束必須晚於開始，而且不能超過音訊長度", "", "error");
    renderCuePanel();
    return;
  }
  if (start === segment.start && end === segment.end) return;
  const draft = currentDraft();
  const moved = { ...draft.segments[index], start, end };
  draft.segments[index] = moved;
  draft.segments.sort((a, b) => a.start - b.start);
  if (await commit(draft, `已調整第 ${index + 1} 段的時間`)) selectSegment(draft.segments.indexOf(moved), true);
  else renderCuePanel();
}

async function setCueSpeaker(value) {
  await settle();
  const index = state.selected;
  if (!state.segments[index]) return;
  const draft = currentDraft();
  let speakerId = value;
  if (value === NEW_SPEAKER) {
    const taken = [...draft.speakers.map((s) => s.id), ...state.result.voiceprint_speakers];
    const next = Math.max(0, ...taken.map((id) => Number(id.slice(1)))) + 1;
    speakerId = `S${next}`;
    draft.speakers.push({ id: speakerId, name: `說話者 ${next}`, voice_id: null, similarity: null });
  }
  draft.segments[index].speaker = speakerId;
  const name = draft.speakers.find((s) => s.id === speakerId).name;
  if (!(await commit(draft, `第 ${index + 1} 段改為「${name}」`))) renderCuePanel();
}

async function splitCue() {
  await settle();
  const index = state.selected;
  const segment = state.segments[index];
  if (!segment) return;
  const text = segment.text;
  const now = previewHoldsJobAudio() ? audioEl().currentTime : null;
  const inside = now !== null && now > segment.start + SPLIT_MARGIN_SECONDS && now < segment.end - SPLIT_MARGIN_SECONDS;
  const span = segment.end - segment.start;
  let offset = state.caret?.index === index ? state.caret.offset : null;
  if (offset === null) {
    const at = inside ? now : segment.start + span / 2;
    offset = Math.round((text.length * (at - segment.start)) / span);
  }
  while (offset < text.length && LEADING_MARKS.test(text[offset])) offset += 1;
  const head = text.slice(0, offset).trim();
  const tail = text.slice(offset).trim();
  if (!head || !tail) {
    setStatus("分割點兩邊都要有文字", "把文字游標放在要分開的位置，再按「分割」或 Ctrl+Enter", "error");
    return;
  }
  const time = Math.round((inside ? now : segment.start + (span * offset) / text.length) * 100) / 100;
  if (time <= segment.start || time >= segment.end) {
    setStatus("這一段太短，無法分割", "", "error");
    return;
  }
  const draft = currentDraft();
  const original = draft.segments[index];
  draft.segments.splice(index, 1, { ...original, end: time, text: head }, { ...original, start: time, text: tail });
  if (await commit(draft, `已把第 ${index + 1} 段分成兩段`)) {
    state.caret = null;
    selectSegment(index + 1, true);
  }
}

async function mergeCue() {
  await settle();
  const index = state.selected;
  const a = state.segments[index];
  const b = state.segments[index + 1];
  if (!a || !b) return;
  const joiner = /[A-Za-z0-9]$/.test(a.text) && /^[A-Za-z0-9]/.test(b.text) ? " " : "";
  const text = a.text + joiner + b.text;
  if (text.length > state.config.max_text_length) {
    setStatus(`合併後超過 ${state.config.max_text_length} 個字`, "", "error");
    return;
  }
  const draft = currentDraft();
  draft.segments.splice(index, 2, { ...draft.segments[index], end: Math.max(a.end, b.end), text });
  if (await commit(draft, `已合併第 ${index + 1}、${index + 2} 段`)) selectSegment(index, true);
}

async function deleteCue() {
  await settle();
  const index = state.selected;
  if (!state.segments[index]) return;
  const draft = currentDraft();
  draft.segments.splice(index, 1);
  if (await commit(draft, `已刪除第 ${index + 1} 段，可按「復原」還原`)) {
    selectSegment(Math.min(index, draft.segments.length - 1), true);
  }
}

async function mergeSpeaker(from, into) {
  await settle();
  const fromName = speakerName(from);
  const intoName = speakerName(into);
  const draft = currentDraft();
  for (const segment of draft.segments) if (segment.speaker === from) segment.speaker = into;
  draft.speakers = draft.speakers.filter((s) => s.id !== from);
  await commit(draft, `已把「${fromName}」併入「${intoName}」`);
}

function bindCueEditor() {
  document.addEventListener("selectionchange", () => {
    const anchor = getSelection()?.anchorNode;
    const node = anchor?.nodeType === Node.TEXT_NODE ? anchor.parentElement : anchor;
    if (node?.closest?.(".cue-edit")) rememberCaret();
  });
  for (const which of ["start", "end"]) {
    const input = $(`cue-${which}`);
    const apply = () => {
      const seconds = parseTimecode(input.value);
      if (Number.isNaN(seconds)) {
        setStatus("時間格式是 時:分:秒.百分秒，例如 0:01:23.45", "", "error");
        renderCuePanel();
        return;
      }
      setCueTime(which, seconds);
    };
    input.addEventListener("change", apply);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        input.value = timecode(state.segments[state.selected][which]);
        input.blur();
      }
    });
    $(`cue-${which}-now`).addEventListener("click", () => setCueTime(which, audioEl().currentTime));
  }
  $("cue-speaker").addEventListener("change", (event) => setCueSpeaker(event.target.value));
  $("cue-split").addEventListener("click", splitCue);
  $("cue-merge").addEventListener("click", mergeCue);
  $("cue-delete").addEventListener("click", deleteCue);
  $("undo-btn").addEventListener("click", undo);
  $("redo-btn").addEventListener("click", redo);
  document.addEventListener("keydown", (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    const key = event.key.toLowerCase();
    if (key === "f" && editable() && state.segments.length) {
      event.preventDefault();
      openFind();
      return;
    }
    if (event.target.closest("input, textarea, [contenteditable]")) return;
    if (key === "z" && !event.shiftKey) {
      event.preventDefault();
      undo();
    } else if (key === "y" || (key === "z" && event.shiftKey)) {
      event.preventDefault();
      redo();
    }
  });
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
  const list = $("speaker-list");
  const key = JSON.stringify(state.speakers.map((s) => s.id));
  if (list.dataset.key === key) {
    for (const speaker of state.speakers) {
      const item = list.querySelector(`[data-speaker-id="${CSS.escape(speaker.id)}"]`);
      if (item) updateSpeakerItem(item, speaker);
    }
    return;
  }
  list.dataset.key = key;
  list.replaceChildren(...state.speakers.map(speakerItem));
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

  const remember = el("button", "link-btn speaker-remember", "記住聲音");
  remember.type = "button";
  remember.title = "存進語者庫，之後自動認出";
  remember.addEventListener("click", () => rememberVoice(speaker.id, input, status, remember));

  const merge = el("select", "speaker-merge");
  merge.hidden = state.speakers.length < 2;
  merge.addEventListener("change", () => {
    const into = merge.value;
    merge.value = "";
    if (into) mergeSpeaker(speaker.id, into);
  });

  const line = el("span", "speaker-line");
  line.append(el("span", "speaker-meta"), el("span", "speaker-badge"), merge, remember);
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
  item.querySelector(".speaker-remember").hidden = !state.result.voiceprint_speakers.includes(speaker.id);
  const merge = item.querySelector(".speaker-merge");
  merge.setAttribute("aria-label", `把 ${speaker.name} 併入其他說話者`);
  merge.replaceChildren(
    new Option("併入…", ""),
    ...state.speakers.filter((s) => s.id !== speaker.id).map((s) => new Option(s.name, s.id)),
  );
}

function applySpeakerUpdate(job) {
  if (state.job?.id !== job.id || !job.result) return;
  renderTranscript(job);
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
  const before = currentDraft();
  const saving = api(`/api/jobs/${encodeURIComponent(jobId)}/speakers/${encodeURIComponent(speakerId)}`, {
    method: "PATCH",
    body: { name },
  })
    .then((job) => {
      applySpeakerUpdate(job);
      if (state.job?.id === jobId) remember(before);
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

function openFind() {
  $("find-bar").hidden = false;
  $("find-btn").setAttribute("aria-expanded", "true");
  $("find-input").focus();
  $("find-input").select();
  updateMatches();
}

function closeFind() {
  $("find-bar").hidden = true;
  $("find-btn").setAttribute("aria-expanded", "false");
  state.find = { matches: [], current: -1 };
  for (const row of $("segments").querySelectorAll(".is-match")) row.classList.remove("is-match");
}

function findPattern() {
  const query = $("find-input").value;
  return query ? new RegExp(escapeRegExp(query), "gi") : null;
}

function updateMatches() {
  const pattern = findPattern();
  const matches = pattern ? state.segments.flatMap((s, i) => (s.text.match(pattern) ? [i] : [])) : [];
  state.find.matches = matches;
  if (state.find.current >= matches.length || state.find.current < 0) state.find.current = matches.length ? 0 : -1;
  const set = new Set(matches);
  for (const row of $("segments").children) row.classList.toggle("is-match", set.has(Number(row.dataset.index)));
  const count = $("find-count");
  count.textContent = !pattern ? "" : matches.length ? `${state.find.current + 1}／${matches.length}` : "沒有符合";
  for (const id of ["find-prev", "find-next", "replace-one", "replace-all"]) $(id).disabled = !matches.length;
}

function stepMatch(delta) {
  const { matches } = state.find;
  if (!matches.length) return;
  state.find.current = (state.find.current + delta + matches.length) % matches.length;
  selectSegment(matches[state.find.current], true);
  updateMatches();
}

async function replaceIn(indices, message) {
  await settle();
  const pattern = findPattern();
  if (!pattern || !indices.length) return;
  const replacement = $("replace-input").value;
  const draft = currentDraft();
  let changed = 0;
  for (const index of indices) {
    const text = draft.segments[index].text.replace(pattern, () => replacement).replace(/\s+/g, " ").trim();
    if (text !== draft.segments[index].text) changed += 1;
    draft.segments[index].text = text;
  }
  draft.segments = draft.segments.filter((s) => s.text);
  if (!changed) return;
  if (await commit(draft, message(changed))) updateMatches();
}

async function applyGlossary() {
  await settle();
  let glossary;
  try {
    glossary = await api("/api/glossary");
  } catch (err) {
    setStatus(`無法讀取詞彙表：${err.message}`, "", "error");
    return;
  }
  state.glossary = glossary;
  if (!glossary.replacements.length) {
    setStatus("詞彙表沒有取代規則", "在工具列的「詞彙」新增", "error");
    return;
  }
  const draft = currentDraft();
  let changed = 0;
  for (const segment of draft.segments) {
    let text = segment.text;
    for (const rule of glossary.replacements) text = text.split(rule.find).join(rule.replace);
    if (text !== segment.text) changed += 1;
    segment.text = text.replace(/\s+/g, " ").trim();
  }
  draft.segments = draft.segments.filter((s) => s.text);
  if (!changed) {
    setStatus("套用詞彙表：沒有需要改的地方", "");
    return;
  }
  if (await commit(draft, `套用詞彙表：改了 ${changed} 段`)) updateMatches();
}

function bindFind() {
  $("find-btn").addEventListener("click", () => ($("find-bar").hidden ? openFind() : closeFind()));
  $("find-close").addEventListener("click", () => {
    closeFind();
    $("find-btn").focus();
  });
  $("find-input").addEventListener("input", () => {
    state.find.current = 0;
    updateMatches();
    if (state.find.matches.length) selectSegment(state.find.matches[0], true);
  });
  for (const input of [$("find-input"), $("replace-input")]) {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeFind();
        $("find-btn").focus();
      } else if (event.key === "Enter" && input.id === "find-input") {
        event.preventDefault();
        stepMatch(event.shiftKey ? -1 : 1);
      }
    });
  }
  $("find-prev").addEventListener("click", () => stepMatch(-1));
  $("find-next").addEventListener("click", () => stepMatch(1));
  $("replace-one").addEventListener("click", () => {
    const index = state.find.matches[state.find.current];
    if (index !== undefined) replaceIn([index], () => `已取代第 ${index + 1} 段`);
  });
  $("replace-all").addEventListener("click", () => replaceIn(state.find.matches, (n) => `已取代 ${n} 段`));
  $("apply-glossary").addEventListener("click", applyGlossary);
}

async function loadGlossary() {
  try {
    state.glossary = await api("/api/glossary");
  } catch {
    return;
  }
  renderGlossarySummary();
  updateHotwordsOption();
}

function renderGlossarySummary() {
  const rules = state.glossary.replacements.length;
  $("glossary-summary").textContent = rules ? String(rules) : "";
  $("glossary-btn").title = `${state.glossary.hotwords ? "已設定熱詞" : "沒有熱詞"}，取代規則 ${rules} 條`;
}

function ruleRow(rule) {
  const row = el("tr", "");
  const find = el("input", "tb-input");
  find.value = rule.find;
  find.maxLength = 100;
  find.setAttribute("aria-label", "原文");
  const replace = el("input", "tb-input");
  replace.value = rule.replace;
  replace.maxLength = 100;
  replace.setAttribute("aria-label", "改成");
  const remove = el("button", "link-btn danger", "刪除");
  remove.type = "button";
  remove.addEventListener("click", () => {
    const next = row.nextElementSibling ?? row.previousElementSibling;
    row.remove();
    (next?.querySelector("input") ?? $("rule-add")).focus();
  });
  const cells = [find, replace, remove].map((node) => {
    const cell = el("td", "");
    cell.append(node);
    return cell;
  });
  row.append(...cells);
  return row;
}

let hotwordsTimer = 0;

function countHotwords() {
  clearTimeout(hotwordsTimer);
  const node = $("hotwords-count");
  const text = $("hotwords-input").value.trim();
  const model = modelById(selectedModelId());
  if (!text) {
    node.textContent = "";
    return;
  }
  if (model && !model.hotwords) {
    node.textContent = `${model.name} 不支援熱詞`;
    node.dataset.tone = "error";
    return;
  }
  hotwordsTimer = setTimeout(async () => {
    try {
      const data = await api("/api/hotwords/count", { method: "POST", body: { model: selectedModelId(), text } });
      const over = data.tokens > data.limit;
      node.textContent = `${data.tokens}／${data.limit} token（${model?.name ?? ""}）${over ? "，太長了，請刪減" : ""}`;
      if (over) node.dataset.tone = "error";
      else delete node.dataset.tone;
    } catch (err) {
      node.textContent = err.message;
      node.dataset.tone = "error";
    }
  }, HOTWORDS_COUNT_DELAY_MS);
}

async function openGlossary() {
  await loadGlossary();
  $("hotwords-input").value = state.glossary.hotwords;
  if (state.config) $("hotwords-input").maxLength = state.config.max_hotwords_length;
  $("rule-rows").replaceChildren(...state.glossary.replacements.map(ruleRow));
  $("glossary-status").textContent = "";
  $("glossary-dialog").showModal();
  countHotwords();
}

async function saveGlossary() {
  const replacements = [...$("rule-rows").children]
    .map((row) => {
      const [find, replace] = row.querySelectorAll("input");
      return { find: find.value.trim(), replace: replace.value.trim() };
    })
    .filter((rule) => rule.find);
  const status = $("glossary-status");
  try {
    state.glossary = await api("/api/glossary", {
      method: "PUT",
      body: { hotwords: $("hotwords-input").value, replacements },
    });
    $("rule-rows").replaceChildren(...state.glossary.replacements.map(ruleRow));
    status.textContent = "已儲存";
    delete status.dataset.tone;
    announce("詞彙已儲存");
    renderGlossarySummary();
    updateHotwordsOption();
  } catch (err) {
    status.textContent = `無法儲存：${err.message}`;
    status.dataset.tone = "error";
    announce(status.textContent);
  }
}

function bindGlossary() {
  $("glossary-btn").addEventListener("click", openGlossary);
  $("hotwords-input").addEventListener("input", countHotwords);
  $("rule-add").addEventListener("click", () => {
    const row = ruleRow({ find: "", replace: "" });
    $("rule-rows").append(row);
    row.querySelector("input").focus();
  });
  $("glossary-save").addEventListener("click", saveGlossary);
  $("hotwords-check").addEventListener("change", (event) =>
    storageSet(STORAGE_KEYS.hotwords, event.target.checked ? "on" : "off"),
  );
}

function renderBurn() {
  const render = state.render;
  const running = !!render && !["succeeded", "failed", "cancelled"].includes(render.status);
  $("burn-progress").hidden = !render;
  $("burn-cancel").hidden = !running;
  $("burn-start").disabled = running || !state.burnFile || !editable();
  const download = $("burn-download");
  download.hidden = render?.status !== "succeeded";
  if (render?.status === "succeeded") {
    download.href = `/api/renders/${encodeURIComponent(render.id)}/file`;
    download.setAttribute("download", render.filename);
  }
  if (!render) return;
  $("burn-fill").style.transform = `scaleX(${render.progress ?? 0})`;
  const status = $("burn-status");
  const label = RENDER_LABELS[render.status] ?? render.status;
  status.textContent = render.error ?? (render.status === "running" ? `${label} ${Math.round(render.progress * 100)}%` : label);
  if (render.status === "failed") status.dataset.tone = "error";
  else delete status.dataset.tone;
}

async function pollRender() {
  const render = state.render;
  if (!render) return;
  try {
    state.render = await api(`/api/renders/${encodeURIComponent(render.id)}`);
  } catch (err) {
    state.render = { ...render, status: "failed", error: err.message };
  }
  renderBurn();
  if (["succeeded", "failed", "cancelled"].includes(state.render.status)) {
    announce(`燒錄字幕${RENDER_LABELS[state.render.status]}`);
    return;
  }
  state.renderTimer = setTimeout(pollRender, RENDER_POLL_MS);
}

function chooseBurnFile(file, name) {
  state.burnFile = { blob: file, name };
  $("burn-name").textContent = name;
  renderBurn();
}

async function startBurn() {
  const source = state.burnFile;
  if (!source || !editable()) return;
  await settle();
  clearTimeout(state.renderTimer);
  state.render = { id: "", status: "queued", progress: 0, error: null };
  renderBurn();
  $("burn-status").textContent = "上傳影片…";
  try {
    state.render = await upload(
      `/api/jobs/${encodeURIComponent(state.job.id)}/renders`,
      {},
      source.blob,
      source.name,
      (ratio) => {
        $("burn-status").textContent = ratio < 1 ? `上傳影片 ${Math.round(ratio * 100)}%` : "檢查影片…";
      },
    );
  } catch (err) {
    state.render = { id: "", status: "failed", progress: 0, error: err.message };
    renderBurn();
    announce(err.message);
    return;
  }
  renderBurn();
  state.renderTimer = setTimeout(pollRender, RENDER_POLL_MS);
}

function openBurn() {
  if (state.render && state.render.job_id !== state.job?.id) {
    clearTimeout(state.renderTimer);
    state.render = null;
  }
  const current = previewHoldsJobAudio() && hasVideo();
  $("burn-current").hidden = !current;
  if (!state.render) {
    state.burnFile = null;
    $("burn-name").textContent = current ? "" : "請選擇這份字幕的原始影片";
  }
  renderBurn();
  $("burn-dialog").showModal();
}

function bindBurn() {
  $("burn-btn").addEventListener("click", openBurn);
  $("burn-current").addEventListener("click", () => chooseBurnFile(state.audio.blob, state.audio.name));
  $("burn-input").addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) chooseBurnFile(file, file.name);
  });
  $("burn-start").addEventListener("click", startBurn);
  $("burn-cancel").addEventListener("click", async () => {
    if (!state.render?.id) return;
    try {
      state.render = await api(`/api/renders/${encodeURIComponent(state.render.id)}`, { method: "DELETE" });
      renderBurn();
    } catch (err) {
      $("burn-status").textContent = err.message;
    }
  });
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
bindCueEditor();
bindFind();
bindGlossary();
bindBurn();
refreshTimeline();
refreshConfig();
setInterval(() => {
  if (document.visibilityState === "visible") refreshConfig();
}, CONFIG_REFRESH_MS);
