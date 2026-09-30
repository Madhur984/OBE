/* Helpers common to the sign-in, teacher and administrator pages.

   Kept out of index.html on purpose: that page is a single self-contained
   file so it still works opened from disk with no server, and these helpers
   all assume one.

   The two things worth knowing are here rather than repeated on every page:
   a 401 anywhere means the session ended, so the visitor is sent to sign in
   instead of being shown an error they cannot act on; and role is checked on
   load, so an administrator who lands on the teacher page and a teacher who
   lands on the administrator page each end up where they belong. Neither is
   access control — every route behind these is guarded on the server. */

const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function flash(kind, msg) {
  const el = $("#" + kind);
  if (!el) return;
  el.textContent = msg;
  el.className = "msg " + kind + " show";
  el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), kind === "ok" ? 3500 : 9000);
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: opts.body ? { "Content-Type": "application/json" } : {}, ...opts });
  if (r.status === 401) {
    // The session ran out mid-visit. Come back to this page afterwards.
    location.replace("/login?next=" + encodeURIComponent(location.pathname + location.search));
    throw new Error("signed out");
  }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || r.statusText);
  return d;
}

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return sameDay ? `today ${time}`
    : d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " " + time;
}

async function download(path, fallbackName) {
  const r = await fetch(path);
  if (r.status === 401) { location.replace("/login"); return; }
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(d.detail || "Could not build the file.");
  }
  const name = (r.headers.get("content-disposition") || "")
    .match(/filename="([^"]+)"/)?.[1] || fallbackName;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await r.blob());
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

/** Who is signed in, sent to the right place if it is not this page. */
async function requireUser(wantRole) {
  const r = await fetch("/api/auth/me");
  if (!r.ok) {
    location.replace("/login?next=" + encodeURIComponent(location.pathname + location.search));
    return null;
  }
  const { user } = await r.json();
  if (wantRole === "admin" && user.role !== "admin") {
    location.replace("/teacher");
    return null;
  }
  return user;
}

async function signOut() {
  await fetch("/api/auth/logout", { method: "POST" });
  location.replace("/login");
}

/* ------------------------------------------------ changing your password */

function passwordDialog() {
  let dlg = $("#pwdlg");
  if (dlg) return dlg;
  dlg = document.createElement("dialog");
  dlg.id = "pwdlg";
  dlg.style.cssText = "border:1px solid var(--hair);border-radius:10px;padding:0;"
    + "background:var(--surface);color:var(--ink);max-width:380px;width:92vw;"
    + "box-shadow:var(--shadow)";
  dlg.innerHTML = `
    <form method="dialog" style="padding:18px 20px">
      <h2 style="margin:0 0 4px">Change your password</h2>
      <p class="faint" style="margin:0">
        Every other session on your account is signed out; this one stays.
      </p>
      <label for="pw_old">Current password</label>
      <input id="pw_old" type="password" autocomplete="current-password" required>
      <label for="pw_new">New password <span class="faint">— at least 8 characters</span></label>
      <input id="pw_new" type="password" autocomplete="new-password" minlength="8" required>
      <div class="msg err" id="pw_err" style="margin-top:9px"></div>
      <div class="row" style="margin-top:14px;justify-content:flex-end">
        <button value="cancel" type="submit">Cancel</button>
        <button class="primary" id="pw_go" type="button">Change it</button>
      </div>
    </form>`;
  document.body.appendChild(dlg);
  dlg.querySelector("#pw_go").onclick = async () => {
    const err = dlg.querySelector("#pw_err");
    err.classList.remove("show");
    const current = dlg.querySelector("#pw_old").value;
    const next = dlg.querySelector("#pw_new").value;
    if (next.length < 8) {
      err.textContent = "The new password needs at least 8 characters.";
      err.classList.add("show");
      return;
    }
    try {
      await api("/api/me/password", {
        method: "POST", body: JSON.stringify({ current, new: next }) });
      dlg.close();
      dlg.querySelector("#pw_old").value = dlg.querySelector("#pw_new").value = "";
      flash("ok", "Password changed.");
    } catch (ex) {
      err.textContent = ex.message;
      err.classList.add("show");
    }
  };
  return dlg;
}

function changePassword() {
  const d = passwordDialog();
  d.showModal();
  d.querySelector("#pw_old").focus();
}
