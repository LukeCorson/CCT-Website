const $ = id => document.getElementById(id);
const $$ = selector => [...document.querySelectorAll(selector)];

let state = {};
let series = [];
let toastTimer;

const deviceInfo = {
  core2: ["Core2 + S3", "Temperature, valves & local safety"],
  licor: ["LI-COR", "CO₂ analyzer"],
  alicat: ["Alicat", "Gas flow meter"],
  calibrator: ["146C", "Gas delivery selector"]
};

const testInfo = {
  zero: ["Zero", "Route nitrogen to the analyzer to establish or verify a zero reading."],
  adsorption: ["Adsorption", "Run the commissioned adsorption route while recording analyzer and flow data."],
  breakthrough: ["Breakthrough", "Measure sample breakthrough under the selected gas and flow conditions."],
  desorption: ["Desorption", "Purge with nitrogen and heat the column to the selected desorption target."],
  breakthrough_cycles: ["Repeating breakthrough", "Repeat breakthrough and regeneration using the configured cutoff and cycle count."]
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"})[char]);
}

async function api(path, method = "GET", body) {
  const response = await fetch(path, {
    method,
    headers: {"Content-Type": "application/json"},
    body: body ? JSON.stringify(body) : undefined
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(data.detail || "Request failed");
  return data;
}

function showToast(message, error = false) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").className = `toast show${error ? " error" : ""}`;
  toastTimer = setTimeout(() => $("toast").className = "toast", 3200);
}

function safe(value, digits = 1) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
}

function duration(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  const hours = String(Math.floor(seconds / 3600)).padStart(2, "0");
  const minutes = String(Math.floor(seconds % 3600 / 60)).padStart(2, "0");
  const remainder = String(seconds % 60).padStart(2, "0");
  return `${hours}:${minutes}:${remainder}`;
}

