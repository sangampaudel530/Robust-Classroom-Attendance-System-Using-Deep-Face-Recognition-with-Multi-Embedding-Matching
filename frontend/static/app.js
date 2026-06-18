/**
 * app.js — Face Attendance System Frontend
 * All UI logic: navigation, enrollment, attendance processing, records, export.
 */

const API = "/api";

// ── Utility helpers ────────────────────────────────────────────────
function $(sel, ctx = document) { return ctx.querySelector(sel); }
function $$(sel, ctx = document) { return [...ctx.querySelectorAll(sel)]; }

function toast(msg, type = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `toast ${type} show`;
  setTimeout(() => el.classList.remove("show"), 3200);
}

function fmtDate(d) {
  return new Date(d).toLocaleDateString("en-IN", { day:"2-digit", month:"short", year:"numeric" });
}

async function api(path, opts = {}) {
  try {
    const r = await fetch(API + path, opts);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
    return data;
  } catch (e) {
    throw e;
  }
}

// ── Clock ──────────────────────────────────────────────────────────
function startClock() {
  const el = $("#clock");
  const tick = () => {
    const now = new Date();
    el.textContent = now.toLocaleTimeString("en-IN", { hour:"2-digit", minute:"2-digit", second:"2-digit" });
  };
  tick();
  setInterval(tick, 1000);
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
      if (tab === "students")         loadStudents();
      if (tab === "dashboard")        loadDashboard();
      if (tab === "records")          setDefaultDate("records-date");
      if (tab === "active-learning")  loadActiveLearningCandidates();
    });
  });
}

function setDefaultDate(inputId) {
  const el = $(`#${inputId}`);
  if (el && !el.value) el.value = todayStr();
}

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

// ── Dashboard ─────────────────────────────────────────────────────
async function loadDashboard() {
  const today = todayStr();
  $("#today-badge").textContent = fmtDate(today);
  $("#att-date").value = today;

  try {
    const [studentsData, attData] = await Promise.all([
      api("/students"),
      api(`/attendance/${today}`).catch(() => ({ records: [], present: 0, absent: 0, total: 0 }))
    ]);

    const total   = studentsData.total || 0;
    const present = attData.present ?? 0;
    const absent  = attData.absent  ?? 0;
    const pct     = total > 0 ? Math.round(present / total * 100) : 0;

    $("#stat-total").textContent   = total;
    $("#stat-present").textContent = present;
    $("#stat-absent").textContent  = absent;
    $("#stat-pct").textContent     = `${pct}%`;

    const records = attData.records || [];
    const resetBtn = $("#dashboard-reset-btn");
    if (records.length === 0) {
      if (resetBtn) resetBtn.style.display = "none";
      $("#dashboard-table-wrap").innerHTML = `<p class="muted">No attendance recorded today yet. <a href="#" onclick="switchTab('attendance')">Take attendance →</a></p>`;
    } else {
      if (resetBtn) resetBtn.style.display = "inline-flex";
      $("#dashboard-table-wrap").innerHTML = buildAttTable(records);
    }
  } catch (e) {
    const resetBtn = $("#dashboard-reset-btn");
    if (resetBtn) resetBtn.style.display = "none";
    toast("Failed to load dashboard: " + e.message, "error");
  }
}

function switchTab(name) {
  $(`.nav-item[data-tab="${name}"]`).click();
}

