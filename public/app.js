const state = {
  bootstrap: null,
  user: null,
  view: "dashboard",
  cache: {},
};

const navItems = {
  ADMIN: [
    ["dashboard", "Dashboard"],
    ["profile", "Profile"],
    ["users", "Users"],
    ["academic", "Academics"],
    ["reports", "Reports"],
    ["notices", "Notices"],
  ],
  TEACHER: [
    ["dashboard", "Dashboard"],
    ["profile", "Profile"],
    ["attendance", "Attendance"],
    ["marks", "Marks"],
    ["analytics", "Analytics"],
  ],
  STUDENT: [
    ["dashboard", "Dashboard"],
    ["profile", "Profile"],
    ["attendance", "Attendance"],
    ["marks", "Marks"],
  ],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
let tableCounter = 0;

function applyTheme(theme) {
  const safeTheme = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = safeTheme;
  localStorage.setItem("theme", safeTheme);
  const toggle = $("#themeToggle");
  if (toggle) {
    toggle.textContent = safeTheme === "dark" ? "Light mode" : "Dark mode";
    toggle.setAttribute("aria-pressed", String(safeTheme === "dark"));
  }
}

function initTheme() {
  const saved = localStorage.getItem("theme") || "light";
  applyTheme(saved);
  $("#themeToggle")?.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function asPercent(value) {
  return `${Number(value || 0).toFixed(1)}%`;
}

function chip(value) {
  const text = String(value ?? "N/A");
  const key = text.toUpperCase();
  let color = "";
  if (["APPROVED", "ACTIVE", "PRESENT", "O", "A+", "A"].includes(key)) color = "green";
  if (["PENDING", "ON LEAVE", "B+", "B", "C"].includes(key)) color = "amber";
  if (["REJECTED", "INACTIVE", "DETAINED", "NOT_ELIGIBLE", "ABSENT", "F"].includes(key)) color = "red";
  if (["ADMIN", "TEACHER", "STUDENT"].includes(key)) color = "blue";
  return `<span class="chip ${color}">${escapeHtml(text.replaceAll("_", " "))}</span>`;
}

function progress(value, risk = false) {
  const width = Math.max(0, Math.min(100, Number(value || 0)));
  return `<div class="progress ${risk ? "risk" : ""}" aria-label="${width}%"><span style="width:${width}%"></span></div>`;
}

function toast(message, type = "ok") {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show ${type === "error" ? "error" : ""}`;
  setTimeout(() => {
    node.className = "toast";
  }, 3600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    throw new Error(payload.error || payload || "Request failed");
  }
  return payload;
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function optionList(items, selected = "", label = (item) => item.name) {
  return items
    .map((item) => `<option value="${item.id}" ${String(item.id) === String(selected) ? "selected" : ""}>${escapeHtml(label(item))}</option>`)
    .join("");
}

function optionListWithPlaceholder(items, placeholder, label = (item) => item.name) {
  return `<option value="">${escapeHtml(placeholder)}</option>${optionList(items, "", label)}`;
}

function setTitle(title, eyebrow = "") {
  $("#viewTitle").textContent = title;
  $("#viewEyebrow").textContent = eyebrow || state.user.role.toLowerCase();
}

function renderEmpty(message) {
  return `<div class="empty-state">${escapeHtml(message)}</div>`;
}

function renderTable(headers, rows, empty = "No records found.") {
  if (!rows.length) return renderEmpty(empty);
  tableCounter += 1;
  const tableId = `table-${tableCounter}`;
  return `
    <div class="table-search-row">
      <input class="table-search" data-table-search="${tableId}" placeholder="Search this table by name, email, roll, subject...">
    </div>
    <div class="table-wrap">
      <table data-table-id="${tableId}">
        <thead><tr>${headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("")}</tr></thead>
        <tbody>${rows.join("")}</tbody>
      </table>
    </div>
  `;
}

document.addEventListener("input", (event) => {
  const input = event.target.closest("[data-table-search]");
  if (!input) return;
  const table = document.querySelector(`[data-table-id="${input.dataset.tableSearch}"]`);
  if (!table) return;
  const needle = input.value.trim().toLowerCase();
  $$("tbody tr", table).forEach((row) => {
    row.classList.toggle("hidden", needle && !row.textContent.toLowerCase().includes(needle));
  });
});

function paginationControls(kind, page, hasNext) {
  return `
    <div class="pagination-row">
      <button class="secondary-action" type="button" data-page-kind="${kind}" data-page-dir="-1" ${page <= 0 ? "disabled" : ""}>Prev</button>
      <span class="muted">Page ${page + 1}</span>
      <button class="secondary-action" type="button" data-page-kind="${kind}" data-page-dir="1" ${!hasNext ? "disabled" : ""}>Next</button>
    </div>
  `;
}

function bindPaginationControls(handlers) {
  $$("[data-page-kind]").forEach((button) => {
    button.addEventListener("click", async () => {
      const handler = handlers[button.dataset.pageKind];
      if (!handler) return;
      await handler(Number(button.dataset.pageDir));
    });
  });
}

async function init() {
  initTheme();
  state.bootstrap = await api("/api/bootstrap");
  fillRegisterOptions();
  bindAuth();
  try {
    const payload = await api("/api/me");
    state.user = payload.user;
    showApp();
  } catch {
    showAuth();
  }
}

function fillRegisterOptions() {
  $$("[data-options='departments']").forEach((select) => {
    select.innerHTML = optionListWithPlaceholder(state.bootstrap.departments, "Unassigned until admin setup", (item) => `${item.code} - ${item.name}`);
  });
  $$("[data-options='courses']").forEach((select) => {
    select.innerHTML = optionListWithPlaceholder(state.bootstrap.courses, "Unassigned until admin setup", (item) => `${item.code} - ${item.name}`);
  });
  $$("[data-options='sections']").forEach((select) => {
    select.innerHTML = optionListWithPlaceholder(state.bootstrap.sections, "Unassigned until admin setup", (item) => `${item.name} Sem ${item.semester}`);
  });
}

function syncRegistrationRole() {
  const form = $("#registerForm");
  const isStudent = form.role.value === "STUDENT";
  $("#studentFields").classList.toggle("hidden", !isStudent);
  $("#teacherFields").classList.toggle("hidden", isStudent);
  form.rollNumber.required = isStudent;
  form.employeeId.required = !isStudent;
}

function bindAuth() {
  $$("[data-auth-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      $$("[data-auth-tab]").forEach((item) => item.classList.toggle("active", item === button));
      $("#loginForm").classList.toggle("hidden", button.dataset.authTab !== "login");
      $("#registerForm").classList.toggle("hidden", button.dataset.authTab !== "register");
    });
  });

  $("#registerRole").addEventListener("change", syncRegistrationRole);
  syncRegistrationRole();

  $("#loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = formData(event.currentTarget);
    await login(data.email, data.password);
  });

  $("#registerForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = event.currentTarget.querySelector("button[type='submit']");
    submitButton.disabled = true;
    submitButton.textContent = "Submitting...";
    try {
      const data = formData(event.currentTarget);
      await api("/api/register", { method: "POST", body: JSON.stringify(data) });
      event.currentTarget.reset();
      syncRegistrationRole();
      const message = "Registration submitted to Admin. Please wait for approval before logging in.";
      toast(message);
      window.alert(message);
      $("[data-auth-tab='login']").click();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = "Submit for approval";
    }
  });
}

async function login(email, password) {
  try {
    const payload = await api("/api/login", { method: "POST", body: JSON.stringify({ email, password }) });
    state.user = payload.user;
    state.view = "dashboard";
    state.cache = {};
    showApp();
    toast(`Welcome, ${state.user.name}`);
  } catch (error) {
    toast(error.message, "error");
  }
}

function showAuth() {
  $("#authScreen").classList.remove("hidden");
  $("#appShell").classList.add("hidden");
}

function showApp() {
  $("#authScreen").classList.add("hidden");
  $("#appShell").classList.remove("hidden");
  $("#roleBadge").textContent = state.user.role;
  $("#userLabel").textContent = `${state.user.name} (${state.user.role})`;
  renderNav();
  renderView();
}

function renderNav() {
  $("#mainNav").innerHTML = navItems[state.user.role]
    .map(([key, label]) => `<button type="button" class="${state.view === key ? "active" : ""}" data-view="${key}" ${state.view === key ? 'aria-current="page"' : ""}>${escapeHtml(label)}</button>`)
    .join("");
  $$("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.dataset.view;
      renderNav();
      renderView();
    });
  });
}

async function renderView() {
  const content = $("#content");
  content.innerHTML = renderEmpty("Loading...");
  try {
    if (state.user.role === "ADMIN") await renderAdminView();
    if (state.user.role === "TEACHER") await renderTeacherView();
    if (state.user.role === "STUDENT") await renderStudentView();
    content.focus();
  } catch (error) {
    content.innerHTML = renderEmpty(error.message);
    toast(error.message, "error");
  }
}

$("#logoutButton").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: "{}" });
  state.user = null;
  state.cache = {};
  showAuth();
});

async function renderAdminView() {
  if (state.view === "dashboard") return renderAdminDashboard();
  if (state.view === "profile") return renderProfileView();
  if (state.view === "users") return renderAdminUsers();
  if (state.view === "academic") return renderAdminAcademic();
  if (state.view === "reports") return renderAdminReports();
  if (state.view === "notices") return renderAdminNotices();
}

async function renderAdminDashboard() {
  setTitle("Admin Dashboard", "Whole college overview");
  const data = await api("/api/dashboard");
  $("#content").innerHTML = `
    <div class="section-stack">
      <section class="metric-grid">
        ${metric("Students", data.stats.students)}
        ${metric("Teachers", data.stats.teachers)}
        ${metric("Pending approvals", data.stats.pending)}
        ${metric("Subjects", data.stats.subjects)}
      </section>
      <section class="split-grid">
        <div class="panel">
          <div class="panel-header"><div><h3>Pending approvals</h3><p>New users waiting for admin action.</p></div></div>
          ${adminPendingTable(data.pendingUsers)}
        </div>
        <div class="panel">
          <div class="panel-header"><div><h3>Low attendance alerts</h3><p>Students below subject threshold.</p></div></div>
          ${riskTable(data.lowAttendance)}
        </div>
      </section>
      <section class="panel">
        <div class="panel-header"><div><h3>Toppers</h3><p>Highest overall subject performance.</p></div></div>
        ${marksSummaryTable(data.toppers)}
      </section>
    </div>
  `;
  bindAdminActions();
}

function metric(label, value) {
  const initials = String(label)
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase();
  return `
    <div class="metric-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <div class="metric-meta" aria-hidden="true">${escapeHtml(initials)}</div>
    </div>
  `;
}

function adminPendingTable(users) {
  return renderTable(
    ["Name", "Role", "Email", "Status", "Action"],
    users.map((user) => `
      <tr>
        <td>${escapeHtml(user.name)}</td>
        <td>${chip(user.role)}</td>
        <td>${escapeHtml(user.email)}</td>
        <td>${chip(user.status)}</td>
        <td><div class="actions">
          <button class="secondary-action" data-admin-action="approve" data-id="${user.id}">Approve</button>
          <button class="danger-action" data-admin-action="reject" data-id="${user.id}">Reject</button>
        </div></td>
      </tr>
    `),
    "No pending users."
  );
}

function riskTable(rows) {
  return renderTable(
    ["Roll", "Student", "Subject", "Attendance", "Required"],
    rows.map((row) => `
      <tr>
        <td>${escapeHtml(row.rollNumber)}</td>
        <td>${escapeHtml(row.studentName)}</td>
        <td>${escapeHtml(row.code)} ${escapeHtml(row.subjectName)}</td>
        <td>${progress(row.percentage, row.atRisk)} ${asPercent(row.percentage)}</td>
        <td>${asPercent(row.required)}</td>
      </tr>
    `),
    "No low attendance alerts."
  );
}

function marksSummaryTable(rows) {
  return renderTable(
    ["Roll", "Student", "Subject", "Performance"],
    rows.map((row) => `
      <tr>
        <td>${escapeHtml(row.rollNumber)}</td>
        <td>${escapeHtml(row.studentName)}</td>
        <td>${escapeHtml(row.code)} ${escapeHtml(row.subjectName)}</td>
        <td>${progress(row.percentage, row.failed)} ${asPercent(row.percentage)}</td>
      </tr>
    `),
    "No marks data yet."
  );
}

async function renderAdminUsers() {
  setTitle("Users", "Approvals, status, and password reset");
  state.cache.usersPage = state.cache.usersPage || 0;
  $("#content").innerHTML = `
    <section class="panel">
      <div class="toolbar">
        <label>Role
          <select id="filterRole"><option value="">All roles</option><option>STUDENT</option><option>TEACHER</option><option>ADMIN</option></select>
        </label>
        <label>Status
          <select id="filterStatus"><option value="">All statuses</option><option>PENDING</option><option>APPROVED</option><option>REJECTED</option><option>INACTIVE</option></select>
        </label>
        <label>Search
          <input id="filterSearch" placeholder="Name, email, roll, employee id">
        </label>
        <button class="secondary-action" id="runUserFilter">Filter</button>
      </div>
      <div id="userEditPanel" class="panel nested-panel hidden"></div>
      <div id="usersTable">${renderEmpty("Loading users...")}</div>
    </section>
  `;
  $("#runUserFilter").addEventListener("click", () => {
    state.cache.usersPage = 0;
    loadUsers();
  });
  await loadUsers();
}

async function loadUsers() {
  const params = new URLSearchParams({
    role: $("#filterRole").value,
    status: $("#filterStatus").value,
    q: $("#filterSearch").value,
    page: String(state.cache.usersPage || 0),
  });
  const { users, page, hasNext } = await api(`/api/admin/users?${params}`);
  $("#usersTable").innerHTML = renderTable(
    ["Name", "Role", "Email", "Department", "Section", "Status", "Academic", "Action"],
    users.map((user) => `
      <tr>
        <td>${escapeHtml(user.name)}<br><span class="muted">${escapeHtml(user.rollNumber || user.employeeId || "")}</span></td>
        <td>${chip(user.role)}</td>
        <td>${escapeHtml(user.email)}</td>
        <td>${escapeHtml(user.departmentName || "-")}</td>
        <td>${escapeHtml(user.sectionName || "-")}</td>
        <td>${chip(user.status)}</td>
        <td>${user.role === "STUDENT" ? studentStatusSelect(user) : "-"}</td>
        <td><div class="actions">
          <button class="secondary-action" data-admin-action="edit" data-id="${user.id}">Edit</button>
          ${user.status === "PENDING" ? `<button class="secondary-action" data-admin-action="approve" data-id="${user.id}">Approve</button>` : ""}
          ${user.role !== "ADMIN" && user.status !== "REJECTED" ? `<button class="danger-action" data-admin-action="reject" data-id="${user.id}">Reject</button>` : ""}
          <button class="secondary-action" data-admin-action="reset-password" data-id="${user.id}">Reset</button>
        </div></td>
      </tr>
    `),
    "No users match the filters."
  ) + paginationControls("users", page, hasNext);
  bindPaginationControls({
    users: async (dir) => {
      state.cache.usersPage = Math.max(0, (state.cache.usersPage || 0) + dir);
      await loadUsers();
    },
  });
  bindAdminActions();
  $$("[data-student-status]").forEach((select) => {
    select.addEventListener("change", async () => {
      try {
        await api(`/api/admin/students/${select.dataset.studentStatus}/status`, {
          method: "POST",
          body: JSON.stringify({ academicStatus: select.value }),
        });
        toast("Student status updated.");
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });
}

function studentStatusSelect(user) {
  const statuses = ["ACTIVE", "INACTIVE", "DETAINED", "NOT_ELIGIBLE"];
  return `<select data-student-status="${user.id}">${statuses.map((status) => `<option ${status === user.academicStatus ? "selected" : ""}>${status}</option>`).join("")}</select>`;
}

function bindAdminActions() {
  $$("[data-admin-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        if (button.dataset.adminAction === "edit") {
          await showUserEditForm(button.dataset.id);
          return;
        }
        if (button.dataset.adminAction === "reject" && !window.confirm("Reject this user account? They will not be able to log in.")) {
          return;
        }
        const payload = await api(`/api/admin/users/${button.dataset.id}/${button.dataset.adminAction}`, {
          method: "POST",
          body: "{}",
        });
        toast(payload.temporaryPassword ? `Password reset successful. Temporary password: ${payload.temporaryPassword}` : payload.message);
        renderView();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });
}

async function showUserEditForm(userId) {
  const [{ user }, structures] = await Promise.all([
    api(`/api/admin/users/${userId}`),
    api("/api/admin/structures"),
  ]);
  const p = user.profile || {};
  const panel = $("#userEditPanel");
  panel.classList.remove("hidden");
  panel.innerHTML = `
    <form id="editUserForm" class="form-stack">
      <div class="panel-header">
        <div><h3>Edit ${escapeHtml(user.role.toLowerCase())}</h3><p>Update account and profile details.</p></div>
        <button class="secondary-action" type="button" id="cancelUserEdit">Cancel</button>
      </div>
      <input type="hidden" name="id" value="${user.id}">
      <div class="form-grid">
        <label>Name<input name="name" value="${escapeHtml(user.name)}" required></label>
        <label>Email<input name="email" type="email" value="${escapeHtml(user.email)}" required></label>
      </div>
      ${user.role === "STUDENT" ? studentEditFields(p, structures) : ""}
      ${user.role === "TEACHER" ? teacherEditFields(p, structures) : ""}
      <button class="primary-action" type="submit">Save Changes</button>
    </form>
  `;
  $("#cancelUserEdit").addEventListener("click", () => panel.classList.add("hidden"));
  $("#editUserForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const payload = await api(`/api/admin/users/${userId}`, {
        method: "PUT",
        body: JSON.stringify(formData(event.currentTarget)),
      });
      toast(payload.message);
      panel.classList.add("hidden");
      await loadUsers();
    } catch (error) {
      toast(error.message, "error");
    }
  });
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function studentEditFields(profile, structures) {
  return `
    <div class="form-grid">
      <label>Roll number<input name="rollNumber" value="${escapeHtml(profile.rollNumber || "")}" required></label>
      <label>Semester<input name="semester" type="number" min="1" value="${escapeHtml(profile.semester || 1)}"></label>
    </div>
    <div class="form-grid">
      <label>Department<select name="departmentId">${optionListWithPlaceholder(structures.departments, "Unassigned", (d) => `${d.code} - ${d.name}`).replace(`value="${profile.departmentId}"`, `value="${profile.departmentId}" selected`)}</select></label>
      <label>Course<select name="courseId">${optionListWithPlaceholder(structures.courses, "Unassigned", (c) => `${c.code} - ${c.name}`).replace(`value="${profile.courseId}"`, `value="${profile.courseId}" selected`)}</select></label>
    </div>
    <div class="form-grid">
      <label>Section<select name="sectionId">${optionListWithPlaceholder(structures.sections, "Unassigned", (s) => `${s.name} Sem ${s.semester}`).replace(`value="${profile.sectionId}"`, `value="${profile.sectionId}" selected`)}</select></label>
      <label>Phone<input name="phone" value="${escapeHtml(profile.phone || "")}"></label>
    </div>
    <label>Guardian name<input name="guardianName" value="${escapeHtml(profile.guardianName || "")}"></label>
  `;
}

function teacherEditFields(profile, structures) {
  return `
    <div class="form-grid">
      <label>Employee ID<input name="employeeId" value="${escapeHtml(profile.employeeId || "")}" required></label>
      <label>Title<input name="title" value="${escapeHtml(profile.title || "Assistant Professor")}"></label>
    </div>
    <div class="form-grid">
      <label>Department<select name="departmentId">${optionListWithPlaceholder(structures.departments, "Unassigned", (d) => `${d.code} - ${d.name}`).replace(`value="${profile.departmentId}"`, `value="${profile.departmentId}" selected`)}</select></label>
      <label>Phone<input name="phone" value="${escapeHtml(profile.phone || "")}"></label>
    </div>
  `;
}

async function renderAdminAcademic() {
  setTitle("Academic Structures", "Departments, courses, sections, subjects");
  const data = await api("/api/admin/structures");
  state.cache.structures = data;
  $("#content").innerHTML = `
    <div class="section-stack">
      <section class="three-grid">
        ${structureForm("Department", "departmentForm", [
          ["name", "Name", "Computer Science Engineering"],
          ["code", "Code", "CSE"]
        ])}
        ${structureForm("Course", "courseForm", [
          ["name", "Name", "B.Tech CSE"],
          ["code", "Code", "BTECH-CSE"],
          ["durationSemesters", "Duration semesters", "8", "number"]
        ], departmentSelect("departmentId", data.departments))}
        ${structureForm("Section", "sectionForm", [
          ["name", "Name", "CSE-A"],
          ["semester", "Semester", "3", "number"],
          ["capacity", "Capacity", "60", "number"]
        ], departmentSelect("departmentId", data.departments) + courseSelect("courseId", data.courses))}
      </section>
      <section class="split-grid">
        ${subjectForm(data)}
        ${assignmentForm(data)}
      </section>
      <section class="panel">
        <div class="panel-header"><div><h3>Subjects</h3><p>Course-wise subjects and thresholds.</p></div></div>
        ${renderTable(["Code", "Subject", "Course", "Semester", "Attendance"], data.subjects.map((s) => `
          <tr><td>${escapeHtml(s.code)}</td><td>${escapeHtml(s.name)}</td><td>${escapeHtml(s.courseName)}</td><td>${s.semester}</td><td>${asPercent(s.attendanceRequired)}</td></tr>
        `))}
      </section>
      <section class="panel">
        <div class="panel-header"><div><h3>Teacher assignments</h3><p>Approved teachers linked to sections and subjects.</p></div></div>
        ${renderTable(["Teacher", "Subject", "Section"], data.assignments.map((a) => `
          <tr><td>${escapeHtml(a.teacherName)}</td><td>${escapeHtml(a.code)} ${escapeHtml(a.subjectName)}</td><td>${escapeHtml(a.sectionName)}</td></tr>
        `), "No teacher assignments yet.")}
      </section>
    </div>
  `;
  bindAcademicForms();
}

function structureForm(title, id, fields, prepend = "") {
  return `
    <form class="panel form-stack" id="${id}">
      <div class="panel-header"><div><h3>${title}</h3><p>Create a new ${title.toLowerCase()}.</p></div></div>
      ${prepend}
      ${fields.map(([name, label, placeholder, type = "text"]) => `<label>${label}<input name="${name}" type="${type}" placeholder="${placeholder || ""}" required></label>`).join("")}
      <button class="primary-action" type="submit">Add ${title}</button>
    </form>
  `;
}

function departmentSelect(name, departments) {
  return `<label>Department<select name="${name}" required>${optionList(departments, "", (d) => `${d.code} - ${d.name}`)}</select></label>`;
}

function courseSelect(name, courses) {
  return `<label>Course<select name="${name}" required>${optionList(courses, "", (c) => `${c.code} - ${c.name}`)}</select></label>`;
}

function subjectForm(data) {
  return `
    <form class="panel form-stack" id="subjectForm">
      <div class="panel-header"><div><h3>Subject</h3><p>Add semester subjects with attendance rules.</p></div></div>
      ${departmentSelect("departmentId", data.departments)}
      ${courseSelect("courseId", data.courses)}
      <div class="form-grid">
        <label>Code<input name="code" placeholder="CS401" required></label>
        <label>Name<input name="name" placeholder="Operating Systems" required></label>
      </div>
      <div class="form-grid">
        <label>Semester<input name="semester" type="number" value="3" min="1" required></label>
        <label>Credit<input name="credit" type="number" value="4" min="1" required></label>
      </div>
      <label>Attendance required %
        <input name="attendanceRequired" type="number" value="75" min="0" max="100" required>
      </label>
      <button class="primary-action" type="submit">Add Subject</button>
    </form>
  `;
}

function assignmentForm(data) {
  return `
    <form class="panel form-stack" id="assignmentForm">
      <div class="panel-header"><div><h3>Teacher Assignment</h3><p>Link one teacher with one subject and class.</p></div></div>
      <label>Teacher<select name="teacherId" required>${optionList(data.teachers, "", (t) => `${t.name} (${t.employeeId})`)}</select></label>
      <label>Subject<select name="subjectId" required>${optionList(data.subjects, "", (s) => `${s.code} - ${s.name}`)}</select></label>
      <label>Section<select name="sectionId" required>${optionList(data.sections, "", (s) => `${s.name} Sem ${s.semester}`)}</select></label>
      <button class="primary-action" type="submit">Assign Teacher</button>
    </form>
  `;
}

function bindAcademicForms() {
  const routes = {
    departmentForm: "/api/admin/departments",
    courseForm: "/api/admin/courses",
    sectionForm: "/api/admin/sections",
    subjectForm: "/api/admin/subjects",
    assignmentForm: "/api/admin/assignments",
  };
  Object.entries(routes).forEach(([id, route]) => {
    $(`#${id}`).addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await api(route, { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
        toast("Saved.");
        renderAdminAcademic();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });
}