function switchPage(name) {
  $$(".nav").forEach(button => {
    const active = button.dataset.page === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  $$(".page").forEach(page => page.classList.toggle("active", page.id === `page-${name}`));
  if (name === "results") loadRuns();
  if (name === "live") requestAnimationFrame(drawCharts);
}

$$(".nav").forEach(button => button.onclick = () => switchPage(button.dataset.page));

function render(snapshot) {
  state = snapshot;
  const connected = Boolean(snapshot.connected);
  const running = Boolean(snapshot.running);
  const mode = connected ? (snapshot.mode === "simulation" ? "simulation" : "hardware") : "offline";
  const blockers = snapshot.blockers || [];

  $("mode-pill").textContent = mode === "offline" ? "Offline" : mode === "simulation" ? "Simulation" : "Hardware connected";
  $("recording-state").textContent = snapshot.save_status || "Not recording";
  $("connection-dot").className = `status-dot ${mode === "offline" ? "" : mode}`;

  const notice = $("notice");
  const noticeText = snapshot.stage === "fault" ? (snapshot.waiting_for || "Experiment fault") : snapshot.connection_notice || "Choose a connection to begin.";
  $("notice-text").textContent = noticeText;
  notice.className = `notice${snapshot.stage === "fault" ? " fault" : blockers.length && connected ? " warning" : ""}`;

  $("connect").hidden = connected;
  $("disconnect").hidden = !connected;
  $("connection-mode").disabled = connected;
  $("connection-detail").textContent = connected ? (snapshot.mode === "simulation" ? "Practice data only; no physical equipment is controlled." : "Using the saved local apparatus setup and verified USB identities.") : "Core2 uses the saved local apparatus setup automatically.";

  $("start-test").disabled = !connected || running || blockers.length > 0;
  $("stop-test").disabled = !running;
  $("global-safe").disabled = !connected;
  $("global-safe").textContent = running ? "End test safely" : "Safe state";

  const startMessage = running ? "A test is currently running." : blockers.slice(0, 2).join(" · ") || (connected ? "Ready to start." : "Connect before starting.");
  $("start-status").textContent = startMessage;
  $("blocker-list").textContent = blockers.length ? blockers.slice(0, 3).join(" · ") : connected ? "Connection and required readings are ready." : "Connect the simulator or apparatus.";
  $("blocker-box").classList.toggle("ready", connected && !blockers.length);
  $("blocker-box").querySelector("strong").textContent = connected && !blockers.length ? "Ready" : "Before starting";

  const locked = !connected || running;
  $$(".hardware-control").forEach(control => control.disabled = locked);
  $("control-lock").className = `safety-callout${locked ? "" : " ready"}`;
  $("control-lock").innerHTML = locked
    ? `<strong>Controls locked</strong><span>${running ? "A test is running. Use End test safely before manual control." : "Connect first. Manual controls remain locked while offline."}</span>`
    : "<strong>Manual control ready</strong><span>Commands are recorded and require Core2 acknowledgement.</span>";

  const devices = snapshot.devices || {};
  $("devices").innerHTML = ["core2", "licor", "alicat", "calibrator"].map(key => {
    const device = devices[key] || {};
    const ok = device.connected && device.fresh;
    const status = ok ? "Ready" : device.connected ? "Waiting" : device.required === false ? "Manual" : "Offline";
    return `<article class="device"><div class="top"><div><b>${deviceInfo[key][0]}</b><em>${status}</em></div><i class="device-dot ${ok ? "ok" : device.connected ? "warn" : ""}"></i></div><p>${escapeHtml(device.message || deviceInfo[key][1])}</p></article>`;
  }).join("");

  const values = snapshot.values || {};
  $("co2-value").textContent = safe(values.co2_ppm);
  $("flow-value").textContent = safe(values.flow_sccm);
  $("temp-value").textContent = safe(values.column_temp_c);
  $("elapsed").textContent = duration(snapshot.elapsed_s);
  $("cycle-value").textContent = snapshot.cycle || "—";
  $("cycle-total").textContent = snapshot.total_cycles ? `of ${snapshot.total_cycles}` : "of —";
  $("live-stage").textContent = snapshot.stage_label || "Idle";
  $("live-waiting").textContent = snapshot.waiting_for || "Connect and configure the experiment.";

  const outputs = snapshot.outputs || {};
  $("route-readback").textContent = outputs.route || "—";
  $("gas-readback").textContent = outputs.gas || "—";
  $("heater-readback").textContent = outputs.heater_target_c !== null && outputs.heater_target_c !== undefined && Number.isFinite(Number(outputs.heater_target_c)) ? `${Number(outputs.heater_target_c).toFixed(0)} °C` : "Off";
  $("heater-state").textContent = outputs.heater_on ? "On" : "Off";
  $("s3-state").textContent = outputs.s3_online === true ? "Online" : outputs.s3_online === false ? "Offline" : "—";
  if (outputs.valves) Object.entries(outputs.valves).forEach(([name, on]) => {
    const control = $(`valve-${name}`);
    if (control) control.value = on ? "on" : "off";
  });

  series = (snapshot.series || []).slice(-600);
  if ($("page-live").classList.contains("active")) requestAnimationFrame(drawCharts);

  const events = (snapshot.events || []).slice(-30).reverse();
  $("activity").innerHTML = events.length ? events.map(event => `<div class="event"><time>${duration(event.elapsed_s)}</time><b>${escapeHtml(String(event.event || "event").replaceAll("_", " "))}</b><span>${escapeHtml(event.message || "")}</span></div>`).join("") : '<div class="empty-state"><span>No activity yet.</span></div>';
}

function drawCharts() {
  drawChart($("co2-chart"), "co2_ppm", "#087d7e", "ppm");
  drawChart($("flow-chart"), "flow_sccm", "#a96d13", "sccm");
}

function drawChart(canvas, key, color, unit) {
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 20) return;
  const dpr = window.devicePixelRatio || 1;
  const width = rect.width;
  const height = 210;
  canvas.width = Math.floor(width * dpr);
  canvas.height = height * dpr;
  const context = canvas.getContext("2d");
  context.scale(dpr, dpr);
  context.clearRect(0, 0, width, height);
  context.font = "10px Segoe UI, Arial";
  context.fillStyle = "#83959d";
  context.strokeStyle = "#e7edef";
  context.lineWidth = 1;
  for (let row = 0; row < 4; row++) {
    const y = 16 + row * 55;
    context.beginPath(); context.moveTo(42, y); context.lineTo(width - 6, y); context.stroke();
  }
  const points = series.map((record, index) => [index, Number(record[key] ?? record.values?.[key])]).filter(point => Number.isFinite(point[1]));
  if (points.length < 2) {
    context.textAlign = "center";
    context.fillText("Waiting for recorded values", width / 2, height / 2);
    return;
  }
  const values = points.map(point => point[1]);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = maximum - minimum || 1;
  context.textAlign = "left";
  context.fillText(`${maximum.toFixed(1)} ${unit}`, 2, 13);
  context.fillText(`${minimum.toFixed(1)}`, 2, height - 5);
  context.strokeStyle = color;
  context.lineWidth = 2;
  context.lineJoin = "round";
  context.beginPath();
  points.forEach((point, index) => {
    const x = 42 + point[0] / Math.max(1, series.length - 1) * (width - 50);
    const y = height - 14 - (point[1] - minimum) / span * (height - 30);
    index ? context.lineTo(x, y) : context.moveTo(x, y);
  });
  context.stroke();
}

function generatedName() {
  const date = new Date().toISOString().slice(0, 10).replaceAll("-", "");
  const part = value => (value || "").trim().replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "") || "unknown";
  return `${date}-${part($("test-type").value)}-${part($("sample-id").value)}-${part($("operator").value)}`;
}