// ── Student enrollment ─────────────────────────────────────────────
function initEnroll() {
  const zone     = $("#enroll-upload-zone");
  const input    = $("#enroll-photos");
  const preview  = $("#enroll-preview");
  const btn      = $("#enroll-btn");
  const result   = $("#enroll-result");

  zone.addEventListener("click", () => input.click());
  zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("drag-over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
  zone.addEventListener("drop", e => {
    e.preventDefault();
    zone.classList.remove("drag-over");
    showPhotoPreview([...e.dataTransfer.files], preview);
    // Sync to file input via DataTransfer
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
    fd.append("roll_no", roll);
    fd.append("name", name);
    [...input.files].forEach(f => fd.append("photos", f));

    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Enrolling…';
    result.style.display = "none";

    try {
      const data = await api("/students/enroll", { method: "POST", body: fd });
      result.className = "result-box success";
      result.innerHTML = `
        <strong><i class="fa-solid fa-circle-check"></i> Enrolled successfully!</strong><br>
        Roll No: <b>${data.roll_no}</b> — ${data.name}<br>
        Photos processed: <b>${data.photos_processed}</b>
      `;
      result.style.display = "block";
      toast("Student enrolled!", "success");
      // Reset
      $("#enroll-roll").value = "";
      $("#enroll-name").value = "";
      input.value = "";
      preview.innerHTML = "";
    } catch (e) {
      result.className = "result-box error";
      result.innerHTML = `<strong><i class="fa-solid fa-triangle-exclamation"></i> Error:</strong> ${e.message}`;
      result.style.display = "block";
      toast("Enrollment failed.", "error");
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<i class="fa-solid fa-user-plus"></i> Enroll Student';
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
    if (!data.students.length) {
      wrap.innerHTML = '<p class="muted">No students enrolled yet.</p>';
      return;
    }
    wrap.innerHTML = buildStudentTable(data.students);
    attachStudentTableEvents();
  } catch (e) {
    wrap.innerHTML = `<p class="muted" style="color:var(--red)">Error: ${e.message}</p>`;
  }
}

function buildStudentTable(students) {
  return `
    <div class="table-wrap">
    <table>
      <thead><tr>
        <th>Roll No</th><th>Name</th><th>Enrolled On</th><th>Status</th><th>Actions</th>
      </tr></thead>
      <tbody>
        ${students.map(s => `
          <tr data-roll="${s.roll_no}">
            <td><strong>${s.roll_no}</strong></td>
            <td>${s.name}</td>
            <td>${s.enrolled_at ? fmtDate(s.enrolled_at) : "—"}</td>
            <td><span class="pill ${s.is_active ? 'pill-p' : 'pill-a'}">${s.is_active ? "Active" : "Inactive"}</span></td>
            <td>
              <button class="action-btn" title="View attendance" data-action="view" data-roll="${s.roll_no}">
                <i class="fa-solid fa-eye"></i>
              </button>
              <button class="action-btn danger" title="Remove student" data-action="remove" data-roll="${s.roll_no}" data-name="${s.name}">
                <i class="fa-solid fa-user-minus"></i>
              </button>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
    </div>
  `;
}

function attachStudentTableEvents() {
  $$("[data-action='remove']").forEach(btn => {
    btn.addEventListener("click", () => confirmRemove(btn.dataset.roll, btn.dataset.name));
  });
  $$("[data-action='view']").forEach(btn => {
    btn.addEventListener("click", () => viewStudentAttendance(btn.dataset.roll));
  });

  $("#student-search").addEventListener("input", e => {
    const q = e.target.value.toLowerCase();
    $$("#students-table-wrap tbody tr").forEach(row => {
      const text = row.textContent.toLowerCase();
      row.style.display = text.includes(q) ? "" : "none";
    });
  });
}

function confirmRemove(roll, name) {
  const existing = $(".modal-backdrop");
  if (existing) existing.remove();

  const modal = document.createElement("div");
  modal.className = "modal-backdrop";
  modal.innerHTML = `
    <div class="modal" style="max-width:480px">
      <h2><i class="fa-solid fa-user-minus" style="color:var(--red);margin-right:8px"></i>Remove Student</h2>
      <p style="margin-bottom:12px">
        You are about to remove <strong>${name}</strong> (${roll}).
        Choose how you want to remove them:
      </p>
      <div class="remove-options">
        <label class="remove-option" id="opt-soft">
          <input type="radio" name="remove-mode" value="soft" checked/>
          <div class="remove-option-body">
            <strong><i class="fa-solid fa-eye-slash"></i> Remove from Roster</strong>
            <span>Hides student from the active list. All photos, face data, and attendance history are <b>preserved</b>. They can be re-enrolled later.</span>
          </div>
        </label>
        <label class="remove-option danger" id="opt-hard">
          <input type="radio" name="remove-mode" value="hard"/>
          <div class="remove-option-body">
            <strong><i class="fa-solid fa-trash-can"></i> Delete Permanently</strong>
            <span>Deletes <b>everything</b>: face photos, embedding, all attendance records, and the student record. <b>This cannot be undone.</b></span>
          </div>
        </label>
      </div>
      <div class="modal-actions" style="margin-top:20px">
        <button class="btn btn-ghost" id="modal-cancel">Cancel</button>
        <button class="btn btn-danger" id="modal-confirm">Confirm Remove</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  const radios = modal.querySelectorAll("input[name='remove-mode']");
  const optSoft = modal.querySelector("#opt-soft");
  const optHard = modal.querySelector("#opt-hard");
  const confirmBtn = modal.querySelector("#modal-confirm");

  radios.forEach(r => r.addEventListener("change", () => {
    const isHard = modal.querySelector("input[name='remove-mode']:checked").value === "hard";
    optSoft.classList.toggle("selected", !isHard);
    optHard.classList.toggle("selected", isHard);
    confirmBtn.textContent = isHard ? "Delete Permanently" : "Remove from Roster";
  }));
  optSoft.classList.add("selected");

  modal.querySelector("#modal-cancel").onclick = () => modal.remove();
  confirmBtn.onclick = async () => {
    const mode = modal.querySelector("input[name='remove-mode']:checked").value;
    const keepHistory = mode === "soft";

    if (!keepHistory) {
      const sure = confirm(
        `⚠️ PERMANENT DELETE\n\nThis will delete ALL data for ${name} (${roll}):\n` +
        `• All face photos from disk\n• Face recognition embedding\n• All attendance records\n• Active Learning data\n\n` +
        `This CANNOT be undone. Proceed?`
      );
      if (!sure) return;
    }

    confirmBtn.disabled = true;
    confirmBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Removing…';

    try {
      const res = await api(`/students/${roll}?keep_history=${keepHistory}`, { method: "DELETE" });
      if (keepHistory) {
        toast(`${name} removed from roster. History preserved.`, "success");
      } else {
        toast(
          `${name} permanently deleted. ` +
          `${res.photos_deleted ?? 0} photos, ${res.attendance_records_deleted ?? 0} records removed.`,
          "success"
        );
      }
      modal.remove();
      loadStudents();
      loadDashboard();
    } catch (e) {
      toast("Remove failed: " + e.message, "error");
      confirmBtn.disabled = false;
      confirmBtn.innerHTML = "Confirm Remove";
    }
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
        Present: <span style="color:var(--green)">${data.present}</span> /
        ${data.total_days} days
        <span class="pill ${data.percentage >= 75 ? 'pill-p' : 'pill-a'}" style="margin-left:8px">${data.percentage}%</span>
      </div>
      ${buildAttTable(data.records)}
    `;
  } catch (e) {
    toast("Could not load student attendance: " + e.message, "error");
  }
}

// ── Take Attendance ────────────────────────────────────────────────
function initAttendance() {
  const zone  = $("#att-upload-zone");
  const input = $("#att-photo");
  const btn   = $("#att-process-btn");
  const clearBtn = $("#att-clear-btn");

  zone.addEventListener("click", () => input.click());
  zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("drag-over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
  zone.addEventListener("drop", e => {
    e.preventDefault();
    zone.classList.remove("drag-over");
    const f = e.dataTransfer.files[0];
    if (f) { previewClassPhoto(f); const dt = new DataTransfer(); dt.items.add(f); input.files = dt.files; }
  });

  input.addEventListener("change", () => {
    if (input.files[0]) previewClassPhoto(input.files[0]);
  });

  btn.addEventListener("click", processAttendance);
  clearBtn.addEventListener("click", clearAttendancePanel);
}

function clearAttendancePanel() {
  const input = $("#att-photo");
  const zone  = $("#att-upload-zone");
  const previewWrap = $("#att-preview-wrap");
  const result = $("#att-result");
  const clearBtn = $("#att-clear-btn");
  const processBtn = $("#att-process-btn");

  // Reset file input
  input.value = "";

  // Hide preview image
  previewWrap.style.display = "none";
  const img = $("#att-preview-img");
  if (img.src) { URL.revokeObjectURL(img.src); img.src = ""; }

  // Hide results panel
  result.style.display = "none";
  $("#att-details-wrap").innerHTML = "";
  $("#r-present").textContent  = "0";
  $("#r-absent").textContent   = "0";
  $("#r-detected").textContent = "0";
  $("#r-spoofs").textContent   = "0";

  // Hide clear button, ensure process button is enabled
  clearBtn.style.display = "none";
  processBtn.disabled = false;

  toast("Panel cleared. Upload a new class photo.", "");
}

function previewClassPhoto(file) {
  const wrap = $("#att-preview-wrap");
  const img  = $("#att-preview-img");
  img.src = URL.createObjectURL(file);
  wrap.style.display = "block";
  // When a new photo is uploaded, hide previous results and show process button
  $("#att-result").style.display = "none";
  $("#att-clear-btn").style.display = "none";
  $("#att-process-btn").disabled = false;
}

async function processAttendance() {
  const input = $("#att-photo");
  const date  = $("#att-date").value;
  const btn   = $("#att-process-btn");
  const clearBtn = $("#att-clear-btn");
  const spinner = $("#att-spinner");
  const result  = $("#att-result");

  if (!input.files[0]) { toast("Please upload a class photo first.", "error"); return; }

  const fd = new FormData();
  fd.append("photo", input.files[0]);
  if (date) fd.append("date", date);

  btn.disabled = true;
  clearBtn.style.display = "none";
  spinner.style.display = "block";
  result.style.display  = "none";

  try {
    const data = await api("/attendance/process", { method: "POST", body: fd });

    $("#r-present").textContent  = data.present;
    $("#r-absent").textContent   = data.absent;
    $("#r-detected").textContent = data.faces_detected;
    $("#r-spoofs").textContent   = data.spoofs_rejected;
    $("#att-details-wrap").innerHTML = buildAttTable(data.details);
    result.style.display = "block";
    // Show the clear button so user can retake
    clearBtn.style.display = "inline-flex";
    toast(`Attendance recorded: ${data.present} present, ${data.absent} absent.`, "success");
    loadDashboard();
  } catch (e) {
    toast("Processing failed: " + e.message, "error");
    clearBtn.style.display = "inline-flex";
  } finally {
    btn.disabled = false;
    spinner.style.display = "none";
  }
}

// ── View Records ───────────────────────────────────────────────────
async function loadRecords() {
  const date = $("#records-date").value;
  if (!date) { toast("Please select a date.", "error"); return; }

  const wrap = $("#records-wrap");
  wrap.innerHTML = '<p class="muted">Loading…</p>';

  try {
    const data = await api(`/attendance/${date}`);
    if (!data.records.length) {
      wrap.innerHTML = `<p class="muted">No records for ${fmtDate(date)}.</p>`;
      return;
    }
    wrap.innerHTML = `
      <div style="margin-bottom:14px;display:flex;gap:16px;align-items:center">
        <span>${fmtDate(date)}</span>
        <span class="pill pill-p">Present: ${data.present}</span>
        <span class="pill pill-a">Absent: ${data.absent}</span>
      </div>
      ${buildAttTable(data.records, true)}
    `;
    attachOverrideEvents(date);
  } catch (e) {
    wrap.innerHTML = `<p class="muted" style="color:var(--red)">Error: ${e.message}</p>`;
  }
}

function attachOverrideEvents(date) {
  $$("[data-override]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const roll   = btn.dataset.roll;
      const status = btn.dataset.status;
      try {
        const fd = new FormData();
        fd.append("status", status);
        await api(`/attendance/${date}/${roll}`, { method: "PUT", body: fd });
        toast(`Attendance updated for ${roll}.`, "success");
        loadRecords();
      } catch (e) {
        toast("Override failed: " + e.message, "error");
      }
    });
  });
}

// ── Shared table builder ───────────────────────────────────────────
function buildAttTable(records, withOverride = false) {
  if (!records.length) return '<p class="muted">No records.</p>';

  const rows = records.map(r => {
    const confPct = r.confidence ? Math.round(r.confidence * 100) : 0;
    const overrideBtns = withOverride ? `
      <button class="action-btn" data-override data-roll="${r.roll_no}" data-status="P" title="Mark Present">✅</button>
      <button class="action-btn" data-override data-roll="${r.roll_no}" data-status="A" title="Mark Absent">❌</button>
    ` : "";

    return `<tr>
      <td><strong>${r.roll_no}</strong></td>
      <td>${r.name || "—"}</td>
      <td><span class="pill ${r.status === 'P' ? 'pill-p' : 'pill-a'}">${r.status === 'P' ? 'Present' : 'Absent'}</span></td>
      <td>
        <div class="conf-bar-wrap">
          <div class="conf-bar"><div class="conf-bar-fill" style="width:${confPct}%"></div></div>
          <span style="font-size:12px;color:var(--text2)">${confPct}%</span>
        </div>
      </td>
      ${withOverride ? `<td>${overrideBtns}</td>` : ""}
    </tr>`;
  });

  return `
    <div class="table-wrap">
    <table>
      <thead><tr>
        <th>Roll No</th><th>Name</th><th>Status</th><th>Confidence</th>
        ${withOverride ? "<th>Override</th>" : ""}
      </tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table>
    </div>
  `;
}

// ── Export Excel ───────────────────────────────────────────────────
function initExport() {
  $("#export-btn").addEventListener("click", () => {
    const start = $("#export-start").value;
    const end   = $("#export-end").value;
    let url = `${API}/attendance/export/excel`;
    const params = [];
    if (start) params.push(`start_date=${start}`);
    if (end)   params.push(`end_date=${end}`);
    if (params.length) url += "?" + params.join("&");

    toast("Generating Excel report…");
    const a = document.createElement("a");
    a.href = url;
    a.download = "attendance_report.xlsx";
    a.click();
  });
}

// ── Active Learning ────────────────────────────────────────────────
async function loadActiveLearningCandidates() {
  const wrap = $("#al-candidates-wrap");
  wrap.innerHTML = '<p class="muted"><i class="fa-solid fa-spinner fa-spin"></i> Loading candidates and active students…</p>';

  try {
    const [candidatesData, studentsData] = await Promise.all([
      api("/students/active-learning/candidates"),
      api("/students")
    ]);

    const candidates = candidatesData.candidates || [];
    const students = studentsData.students || [];

    if (candidates.length === 0) {
      wrap.innerHTML = `
        <div class="result-box success" style="margin-top:0">
          <i class="fa-solid fa-circle-check"></i> <b>All caught up!</b> No unrecognized face candidates currently pending training.
        </div>
      `;
      return;
    }

    // Build the grid and cards
    let html = `<div class="al-candidates-grid">`;
    candidates.forEach(c => {
      // Build options dropdown of students
      const options = students.map(s => {
        const selected = s.roll_no === c.suggested_roll_no ? "selected" : "";
        return `<option value="${s.roll_no}" ${selected}>${s.roll_no} — ${s.name}</option>`;
      }).join("");

      const dateStr = fmtDate(c.class_date);
      const confPct = Math.round(c.suggested_confidence * 100);
      const suggestionLabel = c.suggested_roll_no 
        ? `Suggested: <b>${c.suggested_name}</b> (${confPct}% match)` 
        : `No automatic suggestion`;

      const cropUrl = "/" + c.face_crop_path;

      html += `
        <div class="al-candidate-card" id="al-candidate-${c.id}">
          <div class="al-candidate-row">
            <img src="${cropUrl}" class="al-candidate-crop" alt="Candidate Face crop"/>
            <div class="al-candidate-info">
              <span>Date: <b>${dateStr}</b></span>
              <span>${suggestionLabel}</span>
            </div>
          </div>
          <div class="form-group" style="margin-bottom:0">
            <label style="margin-bottom:4px">Assign Student Identity:</label>
            <select class="al-candidate-select" id="al-select-${c.id}">
              <option value="">-- Select Student --</option>
              ${options}
            </select>
          </div>
          <div class="al-candidate-actions">
            <button class="btn btn-ghost btn-sm" onclick="rejectCandidate('${c.id}')">
              <i class="fa-solid fa-trash"></i> Ignore
            </button>
            <button class="btn btn-primary btn-sm" onclick="confirmCandidate('${c.id}')" id="al-confirm-btn-${c.id}">
              <i class="fa-solid fa-circle-check"></i> Confirm & Train
            </button>
          </div>
        </div>
      `;
    });
    html += `</div>`;
    wrap.innerHTML = html;

  } catch (e) {
    wrap.innerHTML = `<p class="muted" style="color:var(--red)">Failed to load candidates: ${e.message}</p>`;
  }
}

async function confirmCandidate(candidateId) {
  const select = $(`#al-select-${candidateId}`);
  const rollNo = select.value;
  if (!rollNo) {
    toast("Please select a student first.", "error");
    return;
  }

  const btn = $(`#al-confirm-btn-${candidateId}`);
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Training…';

  try {
    const fd = new FormData();
    fd.append("candidate_id", candidateId);
    fd.append("roll_no", rollNo);

    await api("/students/active-learning/confirm", {
      method: "POST",
      body: fd
    });

    toast("Student model updated with face crop!", "success");
    const card = $(`#al-candidate-${candidateId}`);
    if (card) {
      card.style.opacity = "0";
      card.style.transform = "scale(0.9)";
      setTimeout(() => {
        loadActiveLearningCandidates();
      }, 300);
    } else {
      loadActiveLearningCandidates();
    }
  } catch (e) {
    toast("Confirmation failed: " + e.message, "error");
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}

async function rejectCandidate(candidateId) {
  if (!confirm("Are you sure you want to ignore and delete this candidate face crop?")) {
    return;
  }

  try {
    const fd = new FormData();
    fd.append("candidate_id", candidateId);

    await api("/students/active-learning/reject", {
      method: "POST",
      body: fd
    });

    toast("Candidate ignored.", "success");
    const card = $(`#al-candidate-${candidateId}`);
    if (card) {
      card.style.opacity = "0";
      card.style.transform = "scale(0.9)";
      setTimeout(() => {
        loadActiveLearningCandidates();
      }, 300);
    } else {
      loadActiveLearningCandidates();
    }
  } catch (e) {
    toast("Ignore failed: " + e.message, "error");
  }
}

function initDashboard() {
  const resetBtn = $("#dashboard-reset-btn");
  if (resetBtn) {
    resetBtn.addEventListener("click", async () => {
      const today = todayStr();
      const confirmed = confirm(
        "⚠️ RESET TODAY'S ATTENDANCE\n\n" +
        "Are you sure you want to delete all attendance records and Active Learning candidates for today?\n\n" +
        "This cannot be undone. Proceed?"
      );
      if (!confirmed) return;

      resetBtn.disabled = true;
      const originalHtml = resetBtn.innerHTML;
      resetBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Resetting…';

      try {
        await api(`/attendance/${today}`, { method: "DELETE" });
        toast("Attendance records for today have been reset.", "success");
        // Also cleanup orphaned records
        await api(`/attendance/cleanup/orphaned`, { method: "DELETE" }).catch(() => {});
        loadDashboard();
      } catch (e) {
        toast("Reset failed: " + e.message, "error");
      } finally {
        resetBtn.disabled = false;
        resetBtn.innerHTML = originalHtml;
      }
    });
  }
}

// ── Init ───────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  startClock();
  initNav();
  initEnroll();
  initAttendance();
  initDashboard();
  initExport();

  // Set today's date in relevant inputs
  const today = todayStr();
  $("#att-date").value     = today;
  $("#records-date").value = today;
  $("#today-badge").textContent = fmtDate(today);

  loadDashboard();
});