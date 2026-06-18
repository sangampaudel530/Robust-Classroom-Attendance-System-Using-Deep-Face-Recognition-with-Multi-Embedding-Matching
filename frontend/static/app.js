/**
 * app.js — Face Attendance System v2.0 Frontend
 * New: video attendance tab, accuracy metrics tab, enrollment quality badges
 */

const API = "/api";

function $(sel, ctx = document) { return ctx.querySelector(sel); }
function $$(sel, ctx = document) { return [...ctx.querySelectorAll(sel)]; }

function toast(msg, type = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `toast ${type} show`;
  setTimeout(() => el.classList.remove("show"), 3500);
}

function fmtDate(d) {
  return new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

async function api(path, opts = {}) {
  const r = await fetch(API + path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
  return data;
}

function todayStr() { return new Date().toISOString().slice(0, 10); }

// ── Clock ─────────────────────────────────────────────────────────
function startClock() {
  const el = $("#clock");
  const tick = () => { el.textContent = new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); };
  tick(); setInterval(tick, 1000);
}

// ── Navigation ────────────────────────────────────────────────────
function initNav() {
  $$(".nav-item").forEach(link => {
    link.addEventListener("click", e => {
      e.preventDefault();
      const tab = link.dataset.tab;
      $$(".nav-item").forEach(l => l.classList.remove("active"));
      $$(".tab").forEach(t => t.classList.remove("active"));
      link.classList.add("active");
      $(`#tab-${tab}`).classList.add("active");
      if (tab === "students")        loadStudents();
      if (tab === "dashboard")       loadDashboard();
      if (tab === "records")         { const el = $("#records-date"); if (el && !el.value) el.value = todayStr(); }
      if (tab === "active-learning") loadActiveLearningCandidates();
      if (tab === "metrics")         loadMetrics();
    });
  });
}

function switchTab(name) { $(`.nav-item[data-tab="${name}"]`).click(); }

// ── Dashboard ─────────────────────────────────────────────────────
async function loadDashboard() {
  const today = todayStr();
  $("#today-badge").textContent = fmtDate(today);
  if ($("#att-date")) $("#att-date").value = today;
  if ($("#vid-date")) $("#vid-date").value = today;

  try {
    const [studentsData, attData] = await Promise.all([
      api("/students"),
      api(`/attendance/${today}`).catch(() => ({ records: [], present: 0, absent: 0, total: 0 }))
    ]);
    const total   = studentsData.total || 0;
    const present = attData.present ?? 0;
    const pct     = total > 0 ? Math.round(present / total * 100) : 0;

    $("#stat-total").textContent   = total;
    $("#stat-present").textContent = present;
    $("#stat-absent").textContent  = attData.absent ?? 0;
    $("#stat-pct").textContent     = `${pct}%`;

    const records  = attData.records || [];
    const resetBtn = $("#dashboard-reset-btn");
    if (records.length === 0) {
      if (resetBtn) resetBtn.style.display = "none";
      $("#dashboard-table-wrap").innerHTML = `<p class="muted">No attendance recorded today yet. <a href="#" onclick="switchTab('attendance')">Take attendance →</a></p>`;
    } else {
      if (resetBtn) resetBtn.style.display = "inline-flex";
      $("#dashboard-table-wrap").innerHTML = buildAttTable(records);
    }
  } catch (e) {
    toast("Failed to load dashboard: " + e.message, "error");
  }
}

// ── Student Enrollment ─────────────────────────────────────────────
function initEnroll() {
  const zone    = $("#enroll-upload-zone");
  const input   = $("#enroll-photos");
  const preview = $("#enroll-preview");
  const btn     = $("#enroll-btn");
  const result  = $("#enroll-result");

  zone.addEventListener("click", () => input.click());
  zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("drag-over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
  zone.addEventListener("drop", e => {
    e.preventDefault(); zone.classList.remove("drag-over");
    showPhotoPreview([...e.dataTransfer.files], preview);
    const dt = new DataTransfer();
    [...e.dataTransfer.files].forEach(f => dt.items.add(f));
    input.files = dt.files;
  });
  input.addEventListener("change", () => showPhotoPreview([...input.files], preview));

  btn.addEventListener("click", async () => {
    const roll = $("#enroll-roll").value.trim();
    const name = $("#enroll-name").value.trim();
    if (!roll || !name) { toast("Roll number and name are required.", "error"); return; }
    if (!input.files.length) { toast("Please select at least one photo.", "error"); return; }

    const fd = new FormData();
    fd.append("roll_no", roll); fd.append("name", name);
    [...input.files].forEach(f => fd.append("photos", f));

    btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Enrolling…';
    result.style.display = "none";

    try {
      const data = await api("/students/enroll", { method: "POST", body: fd });
      const qualityColor = data.enrollment_quality === "good" ? "var(--green)" : data.enrollment_quality === "fair" ? "var(--orange)" : "var(--red)";
      result.className = "result-box success";
      result.innerHTML = `
        <strong><i class="fa-solid fa-circle-check"></i> Enrolled!</strong><br>
        Roll: <b>${data.roll_no}</b> — ${data.name}<br>
        Photos processed: <b>${data.photos_processed}</b>
        <span style="color:${qualityColor};font-weight:700;margin-left:8px">(${data.enrollment_quality} quality)</span>
        ${data.warning ? `<br><span style="color:var(--orange);font-size:12px">⚠️ ${data.warning}</span>` : ""}
      `;
      result.style.display = "block";
      toast("Student enrolled!", "success");
      $("#enroll-roll").value = ""; $("#enroll-name").value = "";
      input.value = ""; preview.innerHTML = "";
    } catch (e) {
      result.className = "result-box error";
      result.innerHTML = `<strong><i class="fa-solid fa-triangle-exclamation"></i> Error:</strong> ${e.message}`;
      result.style.display = "block";
      toast("Enrollment failed.", "error");
    } finally {
      btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-user-plus"></i> Enroll Student';
    }
  });
}

function showPhotoPreview(files, container) {
  container.innerHTML = "";
  files.slice(0, 10).forEach(f => {
    const img = document.createElement("img");
    img.src = URL.createObjectURL(f);
    container.appendChild(img);
  });
}

// ── Manage Students ───────────────────────────────────────────────
async function loadStudents() {
  const wrap = $("#students-table-wrap");
  wrap.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const data = await api("/students");
    if (!data.students.length) { wrap.innerHTML = '<p class="muted">No students enrolled yet.</p>'; return; }
    wrap.innerHTML = buildStudentTable(data.students);
    attachStudentTableEvents();
  } catch (e) {
    wrap.innerHTML = `<p class="muted" style="color:var(--red)">Error: ${e.message}</p>`;
  }
}

function buildStudentTable(students) {
  const qualityBadge = (q) => {
    const colors = { good: "#16A34A", fair: "#D97706", poor: "#DC2626", none: "#9CA3AF" };
    return `<span style="color:${colors[q]||"#9CA3AF"};font-weight:700;font-size:12px">${q || "?"}</span>`;
  };
  return `<div class="table-wrap"><table>
    <thead><tr><th>Roll No</th><th>Name</th><th>Photos</th><th>Enrolled</th><th>Status</th><th>Actions</th></tr></thead>
    <tbody>
      ${students.map(s => `<tr data-roll="${s.roll_no}">
        <td><strong>${s.roll_no}</strong></td>
        <td>${s.name}</td>
        <td>${s.enrollment_photos || "?"} ${qualityBadge(s.enrollment_quality)}</td>
        <td>${s.enrolled_at ? fmtDate(s.enrolled_at) : "—"}</td>
        <td><span class="pill ${s.is_active ? "pill-p" : "pill-a"}">${s.is_active ? "Active" : "Inactive"}</span></td>
        <td>
          <button class="action-btn" title="View attendance" data-action="view" data-roll="${s.roll_no}"><i class="fa-solid fa-eye"></i></button>
          <button class="action-btn danger" title="Remove" data-action="remove" data-roll="${s.roll_no}" data-name="${s.name}"><i class="fa-solid fa-user-minus"></i></button>
        </td>
      </tr>`).join("")}
    </tbody>
  </table></div>`;
}

function attachStudentTableEvents() {
  $$("[data-action='remove']").forEach(btn => btn.addEventListener("click", () => confirmRemove(btn.dataset.roll, btn.dataset.name)));
  $$("[data-action='view']").forEach(btn => btn.addEventListener("click", () => viewStudentAttendance(btn.dataset.roll)));
  $("#student-search").addEventListener("input", e => {
    const q = e.target.value.toLowerCase();
    $$("#students-table-wrap tbody tr").forEach(row => { row.style.display = row.textContent.toLowerCase().includes(q) ? "" : "none"; });
  });
}

function confirmRemove(roll, name) {
  const existing = $(".modal-backdrop"); if (existing) existing.remove();
  const modal = document.createElement("div");
  modal.className = "modal-backdrop";
  modal.innerHTML = `<div class="modal" style="max-width:480px">
    <h2><i class="fa-solid fa-user-minus" style="color:var(--red);margin-right:8px"></i>Remove Student</h2>
    <p style="margin-bottom:12px">Remove <strong>${name}</strong> (${roll}):</p>
    <div class="remove-options">
      <label class="remove-option selected" id="opt-soft">
        <input type="radio" name="remove-mode" value="soft" checked/>
        <div class="remove-option-body">
          <strong><i class="fa-solid fa-eye-slash"></i> Remove from Roster</strong>
          <span>Keeps photos and attendance history. Can re-enroll later.</span>
        </div>
      </label>
      <label class="remove-option danger" id="opt-hard">
        <input type="radio" name="remove-mode" value="hard"/>
        <div class="remove-option-body">
          <strong><i class="fa-solid fa-trash-can"></i> Delete Permanently</strong>
          <span>Deletes everything. Cannot be undone.</span>
        </div>
      </label>
    </div>
    <div class="modal-actions" style="margin-top:20px">
      <button class="btn btn-ghost" id="modal-cancel">Cancel</button>
      <button class="btn btn-danger" id="modal-confirm">Remove from Roster</button>
    </div>
  </div>`;
  document.body.appendChild(modal);

  const radios = modal.querySelectorAll("input[name='remove-mode']");
  const confirmBtn = modal.querySelector("#modal-confirm");
  radios.forEach(r => r.addEventListener("change", () => {
    const isHard = modal.querySelector("input[name='remove-mode']:checked").value === "hard";
    modal.querySelector("#opt-soft").classList.toggle("selected", !isHard);
    modal.querySelector("#opt-hard").classList.toggle("selected", isHard);
    confirmBtn.textContent = isHard ? "Delete Permanently" : "Remove from Roster";
  }));

  modal.querySelector("#modal-cancel").onclick = () => modal.remove();
  confirmBtn.onclick = async () => {
    const mode = modal.querySelector("input[name='remove-mode']:checked").value;
    const keepHistory = mode === "soft";
    if (!keepHistory && !confirm(`PERMANENT DELETE: all data for ${name} (${roll}) will be deleted. Proceed?`)) return;

    confirmBtn.disabled = true; confirmBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
    try {
      const res = await api(`/students/${roll}?keep_history=${keepHistory}`, { method: "DELETE" });
      toast(keepHistory ? `${name} removed from roster.` : `${name} permanently deleted.`, "success");
      modal.remove(); loadStudents(); loadDashboard();
    } catch (e) { toast("Remove failed: " + e.message, "error"); confirmBtn.disabled = false; confirmBtn.textContent = "Confirm Remove"; }
  };
}

async function viewStudentAttendance(roll) {
  try {
    const data = await api(`/attendance/student/${roll}`);
    switchTab("records");
    const wrap = $("#records-wrap");
    wrap.innerHTML = `
      <div style="margin-bottom:16px">
        <strong>${data.name}</strong> (${data.roll_no}) —
        Present: <span style="color:var(--green)">${data.present}</span> / ${data.total_days} days
        <span class="pill ${data.percentage >= 75 ? "pill-p" : "pill-a"}" style="margin-left:8px">${data.percentage}%</span>
      </div>${buildAttTable(data.records)}`;
  } catch (e) { toast("Could not load: " + e.message, "error"); }
}

// ── Photo Attendance ───────────────────────────────────────────────
function initAttendance() {
  const zone  = $("#att-upload-zone"), input = $("#att-photo");
  zone.addEventListener("click", () => input.click());
  zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("drag-over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
  zone.addEventListener("drop", e => {
    e.preventDefault(); zone.classList.remove("drag-over");
    const f = e.dataTransfer.files[0];
    if (f) { previewClassPhoto(f); const dt = new DataTransfer(); dt.items.add(f); input.files = dt.files; }
  });
  input.addEventListener("change", () => { if (input.files[0]) previewClassPhoto(input.files[0]); });
  $("#att-process-btn").addEventListener("click", processAttendance);
  $("#att-clear-btn").addEventListener("click", clearAttendancePanel);
}

function previewClassPhoto(file) {
  const wrap = $("#att-preview-wrap"), img = $("#att-preview-img");
  img.src = URL.createObjectURL(file);
  wrap.style.display = "block";
  $("#att-result").style.display = "none";
  $("#att-clear-btn").style.display = "none";
}

function clearAttendancePanel() {
  $("#att-photo").value = "";
  const img = $("#att-preview-img");
  if (img.src) { URL.revokeObjectURL(img.src); img.src = ""; }
  $("#att-preview-wrap").style.display = "none";
  $("#att-result").style.display = "none";
  $("#att-clear-btn").style.display = "none";
  toast("Cleared.", "");
}

async function processAttendance() {
  const input = $("#att-photo"), date = $("#att-date").value;
  const btn = $("#att-process-btn"), spinner = $("#att-spinner"), result = $("#att-result");
  if (!input.files[0]) { toast("Please upload a class photo first.", "error"); return; }

  const fd = new FormData();
  fd.append("photo", input.files[0]); if (date) fd.append("date", date);

  btn.disabled = true; spinner.style.display = "block"; result.style.display = "none";
  try {
    const data = await api("/attendance/process", { method: "POST", body: fd });
    $("#r-present").textContent  = data.present;
    $("#r-absent").textContent   = data.absent;
    $("#r-detected").textContent = data.faces_detected;
    $("#r-spoofs").textContent   = data.spoofs_rejected;
    $("#att-details-wrap").innerHTML = buildAttTable(data.details);
    result.style.display = "block";
    $("#att-clear-btn").style.display = "inline-flex";
    toast(`Attendance recorded: ${data.present} present, ${data.absent} absent.`, "success");
    loadDashboard();
  } catch (e) {
    toast("Processing failed: " + e.message, "error");
    $("#att-clear-btn").style.display = "inline-flex";
  } finally { btn.disabled = false; spinner.style.display = "none"; }
}

// ── Video Attendance (NEW) ─────────────────────────────────────────
function initVideo() {
  const zone  = $("#vid-upload-zone"), input = $("#vid-file");
  if (!zone || !input) return;
  zone.addEventListener("click", () => input.click());
  zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("drag-over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
  zone.addEventListener("drop", e => {
    e.preventDefault(); zone.classList.remove("drag-over");
    const f = e.dataTransfer.files[0];
    if (f) { const dt = new DataTransfer(); dt.items.add(f); input.files = dt.files; showVideoFilename(f); }
  });
  input.addEventListener("change", () => { if (input.files[0]) showVideoFilename(input.files[0]); });
  $("#vid-process-btn").addEventListener("click", processVideo);
  $("#vid-clear-btn").addEventListener("click", () => {
    input.value = ""; $("#vid-filename").style.display = "none";
    $("#vid-result").style.display = "none"; $("#vid-clear-btn").style.display = "none";
  });
}

function showVideoFilename(file) {
  const el = $("#vid-filename");
  el.textContent = `📹 ${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
  el.style.display = "block";
  $("#vid-result").style.display = "none";
}

async function processVideo() {
  const input = $("#vid-file"), date = $("#vid-date").value;
  const btn = $("#vid-process-btn"), spinner = $("#vid-spinner"), result = $("#vid-result");
  if (!input.files[0]) { toast("Please upload a video first.", "error"); return; }

  const fd = new FormData();
  fd.append("video", input.files[0]); if (date) fd.append("date", date);

  btn.disabled = true; spinner.style.display = "block"; result.style.display = "none";
  toast("Processing video… this may take a minute.", "");

  try {
    const data = await api("/attendance/process-video", { method: "POST", body: fd });
    $("#vr-present").textContent = data.present;
    $("#vr-absent").textContent  = data.absent;
    $("#vr-frames").textContent  = data.frames_processed || "—";
    $("#vr-faces").textContent   = data.faces_detected;
    $("#vid-details-wrap").innerHTML = buildAttTable(data.details);
    result.style.display = "block";
    $("#vid-clear-btn").style.display = "inline-flex";
    toast(`Video processed: ${data.present} present, ${data.absent} absent.`, "success");
    loadDashboard();
  } catch (e) {
    toast("Video processing failed: " + e.message, "error");
  } finally { btn.disabled = false; spinner.style.display = "none"; }
}

// ── Records ────────────────────────────────────────────────────────
async function loadRecords() {
  const date = $("#records-date").value;
  if (!date) { toast("Please select a date.", "error"); return; }
  const wrap = $("#records-wrap");
  wrap.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const data = await api(`/attendance/${date}`);
    if (!data.records.length) { wrap.innerHTML = `<p class="muted">No records for ${fmtDate(date)}.</p>`; return; }
    wrap.innerHTML = `
      <div style="margin-bottom:14px;display:flex;gap:16px;align-items:center">
        <span>${fmtDate(date)}</span>
        <span class="pill pill-p">Present: ${data.present}</span>
        <span class="pill pill-a">Absent: ${data.absent}</span>
      </div>${buildAttTable(data.records, true)}`;
    attachOverrideEvents(date);
  } catch (e) { wrap.innerHTML = `<p class="muted" style="color:var(--red)">Error: ${e.message}</p>`; }
}

function attachOverrideEvents(date) {
  $$("[data-override]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const fd = new FormData(); fd.append("status", btn.dataset.status);
      try {
        await api(`/attendance/${date}/${btn.dataset.roll}`, { method: "PUT", body: fd });
        toast(`Updated ${btn.dataset.roll}.`, "success"); loadRecords();
      } catch (e) { toast("Override failed: " + e.message, "error"); }
    });
  });
}

function buildAttTable(records, withOverride = false) {
  if (!records.length) return '<p class="muted">No records.</p>';
  return `<div class="table-wrap"><table>
    <thead><tr><th>Roll No</th><th>Name</th><th>Status</th><th>Confidence</th>${withOverride ? "<th>Override</th>" : ""}</tr></thead>
    <tbody>${records.map(r => {
      const confPct = r.confidence ? Math.round(r.confidence * 100) : 0;
      return `<tr>
        <td><strong>${r.roll_no}</strong></td>
        <td>${r.name || "—"}</td>
        <td><span class="pill ${r.status === "P" ? "pill-p" : "pill-a"}">${r.status === "P" ? "Present" : "Absent"}</span></td>
        <td><div class="conf-bar-wrap"><div class="conf-bar"><div class="conf-bar-fill" style="width:${confPct}%"></div></div><span style="font-size:12px;color:var(--text2)">${confPct}%</span></div></td>
        ${withOverride ? `<td>
          <button class="action-btn" data-override data-roll="${r.roll_no}" data-status="P" title="Mark Present">✅</button>
          <button class="action-btn" data-override data-roll="${r.roll_no}" data-status="A" title="Mark Absent">❌</button>
        </td>` : ""}
      </tr>`;
    }).join("")}</tbody>
  </table></div>`;
}

// ── Active Learning ────────────────────────────────────────────────
async function loadActiveLearningCandidates() {
  const wrap = $("#al-candidates-wrap");
  wrap.innerHTML = '<p class="muted"><i class="fa-solid fa-spinner fa-spin"></i> Loading…</p>';
  try {
    const [candidatesData, studentsData] = await Promise.all([api("/students/active-learning/candidates"), api("/students")]);
    const candidates = candidatesData.candidates || [], students = studentsData.students || [];
    if (!candidates.length) {
      wrap.innerHTML = `<div class="result-box success" style="margin-top:0"><i class="fa-solid fa-circle-check"></i> <b>All caught up!</b> No unrecognized face candidates pending.</div>`;
      return;
    }
    let html = `<div class="al-candidates-grid">`;
    candidates.forEach(c => {
      const options = students.map(s => `<option value="${s.roll_no}" ${s.roll_no === c.suggested_roll_no ? "selected" : ""}>${s.roll_no} — ${s.name}</option>`).join("");
      html += `<div class="al-candidate-card" id="al-candidate-${c.id}">
        <div class="al-candidate-row">
          <img src="/${c.face_crop_path}" class="al-candidate-crop" alt="Face crop"/>
          <div class="al-candidate-info">
            <span>Date: <b>${fmtDate(c.class_date)}</b></span>
            <span>${c.suggested_roll_no ? `Suggested: <b>${c.suggested_name}</b> (${Math.round(c.suggested_confidence * 100)}% match)` : "No suggestion"}</span>
          </div>
        </div>
        <div class="form-group" style="margin-bottom:0">
          <label style="margin-bottom:4px">Assign Student:</label>
          <select class="al-candidate-select" id="al-select-${c.id}">
            <option value="">-- Select Student --</option>${options}
          </select>
        </div>
        <div class="al-candidate-actions">
          <button class="btn btn-ghost btn-sm" onclick="rejectCandidate('${c.id}')"><i class="fa-solid fa-trash"></i> Ignore</button>
          <button class="btn btn-primary btn-sm" onclick="confirmCandidate('${c.id}')" id="al-confirm-btn-${c.id}"><i class="fa-solid fa-circle-check"></i> Confirm & Train</button>
        </div>
      </div>`;
    });
    html += "</div>";
    wrap.innerHTML = html;
  } catch (e) { wrap.innerHTML = `<p class="muted" style="color:var(--red)">Error: ${e.message}</p>`; }
}

async function confirmCandidate(id) {
  const roll = $(`#al-select-${id}`).value;
  if (!roll) { toast("Select a student first.", "error"); return; }
  const btn = $(`#al-confirm-btn-${id}`); btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
  try {
    const fd = new FormData(); fd.append("candidate_id", id); fd.append("roll_no", roll);
    await api("/students/active-learning/confirm", { method: "POST", body: fd });
    toast("Model updated!", "success");
    setTimeout(loadActiveLearningCandidates, 300);
  } catch (e) { toast(e.message, "error"); btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-circle-check"></i> Confirm & Train'; }
}

async function rejectCandidate(id) {
  if (!confirm("Ignore and delete this face crop?")) return;
  try {
    const fd = new FormData(); fd.append("candidate_id", id);
    await api("/students/active-learning/reject", { method: "POST", body: fd });
    toast("Candidate ignored.", "success"); setTimeout(loadActiveLearningCandidates, 300);
  } catch (e) { toast(e.message, "error"); }
}

// ── Metrics (NEW) ─────────────────────────────────────────────────
function initMetrics() {
  const zone = $("#eval-upload-zone"), input = $("#eval-photo");
  if (!zone || !input) return;
  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    if (input.files[0]) zone.innerHTML = `<i class="fa-solid fa-circle-check" style="color:var(--green);font-size:24px"></i><p style="color:var(--green)">Photo selected: ${input.files[0].name}</p>`;
  });
  $("#eval-btn").addEventListener("click", runEvaluation);
  if ($("#eval-date")) $("#eval-date").value = todayStr();
}

async function runEvaluation() {
  const input = $("#eval-photo"), gt = $("#eval-ground-truth").value.trim();
  const date  = $("#eval-date").value;
  if (!input.files[0]) { toast("Upload a class photo first.", "error"); return; }
  if (!gt) { toast("Enter the ground truth roll numbers.", "error"); return; }

  const btn = $("#eval-btn"), spinner = $("#eval-spinner"), result = $("#eval-result");
  btn.disabled = true; spinner.style.display = "block"; result.style.display = "none";

  const fd = new FormData();
  fd.append("photo", input.files[0]);
  fd.append("ground_truth_rolls", gt);
  if (date) fd.append("date", date);

  try {
    const data = await api("/attendance/evaluate", { method: "POST", body: fd });
    const m = data.metrics || {};
    result.innerHTML = `
      <div class="stats-grid" style="margin:0">
        <div class="stat-card blue" style="padding:14px"><div class="stat-value" style="font-size:22px">${(m.precision * 100 || 0).toFixed(1)}%</div><div class="stat-label">Precision</div></div>
        <div class="stat-card green" style="padding:14px"><div class="stat-value" style="font-size:22px">${(m.recall * 100 || 0).toFixed(1)}%</div><div class="stat-label">Recall</div></div>
        <div class="stat-card purple" style="padding:14px"><div class="stat-value" style="font-size:22px">${(m.f1_score * 100 || 0).toFixed(1)}%</div><div class="stat-label">F1 Score</div></div>
        <div class="stat-card red" style="padding:14px"><div class="stat-value" style="font-size:22px">${m.false_negatives || 0}</div><div class="stat-label">Missed Students</div></div>
      </div>
      <p style="font-size:12px;color:var(--text2);margin-top:10px">Saved to history. TP=${m.true_positives} FP=${m.false_positives} FN=${m.false_negatives}</p>
    `;
    result.style.display = "block";
    toast("Evaluation complete!", "success");
    loadMetrics();
  } catch (e) { toast("Evaluation failed: " + e.message, "error"); }
  finally { btn.disabled = false; spinner.style.display = "none"; }
}

async function loadMetrics() {
  try {
    const [summary, history] = await Promise.all([
      api("/attendance/metrics/summary").catch(() => null),
      api("/attendance/metrics/history").catch(() => ({ sessions: [] }))
    ]);

    const summaryWrap = $("#metrics-summary");
    if (summary && summary.total_sessions > 0) {
      summaryWrap.innerHTML = `
        <div class="stats-grid" style="margin-bottom:0">
          <div class="stat-card blue" style="padding:14px"><div class="stat-value" style="font-size:22px">${(summary.avg_precision * 100).toFixed(1)}%</div><div class="stat-label">Avg Precision</div></div>
          <div class="stat-card green" style="padding:14px"><div class="stat-value" style="font-size:22px">${(summary.avg_recall * 100).toFixed(1)}%</div><div class="stat-label">Avg Recall</div></div>
          <div class="stat-card purple" style="padding:14px"><div class="stat-value" style="font-size:22px">${(summary.avg_f1 * 100).toFixed(1)}%</div><div class="stat-label">Avg F1</div></div>
          <div class="stat-card" style="padding:14px"><div class="stat-value" style="font-size:22px">${summary.total_sessions}</div><div class="stat-label">Sessions</div></div>
        </div>`;
    } else {
      summaryWrap.innerHTML = "";
    }

    const histWrap = $("#metrics-history-wrap");
    const sessions = history.sessions || [];
    if (!sessions.length) { histWrap.innerHTML = '<p class="muted">No evaluation sessions yet.</p>'; return; }
    histWrap.innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>Date</th><th>GT Present</th><th>Predicted</th><th>TP</th><th>FP</th><th>FN</th><th>Precision</th><th>Recall</th><th>F1</th></tr></thead>
      <tbody>${sessions.map(s => `<tr>
        <td>${fmtDate(s.eval_date)}</td>
        <td>${s.ground_truth_present}</td>
        <td>${s.predicted_present}</td>
        <td style="color:var(--green)">${s.true_positives}</td>
        <td style="color:var(--orange)">${s.false_positives}</td>
        <td style="color:var(--red)">${s.false_negatives}</td>
        <td>${(s.precision * 100).toFixed(1)}%</td>
        <td>${(s.recall * 100).toFixed(1)}%</td>
        <td><strong>${(s.f1_score * 100).toFixed(1)}%</strong></td>
      </tr>`).join("")}</tbody>
    </table></div>`;
  } catch (e) { console.error("metrics load error", e); }
}

// ── Export ─────────────────────────────────────────────────────────
function initExport() {
  $("#export-btn").addEventListener("click", () => {
    const start = $("#export-start").value, end = $("#export-end").value;
    let url = `${API}/attendance/export/excel`;
    const params = [];
    if (start) params.push(`start_date=${start}`);
    if (end)   params.push(`end_date=${end}`);
    if (params.length) url += "?" + params.join("&");
    toast("Generating Excel…");
    const a = document.createElement("a"); a.href = url; a.download = "attendance_report.xlsx"; a.click();
  });
}

// ── Dashboard reset ────────────────────────────────────────────────
function initDashboard() {
  const resetBtn = $("#dashboard-reset-btn");
  if (!resetBtn) return;
  resetBtn.addEventListener("click", async () => {
    if (!confirm("Delete all attendance records for today? This cannot be undone.")) return;
    resetBtn.disabled = true; resetBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
    try {
      await api(`/attendance/${todayStr()}`, { method: "DELETE" });
      await api("/attendance/cleanup/orphaned", { method: "DELETE" }).catch(() => {});
      toast("Today's attendance reset.", "success");
      loadDashboard();
    } catch (e) { toast("Reset failed: " + e.message, "error"); }
    finally { resetBtn.disabled = false; resetBtn.innerHTML = '<i class="fa-solid fa-rotate-left"></i> Reset Today'; }
  });
}

// ── Init ───────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  startClock(); initNav(); initEnroll(); initAttendance(); initVideo(); initMetrics(); initDashboard(); initExport();
  const today = todayStr();
  if ($("#att-date")) $("#att-date").value = today;
  if ($("#vid-date")) $("#vid-date").value = today;
  if ($("#eval-date")) $("#eval-date").value = today;
  if ($("#records-date")) $("#records-date").value = today;
  $("#today-badge").textContent = fmtDate(today);
  loadDashboard();
});