// The admin page (chat/coder/templates/admin.html). Classic script.
// Fetches invites and users, draws them, creates invites.
// Never set inline styles except the CSS variables named in SPEC §9 (style.setProperty("--x", v)).

const $ = (sel) => document.querySelector(sel);

/** fetch() for /api with the CSRF header; on 401 go to /login. Returns the Response. */
async function api(path, opts) {
  opts = opts || {};
  var headers = { "X-Coder": "1" };
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  headers = Object.assign(headers, opts.headers || {});
  var body = opts.json !== undefined ? JSON.stringify(opts.json) : opts.body;
  var res = await fetch(path, { method: opts.method || "GET", headers: headers, body: body });
  if (res.status === 401) { location.href = "/login"; throw new Error("signed out"); }
  return res;
}

/** Escape text for innerHTML. */
function esc(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
  });
}

/** Load and draw the invites list from GET /api/invites. */
async function loadInvites() {
  var res = await api("/api/invites");
  var invites = await res.json();
  var ul = $("#invite-list");
  ul.innerHTML = "";
  for (var i = 0; i < invites.length; i++) {
    var inv = invites[i];
    var rights = (inv.rights || []).map(function (r) { return esc(r); }).join(", ");
    var note = inv.note ? esc(inv.note) : "";
    var used = inv.used_by ? "used by " + esc(inv.used_by) :
      '<button data-del-invite="' + inv.id + '">Delete</button>';
    var expires = esc(inv.expires);
    ul.insertAdjacentHTML("beforeend",
      '<li>' +
      '<span class="rights">' + rights + '</span>' +
      (note ? '<span class="note">' + note + '</span>' : "") +
      '<span class="expires">' + expires + '</span>' +
      '<span class="status">' + used + '</span>' +
      '</li>'
    );
  }
}

/** Load and draw the users list from GET /api/users. */
async function loadUsers() {
  var res = await api("/api/users");
  var users = await res.json();
  var current = document.body.dataset.username || "";
  var ul = $("#user-list");
  ul.innerHTML = "";
  for (var i = 0; i < users.length; i++) {
    var u = users[i];
    var username = esc(u.username);
    var rights = (u.rights || []).map(function (r) { return esc(r); }).join(", ");
    var lastLogin = u.last_login ? esc(u.last_login) : "—";
    var canDelete = u.username !== current;
    var delBtn = canDelete ?
      '<button data-del-user="' + esc(u.username) + '">Delete</button>' : "";
    ul.insertAdjacentHTML("beforeend",
      '<li>' +
      '<span class="username">' + username + '</span>' +
      '<span class="rights">' + rights + '</span>' +
      '<span class="last-login">' + lastLogin + '</span>' +
      '<span class="actions">' + delBtn + '</span>' +
      '</li>'
    );
  }
}

/** Load both invites and users lists. */
async function loadAll() {
  await Promise.all([loadInvites(), loadUsers()]);
}

/** Handle the #invite-new form submit: POST /api/invites. */
async function handleInviteCreate(e) {
  e.preventDefault();
  var form = $("#invite-new");
  var rights = [];
  var checkboxes = form.querySelectorAll('input[name="rights"]:checked');
  for (var i = 0; i < checkboxes.length; i++) {
    rights.push(checkboxes[i].value);
  }
  var note = (form.querySelector('input[name="note"]').value || "").trim();
  var res = await api("/api/invites", { method: "POST", json: { rights: rights, note: note } });
  var result = await res.json();
  var linkEl = $("#invite-link");
  var fullUrl = location.origin + result.url;
  linkEl.innerHTML =
    '<span class="link">' + esc(fullUrl) + '</span>' +
    '<button data-copy-link>Copy</button>';
  linkEl.querySelector("button[data-copy-link]").addEventListener("click", function () {
    navigator.clipboard.writeText(fullUrl);
  });
  await loadAll();
}

/** Handle delete buttons: DELETE /api/invites/<id> and DELETE /api/users/<username>. */
function handleDeletes(e) {
  var delInvite = e.target.closest("button[data-del-invite]");
  if (delInvite) {
    e.preventDefault();
    var id = Number(delInvite.dataset.delInvite);
    if (!confirm("Delete this invite?")) return;
    api("/api/invites/" + id, { method: "DELETE" });
    loadInvites();
    return;
  }
  var delUser = e.target.closest("button[data-del-user]");
  if (delUser) {
    e.preventDefault();
    var username = delUser.dataset.delUser;
    if (!confirm("Delete user " + username + "?")) return;
    api("/api/users/" + encodeURIComponent(username), { method: "DELETE" });
    loadUsers();
  }
}

/** Wire up event listeners once the DOM is ready. */
function start() {
  $("#invite-new").addEventListener("submit", handleInviteCreate);
  document.addEventListener("click", handleDeletes);
  loadAll();
}

document.addEventListener("DOMContentLoaded", start);