async function renderAdminReports() {
  setTitle("Reports", "Attendance, marks, toppers, failed students");
  state.cache.attendanceReportPage = 0;
  state.cache.marksReportPage = 0;
  const structures = await api("/api/admin/structures");
  $("#content").innerHTML = `
    <div class="section-stack">
      <section class="panel">
        <div class="panel-header"><div><h3>Attendance report</h3><p>Filter by section and subject, then export CSV.</p></div></div>
        <div class="toolbar">
          <label>Section<select id="attendanceSection"><option value="">All sections</option>${optionList(structures.sections, "", (s) => `${s.name} Sem ${s.semester}`)}</select></label>
          <label>Subject<select id="attendanceSubject"><option value="">All subjects</option>${optionList(structures.subjects, "", (s) => `${s.code} - ${s.name}`)}</select></label>
          <label>From<input id="attendanceFrom" type="date"></label>
          <label>To<input id="attendanceTo" type="date" value="${state.bootstrap.today}"></label>
          <button class="secondary-action" id="runAttendanceReport">Run</button>
          <button class="secondary-action" id="exportAttendance">Export CSV</button>
        </div>
        <div id="attendanceReport">${renderEmpty("Run a report to view results.")}</div>
      </section>
      <section class="panel">
        <div class="panel-header"><div><h3>Marks report</h3><p>Review exam performance and failures.</p></div></div>
        <div class="toolbar">
          <label>Section<select id="marksSection"><option value="">All sections</option>${optionList(structures.sections, "", (s) => `${s.name} Sem ${s.semester}`)}</select></label>
          <label>Subject<select id="marksSubject"><option value="">All subjects</option>${optionList(structures.subjects, "", (s) => `${s.code} - ${s.name}`)}</select></label>
          <button class="secondary-action" id="runMarksReport">Run</button>
          <button class="secondary-action" id="exportMarks">Export CSV</button>
        </div>
        <div id="marksReport">${renderEmpty("Run a report to view results.")}</div>
      </section>
    </div>
  `;
  $("#runAttendanceReport").addEventListener("click", () => {
    state.cache.attendanceReportPage = 0;
    loadAttendanceReport();
  });
  $("#runMarksReport").addEventListener("click", () => {
    state.cache.marksReportPage = 0;
    loadMarksReport();
  });
  $("#exportAttendance").addEventListener("click", () => {
    window.location = `/api/admin/reports/attendance.csv?${attendanceReportParams()}`;
  });
  $("#exportMarks").addEventListener("click", () => {
    window.location = `/api/admin/reports/marks.csv?${marksReportParams()}`;
  });
}

