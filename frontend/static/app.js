/**
 * app.js — Face Attendance System v2.0 Frontend
 * New: video attendance tab, accuracy metrics tab, enrollment quality badges
 */

const API = "/api";
let toastTimer = null;

function $(sel, ctx = document) { return ctx.querySelector(sel); }
function $$(sel, ctx = document) { return [...ctx.querySelectorAll(sel)]; }

function toast(msg, type = "") {
  const el = $("#toast");
  if (!el) return;
  if (toastTimer) clearTimeout(toastTimer);
  el.textContent = msg;
  el.className = `toast ${type} show`;
  toastTimer = setTimeout(() => el.classList.remove("show"), 3500);
}

function fmtDate(d) {
  if (!d) return "—";
  const value = String(d);
  const parsed = new Date(value.includes("T") ? value : value + "T00:00:00");
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

async function api(path, opts = {}) {
  let r;
  try {
    r = await fetch(API + path, opts);
  } catch (_) {
    throw new Error("Cannot connect to the server.");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map(item => item.msg || String(item)).join("; ")
      : data.detail;
    throw new Error(detail || `Request failed (HTTP ${r.status}).`);
  }
  return data;
}

function todayStr() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[char]);
}

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
      const target = $(`#tab-${tab}`);
      if (!target) return;
      link.classList.add("active");
      target.classList.add("active");
      if (tab === "students")        loadStudents();
      if (tab === "dashboard")       loadDashboard();
      if (tab === "records")         { const el = $("#records-date"); if (el && !el.value) el.value = todayStr(); }
      if (tab === "active-learning") loadActiveLearningCandidates();
      if (tab === "metrics")         loadMetrics();
    });
  });
}

function switchTab(name) {
  const link = $(`.nav-item[data-tab="${name}"]`);
  if (link) link.click();
}

function validatePhotoFiles(files) {
  if (files.length > 10) return "Select a maximum of 10 photos.";
  if (files.some(file => !file.type.startsWith("image/"))) return "Only image files are allowed.";
  if (files.some(file => file.size > 10 * 1024 * 1024)) return "Each photo must be 10 MB or smaller.";
  return "";
}

