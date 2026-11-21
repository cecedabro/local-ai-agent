// Invite page: the token is in the URL fragment (/invite#TOKEN), so it never reaches server logs.
document.getElementById("invite-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target, err = document.getElementById("invite-error");
  const btn = f.querySelector("button[type=submit], button:not([type])");
  if (btn?.disabled) return;                           // no second account request while the first runs (UI-10)
  if (btn) btn.disabled = true;
  err.textContent = "";
  const res = await fetch("/api/invites/accept", { method: "POST", headers: { "Content-Type": "application/json", "X-Coder": "1" },
    body: JSON.stringify({ token: location.hash.slice(1), username: f.username.value.trim(), password: f.password.value }) });
  if (res.ok) {
    if (window.dewdropPasskeys && window.dewdropPasskeys.supported) {
      f.remove();                                      // the form is done: only the passkey offer stays (UI-10)
      document.getElementById("passkey-offer").hidden = false;
      return;
    }
    location.href = "/";
    return;
  }
  if (btn) btn.disabled = false;
  const body = await res.json().catch(() => ({}));
  err.textContent = res.status === 409 ? "That username is taken." : (body.error || "This invite link is not valid any more.");
});