function attendanceReportParams() {
  return new URLSearchParams({
    sectionId: $("#attendanceSection").value,
    subjectId: $("#attendanceSubject").value,
    from: $("#attendanceFrom").value,
    to: $("#attendanceTo").value,
    page: String(state.cache.attendanceReportPage || 0),
  });
}

function marksReportParams() {
  return new URLSearchParams({
    sectionId: $("#marksSection").value,
    subjectId: $("#marksSubject").value,
    page: String(state.cache.marksReportPage || 0),
  });
}

async function loadAttendanceReport() {
  const { rows, page, hasNext } = await api(`/api/admin/reports/attendance?${attendanceReportParams()}`);
  $("#attendanceReport").innerHTML = renderTable(
    ["Roll", "Student", "Section", "Subject", "Attendance", "Required", "Status"],
    rows.map((row) => `
      <tr>
        <td>${escapeHtml(row.rollNumber)}</td>
        <td>${escapeHtml(row.studentName)}</td>
        <td>${escapeHtml(row.sectionName)}</td>
        <td>${escapeHtml(row.code)} ${escapeHtml(row.subjectName)}</td>
        <td>${progress(row.percentage, row.atRisk)} ${asPercent(row.percentage)}</td>
        <td>${asPercent(row.required)}</td>
        <td>${row.atRisk ? chip("Low") : chip("OK")}</td>
      </tr>
    `)
  ) + paginationControls("attendanceReport", page, hasNext);
  bindPaginationControls({
    attendanceReport: async (dir) => {
      state.cache.attendanceReportPage = Math.max(0, (state.cache.attendanceReportPage || 0) + dir);
      await loadAttendanceReport();
    },
  });
}

