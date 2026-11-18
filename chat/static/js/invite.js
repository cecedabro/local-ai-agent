// Invite page: the token is in the URL fragment (/invite#TOKEN), so it never reaches server logs.
document.getElementById("invite-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target, err = document.getElementById("invite-error");
  err.textContent = "";
  const res = await fetch("/api/invites/accept", { method: "POST", headers: { "Content-Type": "application/json", "X-Coder": "1" },
    body: JSON.stringify({ token: location.hash.slice(1), username: f.username.value.trim(), password: f.password.value }) });
  if (res.ok) { location.href = "/"; return; }
  const body = await res.json().catch(() => ({}));
  err.textContent = res.status === 409 ? "That username is taken." : (body.error || "This invite link is not valid any more.");
});