// ── Dashboard ─────────────────────────────────────────────────────
async function loadDashboard() {
  const today = todayStr();
  $("#today-badge").textContent = fmtDate(today);
  if ($("#vid-date")) $("#vid-date").value = today;

  try {
    const [studentsData, attData] = await Promise.all([
      api("/students"),
      api(`/attendance/${today}`)
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
      $("#dashboard-table-wrap").innerHTML = `<p class="muted">No attendance recorded today yet. <a href="#" onclick="switchTab('video')">Take attendance →</a></p>`;
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
    const fileError = validatePhotoFiles([...input.files]);
    if (fileError) { toast(fileError, "error"); return; }

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
        Roll: <b>${escapeHtml(data.roll_no)}</b> — ${escapeHtml(data.name)}<br>
        Photos processed: <b>${data.photos_processed}</b>
        <span style="color:${qualityColor};font-weight:700;margin-left:8px">(${data.enrollment_quality} quality)</span>
        ${data.photos_rejected ? `<br><span style="color:var(--orange);font-size:12px">${data.photos_rejected} photo(s) rejected.</span>` : ""}
        ${data.warning ? `<br><span style="color:var(--orange);font-size:12px">⚠️ ${escapeHtml(data.warning)}</span>` : ""}
      `;
      result.style.display = "block";
      toast("Student enrolled!", "success");
      $("#enroll-roll").value = ""; $("#enroll-name").value = "";
      input.value = ""; showPhotoPreview([], preview);
    } catch (e) {
      result.className = "result-box error";
      result.innerHTML = `<strong><i class="fa-solid fa-triangle-exclamation"></i> Error:</strong> ${escapeHtml(e.message)}`;
      result.style.display = "block";
      toast("Enrollment failed.", "error");
    } finally {
      btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-user-plus"></i> Enroll Student';
    }
  });
}

function showPhotoPreview(files, container) {
  $$('img', container).forEach(img => {
    if (img.src.startsWith("blob:")) URL.revokeObjectURL(img.src);
  });
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
    wrap.innerHTML = `<p class="muted" style="color:var(--red)">Error: ${escapeHtml(e.message)}</p>`;
  }
}

function buildStudentTable(students) {
  const qualityBadge = (q) => {
    const colors = { excellent: "#15803D", good: "#16A34A", fair: "#D97706", poor: "#DC2626", none: "#9CA3AF" };
    return `<span style="color:${colors[q]||"#9CA3AF"};font-weight:700;font-size:12px">${escapeHtml(q || "?")}</span>`;
  };
  return `<div class="table-wrap"><table>
    <thead><tr><th>Roll No</th><th>Name</th><th>Photos</th><th>Enrolled</th><th>Status</th><th>Actions</th></tr></thead>
    <tbody>
      ${students.map(s => `<tr data-roll="${escapeHtml(s.roll_no)}">
        <td><strong>${escapeHtml(s.roll_no)}</strong></td>
        <td>${escapeHtml(s.name)}</td>
        <td>${s.enrollment_photos ?? "?"} ${qualityBadge(s.enrollment_quality)}</td>
        <td>${s.enrolled_at ? fmtDate(s.enrolled_at) : "—"}</td>
        <td><span class="pill ${s.is_active ? "pill-p" : "pill-a"}">${s.is_active ? "Active" : "Inactive"}</span></td>
        <td>
          <button class="action-btn" title="View attendance" data-action="view" data-roll="${escapeHtml(s.roll_no)}"><i class="fa-solid fa-eye"></i></button>
          <button class="action-btn photos" title="View or add enrolled photos" data-action="photos" data-roll="${escapeHtml(s.roll_no)}" data-name="${escapeHtml(s.name)}"><i class="fa-solid fa-images"></i> Photos</button>
          <button class="action-btn edit" title="Edit student" data-action="edit" data-roll="${escapeHtml(s.roll_no)}" data-name="${escapeHtml(s.name)}"><i class="fa-solid fa-pen"></i> Edit</button>
          <button class="action-btn danger" title="Remove" data-action="remove" data-roll="${escapeHtml(s.roll_no)}" data-name="${escapeHtml(s.name)}"><i class="fa-solid fa-user-minus"></i></button>
        </td>
      </tr>`).join("")}
    </tbody>
  </table></div>`;
}

function attachStudentTableEvents() {
  $$("[data-action='remove']").forEach(btn => btn.addEventListener("click", () => confirmRemove(btn.dataset.roll, btn.dataset.name)));
  $$("[data-action='view']").forEach(btn => btn.addEventListener("click", () => viewStudentAttendance(btn.dataset.roll)));
  $$("[data-action='photos']").forEach(btn => btn.addEventListener("click", () => manageStudentPhotos(btn.dataset.roll, btn.dataset.name)));
  $$("[data-action='edit']").forEach(btn => btn.addEventListener("click", () => editStudent(btn.dataset.roll, btn.dataset.name)));
  $("#student-search").oninput = e => {
    const q = e.target.value.toLowerCase();
    $$("#students-table-wrap tbody tr").forEach(row => { row.style.display = row.textContent.toLowerCase().includes(q) ? "" : "none"; });
  };
}

async function manageStudentPhotos(roll, name) {
  const existing = $(".modal-backdrop"); if (existing) existing.remove();
  const modal = document.createElement("div");
  modal.className = "modal-backdrop";
  modal.innerHTML = `<div class="modal photo-manager-modal">
    <div class="photo-manager-header">
      <div>
        <h2><i class="fa-solid fa-images" style="color:var(--blue);margin-right:8px"></i>Enrolled Photos</h2>
        <span class="muted">${escapeHtml(name)} (${escapeHtml(roll)})</span>
      </div>
      <button class="action-btn" id="photo-modal-close" title="Close"><i class="fa-solid fa-xmark"></i></button>
    </div>
    <div id="student-photo-gallery" class="student-photo-gallery"><p class="muted">Loading photos…</p></div>
    <div class="photo-upload-panel">
      <input type="file" id="student-photo-input" accept="image/*" multiple hidden />
      <button class="btn btn-ghost" id="choose-student-photos"><i class="fa-solid fa-folder-open"></i> Choose Photos</button>
      <span class="muted" id="student-photo-selection">No photos selected</span>
      <button class="btn btn-primary" id="upload-student-photos" disabled><i class="fa-solid fa-plus"></i> Add Photos</button>
    </div>
    <p class="hint photo-upload-hint">Use clear photos containing one face. Up to 10 photos, 10 MB each.</p>
  </div>`;
  document.body.appendChild(modal);

  const gallery = modal.querySelector("#student-photo-gallery");
  const input = modal.querySelector("#student-photo-input");
  const uploadBtn = modal.querySelector("#upload-student-photos");
  const selection = modal.querySelector("#student-photo-selection");
  const close = () => modal.remove();
  let currentPhotos = [];

  const renderPhotos = photos => {
    currentPhotos = photos;
    gallery.innerHTML = photos.length
      ? photos.map((photo, index) => `<figure class="student-photo-card">
          <img src="${escapeHtml(photo.url)}" alt="Enrollment photo ${index + 1} of ${escapeHtml(name)}" loading="lazy" />
          <button class="photo-delete-btn" data-filename="${escapeHtml(photo.filename)}" title="Delete this photo" aria-label="Delete photo ${index + 1}"><i class="fa-solid fa-trash-can"></i></button>
          <figcaption>Photo ${index + 1}</figcaption>
        </figure>`).join("")
      : '<div class="student-photo-empty"><i class="fa-regular fa-images"></i><p>No enrolled photos found.</p></div>';
  };

  try {
    const data = await api(`/students/${encodeURIComponent(roll)}/photos`);
    renderPhotos(data.photos || []);
  } catch (e) {
    gallery.innerHTML = `<p class="muted" style="color:var(--red)">Could not load photos: ${escapeHtml(e.message)}</p>`;
  }

  modal.querySelector("#photo-modal-close").onclick = close;
  modal.addEventListener("click", event => { if (event.target === modal) close(); });
  gallery.onclick = async event => {
    const deleteBtn = event.target.closest(".photo-delete-btn");
    if (!deleteBtn) return;
    const filename = deleteBtn.dataset.filename;
    const isLastPhoto = currentPhotos.length === 1;
    const message = isLastPhoto
      ? "This is the student's last enrolled photo. Deleting it will disable face recognition until another photo is added. Delete it?"
      : "Delete this enrolled photo? The student's recognition data will be rebuilt from the remaining photos.";
    if (!confirm(message)) return;

    deleteBtn.disabled = true;
    deleteBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
    try {
      const data = await api(`/students/${encodeURIComponent(roll)}/photos/${encodeURIComponent(filename)}`, { method: "DELETE" });
      renderPhotos(data.photos || []);
      toast(data.recognition_available ? "Photo deleted and recognition updated." : "Photo deleted. Add a photo to restore recognition.", data.recognition_available ? "success" : "");
      loadStudents();
    } catch (e) {
      toast("Photo deletion failed: " + e.message, "error");
      deleteBtn.disabled = false;
      deleteBtn.innerHTML = '<i class="fa-solid fa-trash-can"></i>';
    }
  };
  modal.querySelector("#choose-student-photos").onclick = () => input.click();
  input.onchange = () => {
    const count = input.files.length;
    selection.textContent = count ? `${count} photo${count === 1 ? "" : "s"} selected` : "No photos selected";
    const fileError = validatePhotoFiles([...input.files]);
    uploadBtn.disabled = count === 0 || Boolean(fileError);
    if (fileError) toast(fileError, "error");
  };
  uploadBtn.onclick = async () => {
    const fd = new FormData();
    [...input.files].forEach(file => fd.append("photos", file));
    uploadBtn.disabled = true;
    uploadBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Processing…';
    try {
      const data = await api(`/students/${encodeURIComponent(roll)}/photos`, { method: "POST", body: fd });
      renderPhotos(data.photos || []);
      const rejected = data.photos_rejected ? ` ${data.photos_rejected} rejected.` : "";
      toast(`${data.photos_added} photo${data.photos_added === 1 ? "" : "s"} added.${rejected}`, "success");
      input.value = "";
      selection.textContent = "No photos selected";
      loadStudents();
    } catch (e) {
      toast("Photo upload failed: " + e.message, "error");
    } finally {
      uploadBtn.disabled = true;
      uploadBtn.innerHTML = '<i class="fa-solid fa-plus"></i> Add Photos';
    }
  };
}

function editStudent(roll, name) {
  const existing = $(".modal-backdrop"); if (existing) existing.remove();
  const modal = document.createElement("div");
  modal.className = "modal-backdrop";
  modal.innerHTML = `<div class="modal" style="max-width:480px">
    <h2><i class="fa-solid fa-user-pen" style="color:var(--blue);margin-right:8px"></i>Edit Student</h2>
    <p>Update the student's roster information. Attendance, photos, and face embeddings will be preserved.</p>
    <div class="form-group">
      <label for="edit-roll">Roll Number <span class="req">*</span></label>
      <input type="text" id="edit-roll" maxlength="32" value="${escapeHtml(roll)}" />
    </div>
    <div class="form-group">
      <label for="edit-name">Full Name <span class="req">*</span></label>
      <input type="text" id="edit-name" maxlength="128" value="${escapeHtml(name)}" />
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" id="modal-cancel">Cancel</button>
      <button class="btn btn-primary" id="modal-save"><i class="fa-solid fa-floppy-disk"></i> Save Changes</button>
    </div>
  </div>`;
  document.body.appendChild(modal);

  const saveBtn = modal.querySelector("#modal-save");
  const close = () => modal.remove();
  modal.querySelector("#modal-cancel").onclick = close;
  modal.addEventListener("click", event => { if (event.target === modal) close(); });
  modal.querySelector("#edit-name").focus();

  saveBtn.onclick = async () => {
    const newRoll = modal.querySelector("#edit-roll").value.trim();
    const newName = modal.querySelector("#edit-name").value.trim();
    if (!newRoll || !newName) { toast("Roll number and name are required.", "error"); return; }

    const fd = new FormData();
    fd.append("new_roll_no", newRoll); fd.append("name", newName);
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Saving…';
    try {
      await api(`/students/${encodeURIComponent(roll)}`, { method: "PUT", body: fd });
      toast(`${newName} updated successfully.`, "success");
      close(); loadStudents(); loadDashboard();
    } catch (e) {
      toast("Update failed: " + e.message, "error");
      saveBtn.disabled = false;
      saveBtn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> Save Changes';
    }
  };
}

function confirmRemove(roll, name) {
  const existing = $(".modal-backdrop"); if (existing) existing.remove();
  const modal = document.createElement("div");
  modal.className = "modal-backdrop";
  modal.innerHTML = `<div class="modal" style="max-width:480px">
    <h2><i class="fa-solid fa-user-minus" style="color:var(--red);margin-right:8px"></i>Remove Student</h2>
    <p style="margin-bottom:12px">Remove <strong>${escapeHtml(name)}</strong> (${escapeHtml(roll)}):</p>
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
      await api(`/students/${encodeURIComponent(roll)}?keep_history=${keepHistory}`, { method: "DELETE" });
      toast(keepHistory ? `${name} removed from roster.` : `${name} permanently deleted.`, "success");
      modal.remove(); loadStudents(); loadDashboard();
    } catch (e) { toast("Remove failed: " + e.message, "error"); confirmBtn.disabled = false; confirmBtn.textContent = "Confirm Remove"; }
  };
}

async function viewStudentAttendance(roll) {
  try {
    const data = await api(`/attendance/student/${encodeURIComponent(roll)}`);
    
    const existing = $(".modal-backdrop"); if (existing) existing.remove();
    const modal = document.createElement("div");
    modal.className = "modal-backdrop";
    modal.innerHTML = `<div class="modal" style="max-width:600px; max-height: 80vh; display: flex; flex-direction: column;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <h2><i class="fa-solid fa-user-graduate" style="color:var(--primary);margin-right:8px"></i>Student Attendance History</h2>
        <button class="action-btn" id="modal-close" style="font-size: 20px;"><i class="fa-solid fa-xmark"></i></button>
      </div>
      
      <div style="margin-bottom:20px; padding: 12px; background: var(--bg); border-radius: 8px; border: 1px solid var(--border);">
        <div style="font-size: 18px; margin-bottom: 8px;"><strong>${escapeHtml(data.name)}</strong> <span class="muted">(${escapeHtml(data.roll_no)})</span></div>
        <div style="display: flex; gap: 12px; align-items: center;">
          <span class="pill pill-p">Present: ${data.present}</span>
          <span class="pill pill-a">Total Days: ${data.total_days}</span>
          <span class="pill ${data.percentage >= 75 ? "pill-p" : "pill-a"}" style="margin-left:auto; font-size: 14px;">Overall: ${data.percentage}%</span>
        </div>
      </div>
      
      <div style="overflow-y: auto; border: 1px solid var(--border); border-radius: 8px;">
        ${buildAttTable(data.records, false, true)}
      </div>
    </div>`;
    
    document.body.appendChild(modal);
    
    modal.querySelector("#modal-close").onclick = () => modal.remove();
    // Close when clicking outside the modal
    modal.addEventListener("click", (e) => {
      if (e.target === modal) modal.remove();
    });

  } catch (e) { toast("Could not load: " + e.message, "error"); }
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
    const preview = $("#vid-preview-img");
    if (preview) { preview.style.display = "none"; preview.src = ""; }
  });
}

function showVideoFilename(file) {
  const el = $("#vid-filename");
  el.textContent = `Video: ${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
  el.style.display = "block";
  $("#vid-result").style.display = "none";
}

async function processVideo() {
  const input = $("#vid-file"), date = $("#vid-date").value;
  const btn = $("#vid-process-btn"), spinner = $("#vid-spinner"), result = $("#vid-result");
  const previewImg = $("#vid-preview-img");
  if (!input.files[0]) { toast("Please upload a video first.", "error"); return; }
  if (input.files[0].size > 250 * 1024 * 1024) { toast("Video must be 250 MB or smaller.", "error"); return; }

  const fd = new FormData();
  fd.append("video", input.files[0]); if (date) fd.append("date", date);

  btn.disabled = true;
  result.style.display = "none";

  // Show preview area immediately so frames appear as they arrive
  if (previewImg) {
    previewImg.style.display = "block";
    previewImg.src = "";
    previewImg.style.opacity = "0.5";
  }

  // Show a minimal processing status (not a blocking spinner)
  spinner.style.display = "block";
  spinner.querySelector("p").textContent = "Starting video processing…";

  toast("Processing video…", "");

  // Pending frame queue for smooth rendering via requestAnimationFrame
  let pendingFrame = null;
  let rafScheduled = false;
  let frameCount = 0;
  let gotResult = false;

  function renderFrame() {
    if (pendingFrame) {
      if (previewImg) {
        previewImg.src = "data:image/jpeg;base64," + pendingFrame;
        previewImg.style.opacity = "1";
      }
      pendingFrame = null;
    }
    rafScheduled = false;
  }

  function scheduleFrame(b64) {
    pendingFrame = b64;  // always keep only the latest frame
    if (!rafScheduled) {
      rafScheduled = true;
      requestAnimationFrame(renderFrame);
    }
  }

  try {
    const res = await fetch(API + "/attendance/process-video", { method: "POST", body: fd });
    if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${res.status}`);
    }
    
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        
        const lines = buffer.split("\n");
        buffer = lines.pop(); // keep incomplete line
        
        for (const line of lines) {
            if (!line.trim()) continue;
            let msg;
            try { msg = JSON.parse(line); } catch (e) { continue; }
            
            if (msg.type === "frame") {
                frameCount++;
                // Queue the latest frame — requestAnimationFrame will render it
                scheduleFrame(msg.image);
                // Update progress text
                const pct = msg.progress ?? 0;
                spinner.querySelector("p").textContent = `Processing… ${pct}% (frame ${frameCount})`;

            } else if (msg.type === "error") {
                throw new Error(msg.message);
            } else if (msg.type === "result") {
                gotResult = true;
                const data = msg.data;
                $("#vr-present").textContent = data.present;
                $("#vr-absent").textContent  = data.absent;
                $("#vr-frames").textContent  = data.frames_processed || "—";
                $("#vr-faces").textContent   = data.faces_detected;
                
                $("#vid-details-wrap").innerHTML = buildAttTable(data.details, false, false, true);
                result.style.display = "block";
                $("#vid-clear-btn").style.display = "inline-flex";
                toast(`Done: ${data.present} present, ${data.absent} absent.`, "success");
                loadDashboard();
            }
        }
    }
    if (!gotResult) throw new Error("The server ended processing without returning a result.");
  } catch (e) {
    toast("Video processing failed: " + e.message, "error");
    if (previewImg) { previewImg.style.display = "none"; previewImg.src = ""; }
  } finally { 
      btn.disabled = false; 
      spinner.style.display = "none";
      // Keep preview visible so teacher can see the last annotated frame
      if (previewImg && previewImg.src && result.style.display === "block") {
        previewImg.style.opacity = "1";
      }
  }
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
  } catch (e) { wrap.innerHTML = `<p class="muted" style="color:var(--red)">Error: ${escapeHtml(e.message)}</p>`; }
}

function attachOverrideEvents(date) {
  $$("[data-override]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const fd = new FormData(); fd.append("status", btn.dataset.status);
      try {
        await api(`/attendance/${date}/${encodeURIComponent(btn.dataset.roll)}`, { method: "PUT", body: fd });
        toast(`Updated ${btn.dataset.roll}.`, "success"); loadRecords();
      } catch (e) { toast("Override failed: " + e.message, "error"); }
    });
  });
}

function buildAttTable(records, withOverride = false, showDate = false, showConfidence = false) {
  if (!records.length) return '<p class="muted">No records.</p>';
  return `<div class="table-wrap"><table>
    <thead><tr>${showDate ? "<th>Date</th>" : ""}<th>Roll No</th><th>Name</th><th>Status</th>${showConfidence ? "<th>Confidence</th>" : ""}${withOverride ? "<th>Override</th>" : ""}</tr></thead>
    <tbody>${records.map(r => {
      const confPct = Math.max(0, Math.min(100, r.confidence ? Math.round(r.confidence * 100) : 0));
      return `<tr>
        ${showDate ? `<td>${fmtDate(r.date)}</td>` : ""}
        <td><strong>${escapeHtml(r.roll_no)}</strong></td>
        <td>${escapeHtml(r.name || "—")}</td>
        <td><span class="pill ${r.status === "P" ? "pill-p" : "pill-a"}">${r.status === "P" ? "Present" : "Absent"}</span></td>
        ${showConfidence ? `<td><div class="conf-bar-wrap"><div class="conf-bar"><div class="conf-bar-fill" style="width:${confPct}%"></div></div><span style="font-size:12px;color:var(--text2)">${confPct}%</span></div></td>` : ""}
        ${withOverride ? `<td>
          <button class="action-btn" data-override data-roll="${escapeHtml(r.roll_no)}" data-status="P" title="Mark Present">✅</button>
          <button class="action-btn" data-override data-roll="${escapeHtml(r.roll_no)}" data-status="A" title="Mark Absent">❌</button>
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
    let html = `<div class="al-bulk-toolbar">
      <label class="al-select-all-label"><input type="checkbox" id="al-select-all"/> Select all <span id="al-selected-count">(0 selected)</span></label>
      <button class="btn btn-primary btn-sm" id="al-confirm-selected"><i class="fa-solid fa-circle-check"></i> Confirm & Train Selected</button>
      <button class="btn btn-danger btn-sm" id="al-reject-selected"><i class="fa-solid fa-trash"></i> Delete Selected</button>
    </div><div class="al-candidates-grid">`;
    candidates.forEach(c => {
      const candidateId = escapeHtml(c.id);
      const options = students.map(s => `<option value="${escapeHtml(s.roll_no)}" ${s.roll_no === c.suggested_roll_no ? "selected" : ""}>${escapeHtml(s.roll_no)} — ${escapeHtml(s.name)}</option>`).join("");
      const confidence = Number.isFinite(Number(c.suggested_confidence)) ? Math.round(Number(c.suggested_confidence) * 100) : 0;
      html += `<div class="al-candidate-card" id="al-candidate-${candidateId}">
        <label class="al-card-check"><input type="checkbox" class="al-candidate-checkbox" value="${candidateId}"/> Select</label>
        <div class="al-candidate-row">
          <img src="${escapeHtml(c.face_crop_url)}" class="al-candidate-crop" alt="Face crop"/>
          <div class="al-candidate-info">
            <span>Date: <b>${fmtDate(c.class_date)}</b></span>
            <span>${c.suggested_roll_no ? `Suggested: <b>${escapeHtml(c.suggested_name || c.suggested_roll_no)}</b> (${confidence}% match)` : "No suggestion"}</span>
          </div>
        </div>
        <div class="form-group" style="margin-bottom:0">
          <label style="margin-bottom:4px">Assign Student:</label>
          <select class="al-candidate-select" id="al-select-${candidateId}">
            <option value="">-- Select Student --</option>${options}
          </select>
        </div>
        <div class="al-candidate-actions">
          <button class="btn btn-ghost btn-sm" data-candidate-action="reject" data-candidate-id="${candidateId}"><i class="fa-solid fa-trash"></i> Ignore</button>
          <button class="btn btn-primary btn-sm" data-candidate-action="confirm" data-candidate-id="${candidateId}" id="al-confirm-btn-${candidateId}"><i class="fa-solid fa-circle-check"></i> Confirm & Train</button>
        </div>
      </div>`;
    });
    html += "</div>";
    wrap.innerHTML = html;
    updateActiveLearningSelectionCount();
    wrap.onchange = event => {
      if (event.target.matches(".al-candidate-checkbox")) updateActiveLearningSelectionCount();
      if (event.target.id === "al-select-all") {
        $$(".al-candidate-checkbox").forEach(checkbox => { checkbox.checked = event.target.checked; });
        updateActiveLearningSelectionCount();
      }
    };
    wrap.onclick = event => {
      const button = event.target.closest("[data-candidate-action]");
      if (button) {
        if (button.dataset.candidateAction === "confirm") confirmCandidate(button.dataset.candidateId);
        else rejectCandidate(button.dataset.candidateId);
        return;
      }
      if (event.target.closest("#al-confirm-selected")) confirmSelectedCandidates();
      else if (event.target.closest("#al-reject-selected")) rejectSelectedCandidates();
    };
  } catch (e) { wrap.innerHTML = `<p class="muted" style="color:var(--red)">Error: ${escapeHtml(e.message)}</p>`; }
}

function selectedActiveLearningIds() {
  return $$(".al-candidate-checkbox:checked").map(checkbox => checkbox.value);
}

function updateActiveLearningSelectionCount() {
  const selected = selectedActiveLearningIds().length;
  const total = $$(".al-candidate-checkbox").length;
  const label = $("#al-selected-count");
  const selectAll = $("#al-select-all");
  if (label) label.textContent = `(${selected} selected)`;
  if (selectAll) {
    selectAll.checked = total > 0 && selected === total;
    selectAll.indeterminate = selected > 0 && selected < total;
  }
}

async function submitCandidateConfirmation(id) {
  const roll = $(`#al-select-${id}`).value;
  if (!roll) throw new Error("Select a student first.");
  const fd = new FormData();
  fd.append("candidate_id", id);
  fd.append("roll_no", roll);
  return api("/students/active-learning/confirm", { method: "POST", body: fd });
}

async function confirmCandidate(id) {
  const roll = $(`#al-select-${id}`).value;
  if (!roll) { toast("Select a student first.", "error"); return; }
  const btn = $(`#al-confirm-btn-${id}`); btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
  try {
    await submitCandidateConfirmation(id);
    toast("Model updated!", "success");
    setTimeout(loadActiveLearningCandidates, 300);
  } catch (e) { toast(e.message, "error"); btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-circle-check"></i> Confirm & Train'; }
}

async function confirmSelectedCandidates() {
  const ids = selectedActiveLearningIds();
  if (!ids.length) { toast("Select at least one candidate.", "error"); return; }
  const missingStudent = ids.find(id => !$(`#al-select-${id}`).value);
  if (missingStudent) { toast("Every selected candidate must have a student assigned.", "error"); return; }
  if (!confirm(`Confirm and train ${ids.length} selected candidate(s)?`)) return;

  const button = $("#al-confirm-selected");
  button.disabled = true;
  button.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Training…';
  let completed = 0;
  const errors = [];
  for (const id of ids) {
    try { await submitCandidateConfirmation(id); completed++; }
    catch (error) { errors.push(error.message); }
  }
  if (completed) toast(`Trained ${completed} candidate(s).`, "success");
  if (errors.length) toast(`${errors.length} candidate(s) failed: ${errors[0]}`, "error");
  loadDashboard();
  await loadActiveLearningCandidates();
}

async function rejectCandidate(id) {
  if (!confirm("Ignore and delete this face crop?")) return;
  try {
    const fd = new FormData(); fd.append("candidate_id", id);
    await api("/students/active-learning/reject", { method: "POST", body: fd });
    toast("Candidate ignored.", "success"); setTimeout(loadActiveLearningCandidates, 300);
  } catch (e) { toast(e.message, "error"); }
}

async function rejectSelectedCandidates() {
  const ids = selectedActiveLearningIds();
  if (!ids.length) { toast("Select at least one candidate.", "error"); return; }
  if (!confirm(`Permanently delete ${ids.length} selected face candidate(s)?`)) return;

  const button = $("#al-reject-selected");
  button.disabled = true;
  button.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Deleting…';
  let completed = 0;
  const errors = [];
  for (const id of ids) {
    try {
      const fd = new FormData(); fd.append("candidate_id", id);
      await api("/students/active-learning/reject", { method: "POST", body: fd });
      completed++;
    } catch (error) { errors.push(error.message); }
  }
  if (completed) toast(`Deleted ${completed} candidate(s).`, "success");
  if (errors.length) toast(`${errors.length} candidate(s) failed: ${errors[0]}`, "error");
  await loadActiveLearningCandidates();
}

// ── Metrics (NEW) ─────────────────────────────────────────────────
function initMetrics() {
  const zone = $("#eval-upload-zone"), input = $("#eval-video");
  if (!zone || !input) return;
  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    if (input.files[0]) {
      const el = $("#eval-filename");
      el.textContent = `Video selected: ${input.files[0].name}`;
      el.style.display = "block";
    }
  });
  $("#eval-btn").addEventListener("click", runEvaluation);
  if ($("#eval-date")) $("#eval-date").value = todayStr();

  // Clear Evaluation History button
  const clearBtn = $("#clear-metrics-btn");
  if (clearBtn) {
    clearBtn.addEventListener("click", async () => {
      if (!confirm("Delete all evaluation history? This cannot be undone.")) return;
      clearBtn.disabled = true;
      clearBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
      try {
        const data = await api("/attendance/metrics/history", { method: "DELETE" });
        toast(`Cleared ${data.deleted} evaluation session(s).`, "success");
        loadMetrics();
      } catch (e) {
        toast("Failed to clear history: " + e.message, "error");
      } finally {
        clearBtn.disabled = false;
        clearBtn.innerHTML = '<i class="fa-solid fa-trash"></i> Clear History';
      }
    });
  }
}

async function runEvaluation() {
  const input = $("#eval-video"), gt = $("#eval-ground-truth").value.trim();
  const date  = $("#eval-date").value;
  if (!input.files[0]) { toast("Upload a class video first.", "error"); return; }
  if (!gt) { toast("Enter the ground truth roll numbers.", "error"); return; }

  const btn = $("#eval-btn"), spinner = $("#eval-spinner"), result = $("#eval-result");
  btn.disabled = true; spinner.style.display = "block"; result.style.display = "none";

  const fd = new FormData();
  fd.append("video", input.files[0]);
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
    if (start && end && start > end) { toast("From Date must be before To Date.", "error"); return; }
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
  startClock(); initNav(); initEnroll(); initVideo(); initMetrics(); initDashboard(); initExport();
  const today = todayStr();
  if ($("#vid-date")) $("#vid-date").value = today;
  if ($("#eval-date")) $("#eval-date").value = today;
  if ($("#records-date")) $("#records-date").value = today;
  $("#today-badge").textContent = fmtDate(today);
  loadDashboard();
});