async function loadMarksReport() {
  const { rows, page, hasNext } = await api(`/api/admin/reports/marks?${marksReportParams()}`);
  $("#marksReport").innerHTML = renderTable(
    ["Roll", "Student", "Section", "Subject", "Exam", "Marks", "Grade"],
    rows.map((row) => `
      <tr>
        <td>${escapeHtml(row.rollNumber)}</td>
        <td>${escapeHtml(row.studentName)}</td>
        <td>${escapeHtml(row.sectionName)}</td>
        <td>${escapeHtml(row.code)} ${escapeHtml(row.subjectName)}</td>
        <td>${escapeHtml(row.examName)}</td>
        <td>${row.marksObtained ?? "-"} / ${row.maxMarks}</td>
        <td>${chip(row.grade)}</td>
      </tr>
    `)
  ) + paginationControls("marksReport", page, hasNext);
  bindPaginationControls({
    marksReport: async (dir) => {
      state.cache.marksReportPage = Math.max(0, (state.cache.marksReportPage || 0) + dir);
      await loadMarksReport();
    },
  });
}

async function renderAdminNotices() {
  setTitle("Notices", "Messages for students and teachers");
  $("#content").innerHTML = `
    <section class="panel">
      <form id="noticeForm" class="form-stack">
        <div class="panel-header"><div><h3>Publish notice</h3><p>Visible on role dashboards.</p></div></div>
        <label>Audience
          <select name="audience"><option>ALL</option><option>STUDENT</option><option>TEACHER</option></select>
        </label>
        <label>Title<input name="title" required></label>
        <label>Message<textarea name="message" required></textarea></label>
        <button class="primary-action" type="submit">Publish</button>
      </form>
    </section>
  `;
  $("#noticeForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/admin/notices", { method: "POST", body: JSON.stringify(formData(event.currentTarget)) });
      event.currentTarget.reset();
      toast("Notice published.");
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

async function renderTeacherView() {
  if (state.view === "dashboard") return renderTeacherDashboard();
  if (state.view === "profile") return renderProfileView();
  if (state.view === "attendance") return renderTeacherAttendance();
  if (state.view === "marks") return renderTeacherMarks();
  if (state.view === "analytics") return renderTeacherAnalytics();
}

async function teacherClasses() {
  if (!state.cache.teacherClasses) {
    state.cache.teacherClasses = (await api("/api/teacher/classes")).classes;
  }
  return state.cache.teacherClasses;
}

function classOptions(classes) {
  return classes.map((item) => `<option value="${item.subjectId}|${item.sectionId}">${escapeHtml(item.sectionName)} - ${escapeHtml(item.code)} ${escapeHtml(item.subjectName)}</option>`).join("");
}

function selectedClass(selectId) {
  const [subjectId, sectionId] = $(`#${selectId}`).value.split("|");
  return { subjectId, sectionId };
}

async function renderTeacherDashboard() {
  setTitle("Teacher Dashboard", "Today classes and academic alerts");
  const data = await api("/api/dashboard");
  $("#content").innerHTML = `
    <div class="section-stack">
      <section class="metric-grid">
        ${metric("Assigned classes", data.classes.length)}
        ${metric("Attendance risks", data.atRiskAttendance.length)}
        ${metric("Marks risks", data.atRiskMarks.length)}
        ${metric("Upcoming exams", data.upcomingExams.length)}
      </section>
      <section class="split-grid">
        <div class="panel">
          <div class="panel-header"><div><h3>My classes</h3><p>Subjects and sections assigned by admin.</p></div></div>
          ${renderTable(["Class", "Subject", "Students"], data.classes.map((c) => `<tr><td>${escapeHtml(c.sectionName)}</td><td>${escapeHtml(c.code)} ${escapeHtml(c.subjectName)}</td><td>${c.studentCount}</td></tr>`), "No classes assigned.")}
        </div>
        <div class="panel">
          <div class="panel-header"><div><h3>Students at risk</h3><p>Low attendance in your subjects.</p></div></div>
          ${riskTable(data.atRiskAttendance)}
        </div>
      </section>
      <section class="split-grid">
        <div class="panel">
          <div class="panel-header"><div><h3>Recent submissions</h3><p>Attendance saved recently.</p></div></div>
          ${renderTable(["Date", "Subject", "Section", "Records"], data.recentAttendance.map((r) => `<tr><td>${escapeHtml(r.attendanceDate)}</td><td>${escapeHtml(r.code)} ${escapeHtml(r.subjectName)}</td><td>${escapeHtml(r.sectionName)}</td><td>${r.records}</td></tr>`), "No attendance submissions yet.")}
        </div>
        <div class="panel">
          <div class="panel-header"><div><h3>Notices</h3><p>Admin messages for teachers.</p></div></div>
          ${noticeList(data.notices)}
        </div>
      </section>
    </div>
  `;
}

async function renderTeacherAttendance() {
  setTitle("Attendance", "Daily attendance entry");
  const classes = await teacherClasses();
  $("#content").innerHTML = `
    <section class="panel">
      <div class="toolbar">
        <label>Class<select id="attendanceClass">${classOptions(classes)}</select></label>
        <label>Date<input id="attendanceDate" type="date" value="${state.bootstrap.today}" max="${state.bootstrap.today}"></label>
        <button class="secondary-action" id="markAllPresent" type="button">Mark All Present</button>
        <button class="primary-action" id="saveAttendance">Save Attendance</button>
      </div>
      <div id="attendanceRoster">${classes.length ? renderEmpty("Select a class and date to mark attendance.") : renderEmpty("No classes assigned.")}</div>
    </section>
  `;
  $("#attendanceClass").addEventListener("change", loadAttendanceRoster);
  $("#attendanceDate").addEventListener("change", loadAttendanceRoster);
  $("#markAllPresent").addEventListener("click", markSelectedPresent);
  $("#saveAttendance").addEventListener("click", saveAttendance);
  if (classes.length) await loadAttendanceRoster();
}

async function loadAttendanceRoster() {
  const { subjectId, sectionId } = selectedClass("attendanceClass");
  const date = $("#attendanceDate").value;
  if (date > state.bootstrap.today) {
    $("#attendanceRoster").innerHTML = renderEmpty("Attendance cannot be recorded for a future date.");
    toast("Attendance cannot be recorded for a future date.", "error");
    return;
  }
  const { students } = await api(`/api/teacher/attendance?subjectId=${subjectId}&sectionId=${sectionId}&date=${date}`);
  $("#attendanceRoster").innerHTML = renderTable(
    ["Select", "Roll", "Student", "Status", "Note to student"],
    students.map((student) => `
      <tr data-student-id="${student.studentId}">
        <td><input type="checkbox" data-attendance-select checked></td>
        <td>${escapeHtml(student.rollNumber)}</td>
        <td>${escapeHtml(student.name)}</td>
        <td><select data-attendance-status>
          ${["Present", "Absent", "On Leave"].map((status) => `<option ${status === student.status ? "selected" : ""}>${status}</option>`).join("")}
        </select></td>
        <td><input data-attendance-reason value="${escapeHtml(student.reason || "")}" placeholder="Optional note"></td>
      </tr>
    `),
    "No students enrolled."
  );
}

function markSelectedPresent() {
  const rows = $$("tr[data-student-id]");
  const selected = rows.filter((row) => $("[data-attendance-select]", row)?.checked);
  const targets = selected.length ? selected : rows;
  targets.forEach((row) => {
    const status = $("[data-attendance-status]", row);
    if (status) status.value = "Present";
  });
  toast(selected.length ? "Selected students marked Present." : "All students marked Present.");
}

async function saveAttendance() {
  const { subjectId, sectionId } = selectedClass("attendanceClass");
  const records = $$("tr[data-student-id]").map((row) => ({
    studentId: row.dataset.studentId,
    status: $("[data-attendance-status]", row).value,
    reason: $("[data-attendance-reason]", row).value,
  }));
  try {
    await api("/api/teacher/attendance", {
      method: "POST",
      body: JSON.stringify({
        subjectId,
        sectionId,
        date: $("#attendanceDate").value,
        correctionReason: "",
        records,
      }),
    });
    toast("Attendance saved.");
    state.cache.teacherClasses = null;
  } catch (error) {
    toast(error.message, "error");
  }
}

async function renderTeacherMarks() {
  setTitle("Marks", "Exam components and grade entry");
  const classes = await teacherClasses();
  $("#content").innerHTML = `
    <div class="section-stack">
      <section class="panel">
        <div class="toolbar">
          <label>Class<select id="marksClass">${classOptions(classes)}</select></label>
          <label>Exam<select id="marksExam"></select></label>
          <button class="secondary-action" id="loadExams">Load exams</button>
          <button class="secondary-action" id="loadMarks">Load marks</button>
          <button class="primary-action" id="saveMarks">Save Marks</button>
        </div>
        <div id="marksRoster">${classes.length ? renderEmpty("Load exams first.") : renderEmpty("No classes assigned.")}</div>
      </section>
      <form class="panel form-inline" id="examForm">
        <label>Name<input name="name" placeholder="Internal Test 2" required></label>
        <label>Type<select name="examType"><option>INTERNAL</option><option>MID</option><option>FINAL</option><option>ASSIGNMENT</option><option>PRACTICAL</option></select></label>
        <label>Max marks<input name="maxMarks" type="number" value="20" min="1" required></label>
        <label>Date<input name="examDate" type="date" value="${state.bootstrap.today}"></label>
        <button class="primary-action" type="submit">Create Exam</button>
      </form>
    </div>
  `;
  $("#marksClass").addEventListener("change", loadExamOptions);
  $("#loadExams").addEventListener("click", loadExamOptions);
  $("#loadMarks").addEventListener("click", loadMarksRoster);
  $("#saveMarks").addEventListener("click", saveMarks);
  $("#examForm").addEventListener("submit", createExam);
  if (classes.length) await loadExamOptions();
}

async function loadExamOptions() {
  const { subjectId, sectionId } = selectedClass("marksClass");
  const { exams } = await api(`/api/teacher/exams?subjectId=${subjectId}&sectionId=${sectionId}`);
  $("#marksExam").innerHTML = exams.map((exam) => `<option value="${exam.id}">${escapeHtml(exam.name)} (${exam.maxMarks})</option>`).join("");
}

async function createExam(event) {
  event.preventDefault();
  const { subjectId, sectionId } = selectedClass("marksClass");
  const data = { ...formData(event.currentTarget), subjectId, sectionId };
  try {
    await api("/api/teacher/exams", { method: "POST", body: JSON.stringify(data) });
    toast("Exam created.");
    await loadExamOptions();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function loadMarksRoster() {
  const { subjectId, sectionId } = selectedClass("marksClass");
  const examId = $("#marksExam").value;
  if (!examId) {
    toast("Create or select an exam first.", "error");
    return;
  }
  const { students, exam } = await api(`/api/teacher/marks?subjectId=${subjectId}&sectionId=${sectionId}&examId=${examId}`);
  $("#marksRoster").innerHTML = renderTable(
    ["Roll", "Student", `Marks / ${exam.max_marks}`, "Grade", "Remarks"],
    students.map((student) => `
      <tr data-student-id="${student.studentId}">
        <td>${escapeHtml(student.rollNumber)}</td>
        <td>${escapeHtml(student.name)}</td>
        <td><input class="small-input" data-marks value="${student.marksObtained ?? ""}" type="number" min="0" max="${exam.max_marks}" step="0.1"></td>
        <td>${chip(student.grade || "N/A")}</td>
        <td><input data-remarks value="${escapeHtml(student.remarks || "")}" placeholder="Optional"></td>
      </tr>
    `),
    "No students enrolled."
  );
}

async function saveMarks() {
  const { subjectId, sectionId } = selectedClass("marksClass");
  const examId = $("#marksExam").value;
  const records = $$("tr[data-student-id]").map((row) => ({
    studentId: row.dataset.studentId,
    marksObtained: $("[data-marks]", row).value,
    remarks: $("[data-remarks]", row).value,
  }));
  try {
    await api("/api/teacher/marks", { method: "POST", body: JSON.stringify({ subjectId, sectionId, examId, records }) });
    toast("Marks saved.");
    await loadMarksRoster();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function renderTeacherAnalytics() {
  setTitle("Analytics", "Class-wise attendance and marks");
  const classes = await teacherClasses();
  $("#content").innerHTML = `
    <section class="panel">
      <div class="toolbar">
        <label>Class<select id="analyticsClass">${classOptions(classes)}</select></label>
        <button class="secondary-action" id="runAnalytics">Run analytics</button>
        <button class="secondary-action" id="exportTeacherAttendance">Export attendance</button>
      </div>
      <div id="analyticsResult">${classes.length ? renderEmpty("Run analytics for a class.") : renderEmpty("No classes assigned.")}</div>
    </section>
  `;
  $("#runAnalytics").addEventListener("click", loadTeacherAnalytics);
  $("#exportTeacherAttendance").addEventListener("click", () => {
    const { subjectId, sectionId } = selectedClass("analyticsClass");
    window.location = `/api/teacher/export/attendance.csv?subjectId=${subjectId}&sectionId=${sectionId}`;
  });
  if (classes.length) await loadTeacherAnalytics();
}

async function loadTeacherAnalytics() {
  const { subjectId, sectionId } = selectedClass("analyticsClass");
  const data = await api(`/api/teacher/analytics?subjectId=${subjectId}&sectionId=${sectionId}`);
  $("#analyticsResult").innerHTML = `
    <div class="split-grid">
      <div>
        <div class="panel-header"><div><h3>Attendance</h3><p>Percentage by student.</p></div></div>
        ${riskTable(data.attendance)}
      </div>
      <div>
        <div class="panel-header"><div><h3>Marks</h3><p>Average performance by student.</p></div></div>
        ${marksSummaryTable(data.marks)}
      </div>
    </div>
  `;
}

async function renderStudentView() {
  if (state.view === "dashboard") return renderStudentDashboard();
  if (state.view === "profile") return renderProfileView();
  if (state.view === "attendance") return renderStudentAttendance();
  if (state.view === "marks") return renderStudentMarks();
}

async function renderStudentDashboard() {
  setTitle("Student Dashboard", "Your attendance, marks, and notices");
  const data = await api("/api/dashboard");
  $("#content").innerHTML = `
    <div class="section-stack">
      <section class="metric-grid">
        ${metric("Overall %", asPercent(data.overall.percentage))}
        ${metric("CGPA estimate", data.overall.cgpaEstimate)}
        ${metric("Current grade", data.overall.grade)}
        ${metric("Attendance risks", data.attendance.filter((item) => item.atRisk).length)}
      </section>
      <section class="split-grid">
        <div class="panel">
          <div class="panel-header"><div><h3>Subject attendance</h3><p>Required threshold is tracked per subject.</p></div></div>
          ${studentAttendanceSummary(data.attendance)}
        </div>
        <div class="panel">
          <div class="panel-header"><div><h3>Latest marks</h3><p>Recently graded components.</p></div></div>
          ${studentMarksTable(data.latestMarks)}
        </div>
      </section>
      <section class="panel">
        <div class="panel-header"><div><h3>Notices</h3><p>Messages from admin and teachers.</p></div></div>
        ${noticeList(data.notices)}
      </section>
    </div>
  `;
}

function studentAttendanceSummary(rows) {
  return renderTable(
    ["Subject", "Present", "Absent", "Leave", "Attendance", "Status"],
    rows.map((row) => `
      <tr>
        <td>${escapeHtml(row.code)} ${escapeHtml(row.name)}</td>
        <td>${row.presentClasses || 0}</td>
        <td>${row.absentClasses || 0}</td>
        <td>${row.leaveClasses || 0}</td>
        <td>${progress(row.percentage, row.atRisk)} ${asPercent(row.percentage)}</td>
        <td>${row.atRisk ? chip("Below required") : chip("Eligible")}</td>
      </tr>
    `),
    "No attendance records yet."
  );
}

function studentMarksTable(rows) {
  return renderTable(
    ["Subject", "Exam", "Marks", "Grade", "Date"],
    rows.map((row) => `
      <tr>
        <td>${escapeHtml(row.code)} ${escapeHtml(row.subjectName)}</td>
        <td>${escapeHtml(row.examName)}</td>
        <td>${row.marksObtained ?? "-"} / ${row.maxMarks}</td>
        <td>${chip(row.grade || "N/A")}</td>
        <td>${escapeHtml(row.examDate || "-")}</td>
      </tr>
    `),
    "No marks entered yet."
  );
}

function noticeList(rows) {
  if (!rows.length) return renderEmpty("No notices.");
  return `<div class="notice-list">${rows
    .map((item) => `<div class="notice-item"><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.message)}</p><span class="muted">${escapeHtml(item.createdAt || "")}</span></div>`)
    .join("")}</div>`;
}

function absenceReasonCell(record) {
  if (record.status !== "Absent") {
    return `<span class="muted">Not required</span>`;
  }
  return `
    <div class="absence-reason-box">
      <textarea data-absence-reason="${record.attendanceId}" maxlength="500" placeholder="Write why you were absent">${escapeHtml(record.studentAbsenceReason || "")}</textarea>
      <button class="secondary-action" type="button" data-save-absence="${record.attendanceId}">Save Reason</button>
      ${record.absenceReasonUpdatedAt ? `<span class="muted">Updated ${escapeHtml(record.absenceReasonUpdatedAt)}</span>` : ""}
    </div>
  `;
}

function bindAbsenceReasonButtons() {
  $$("[data-save-absence]").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = button.dataset.saveAbsence;
      const textarea = $(`[data-absence-reason="${id}"]`);
      try {
        await api(`/api/student/attendance/${id}/reason`, {
          method: "POST",
          body: JSON.stringify({ reason: textarea.value }),
        });
        toast("Absence reason saved.");
        await renderStudentAttendance();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });
}

async function renderProfileView() {
  setTitle("Profile", "Account details and password");
  const payload = await api("/api/me");
  const user = payload.user;
  const p = user.profile || {};
  $("#content").innerHTML = `
    <div class="section-stack">
      <section class="panel">
        <div class="profile-header">
          <div class="profile-photo">
            ${user.photoData ? `<img src="${user.photoData}" alt="${escapeHtml(user.name)} photo">` : `<span>${escapeHtml(user.name.slice(0, 1).toUpperCase())}</span>`}
          </div>
          <div>
            <p class="eyebrow">${escapeHtml(user.role)}</p>
            <h3>${escapeHtml(user.name)}</h3>
            <p class="muted">${escapeHtml(user.email)}</p>
          </div>
          <div>${chip(user.role === "STUDENT" ? p.academicStatus : user.role)}</div>
        </div>
        ${user.role !== "ADMIN" ? `
          <form id="profilePhotoForm" class="photo-form">
            <label>Profile photo
              <input name="photo" type="file" accept="image/*" required>
            </label>
            <button class="secondary-action" type="submit">Upload Photo</button>
          </form>
        ` : ""}
        <div class="three-grid">
          ${profileItem("Role", user.role)}
          ${user.role === "STUDENT" ? profileItem("Roll number", p.rollNumber || "-") : ""}
          ${user.role === "TEACHER" ? profileItem("Employee ID", p.employeeId || "-") : ""}
          ${profileItem("Department", p.departmentName || "-")}
          ${user.role === "STUDENT" ? profileItem("Course", p.courseName || "-") : ""}
          ${user.role === "STUDENT" ? profileItem("Section", p.sectionName || "-") : ""}
          ${user.role === "STUDENT" ? profileItem("Semester", p.semester || "-") : ""}
          ${profileItem("Phone", p.phone || "-")}
          ${user.role === "STUDENT" ? profileItem("Guardian", p.guardianName || "-") : ""}
          ${user.role === "TEACHER" ? profileItem("Title", p.title || "-") : ""}
        </div>
      </section>
      <section class="panel">
        <form id="changePasswordForm" class="form-stack">
          <div class="panel-header"><div><h3>Change Password</h3><p>Enter your current password before setting a new one.</p></div></div>
          <div class="form-grid">
            <label>Current password<input name="currentPassword" type="password" required></label>
            <label>New password<input name="newPassword" type="password" minlength="6" required></label>
          </div>
          <label>Confirm new password<input name="confirmPassword" type="password" minlength="6" required></label>
          <button class="primary-action" type="submit">Change Password</button>
        </form>
      </section>
    </div>
  `;
  $("#changePasswordForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const payload = await api("/api/change-password", {
        method: "POST",
        body: JSON.stringify(formData(event.currentTarget)),
      });
      event.currentTarget.reset();
      toast(payload.message);
    } catch (error) {
      toast(error.message, "error");
    }
  });
  $("#profilePhotoForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = event.currentTarget.photo.files[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      toast("Please choose an image file.", "error");
      return;
    }
    if (file.size > 650 * 1024) {
      toast("Photo is too large. Choose an image under 650 KB.", "error");
      return;
    }
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const payload = await api("/api/profile/photo", {
          method: "POST",
          body: JSON.stringify({ photoData: reader.result }),
        });
        toast(payload.message);
        await renderProfileView();
      } catch (error) {
        toast(error.message, "error");
      }
    };
    reader.readAsDataURL(file);
  });
}