let nameEdited = false;
function updateRunReview() {
  const kind = $("test-type").value;
  if (!nameEdited) $("run-name").value = generatedName();
  $("cycle-settings").hidden = kind !== "breakthrough_cycles";
  $("test-description").textContent = testInfo[kind][1];
  $("summary-test").textContent = testInfo[kind][0];
  $("summary-sample").textContent = $("sample-id").value.trim() || "Not entered";
  $("summary-operator").textContent = $("operator").value.trim() || "Not entered";
  $("summary-temp").textContent = `${safe($("desorption-temp").value, 0)} °C`;
}

["test-type", "sample-id", "operator", "desorption-temp"].forEach(id => $(id).addEventListener("input", updateRunReview));
$("run-name").addEventListener("input", () => nameEdited = true);
updateRunReview();

async function perform(statusId, workingMessage, successMessage, operation) {
  const target = statusId ? $(statusId) : null;
  try {
    if (target) target.textContent = workingMessage;
    const result = await operation();
    if (target) target.textContent = successMessage;
    showToast(successMessage);
    return result;
  } catch (error) {
    if (target) target.textContent = error.message;
    showToast(error.message, true);
    throw error;
  }
}

$("connect").onclick = () => perform("start-status", "Connecting…", "Connected.", () => api("/api/connect", "POST", {mode: $("connection-mode").value})).catch(() => {});
$("disconnect").onclick = () => perform("start-status", "Disconnecting…", "Disconnected.", () => api("/api/disconnect", "POST")).catch(() => {});

$("start-test").onclick = async () => {
  const config = {
    test_type: $("test-type").value,
    operator: $("operator").value,
    run_name: $("run-name").value,
    sample_id: $("sample-id").value,
    sample_mass_g: Number($("sample-mass").value),
    desorption_target_c: Number($("desorption-temp").value),
    notes: $("notes").value,
    cycles: Number($("cycles").value),
    breakthrough_pct: Number($("breakthrough-pct").value),
    initial_treatment: $("initial-treatment").value,
    final_purge: $("final-purge").checked,
    stage_timeout_s: Number($("timeout").value),
    max_temperature_c: Number($("temp-limit").value)
  };
  try {
    await perform("start-status", "Starting and opening the record…", "Test started; recording locally.", () => api("/api/tests/start", "POST", {config}));
    switchPage("live");
  } catch (_) {}
};

async function stopTest() {
  try { await perform(null, "", "Controlled shutdown requested.", () => api("/api/tests/stop", "POST")); } catch (_) {}
}
$("stop-test").onclick = stopTest;
$("global-safe").onclick = () => state.running ? stopTest() : manual("safe_state", {});

function manual(action, arguments_) {
  return perform("control-status", "Sending command to Core2…", "Command queued; waiting for Core2 acknowledgement.", () => api("/api/manual", "POST", {action, arguments: arguments_})).catch(() => {});
}

$("apply-route").onclick = () => manual("route", {name: $("route").value});
$("apply-gas").onclick = () => manual("gas", {name: $("gas").value});
$("apply-heater").onclick = () => {
  const value = $("heater-target").value;
  return manual("heater", {target_c: value === "off" ? null : Number(value)});
};
$("safe-state").onclick = () => manual("safe_state", {});
$$('[data-valve]').forEach(button => button.onclick = () => {
  const name = button.dataset.valve;
  return manual("valve", {name, on: $(`valve-${name}`).value === "on"});
});

async function loadRuns() {
  try {
    const rows = await api("/api/runs");
    $("runs").innerHTML = rows.length ? rows.map(run => `<div class="run-row"><b>${escapeHtml(run.run_name || run.id)}</b><span>${escapeHtml(run.test_type || "test")}</span><span>${escapeHtml(run.operator || "—")}</span><small class="run-status">${escapeHtml(run.status || "saved")}</small></div>`).join("") : '<div class="empty-state"><strong>No saved tests yet</strong><span>Completed tests will appear here automatically.</span></div>';
  } catch (error) {
    $("runs").innerHTML = `<div class="empty-state"><strong>Records unavailable</strong><span>${escapeHtml(error.message)}</span></div>`;
  }
}
$("refresh-runs").onclick = loadRuns;

function connectSocket() {
  const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/state`);
  socket.onmessage = event => render(JSON.parse(event.data));
  socket.onclose = () => {
    $("mode-pill").textContent = "Reconnecting";
    $("connection-dot").className = "status-dot";
    setTimeout(connectSocket, 1500);
  };
}

connectSocket();
window.addEventListener("resize", () => state.series && requestAnimationFrame(drawCharts));