function profileItem(label, value) {
  return `<div class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "-")}</strong></div>`;
}

async function renderStudentAttendance() {
  setTitle("Attendance", "Subject summary and day-wise log");
  state.cache.studentAttendancePage = state.cache.studentAttendancePage || 0;
  const data = await api(`/api/student/attendance?page=${state.cache.studentAttendancePage}`);
  $("#content").innerHTML = `
    <div class="section-stack">
      <section class="panel">
        <div class="panel-header"><div><h3>Subject-wise attendance</h3><p>Eligibility is highlighted automatically.</p></div></div>
        ${studentAttendanceSummary(data.summary)}
      </section>
      <section class="panel">
        <div class="panel-header"><div><h3>Attendance log</h3><p>Recent presents, absents, and leave entries.</p></div></div>
        ${renderTable(["Date", "Subject", "Status", "Teacher note", "Your absence reason"], data.records.map((r) => `
          <tr>
            <td>${escapeHtml(r.attendanceDate)}</td>
            <td>${escapeHtml(r.code)} ${escapeHtml(r.subjectName)}</td>
            <td>${chip(r.status)}</td>
            <td>${escapeHtml(r.reason || "-")}</td>
            <td>${absenceReasonCell(r)}</td>
          </tr>
        `), "No attendance entries yet.")}
        ${paginationControls("studentAttendance", data.page, data.hasNext)}
      </section>
    </div>
  `;
  bindAbsenceReasonButtons();
  bindPaginationControls({
    studentAttendance: async (dir) => {
      state.cache.studentAttendancePage = Math.max(0, (state.cache.studentAttendancePage || 0) + dir);
      await renderStudentAttendance();
    },
  });
}

async function renderStudentMarks() {
  setTitle("Marks", "Exam-wise result details");
  state.cache.studentMarksPage = state.cache.studentMarksPage || 0;
  const data = await api(`/api/student/marks?page=${state.cache.studentMarksPage}`);
  $("#content").innerHTML = `
    <section class="panel">
      <div class="panel-header"><div><h3>Marks and results</h3><p>Subject, component, total, and grade.</p></div></div>
      ${studentMarksTable(data.marks)}
      ${paginationControls("studentMarks", data.page, data.hasNext)}
    </section>
  `;
  bindPaginationControls({
    studentMarks: async (dir) => {
      state.cache.studentMarksPage = Math.max(0, (state.cache.studentMarksPage || 0) + dir);
      await renderStudentMarks();
    },
  });
}

init().catch((error) => {
  $("#content").innerHTML = renderEmpty(error.message);
  toast(error.message, "error");
});
