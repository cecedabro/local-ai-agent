// Sign-in page: POST /api/login, then go to the chat.
document.getElementById("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target, err = document.getElementById("login-error");
  err.textContent = "";
  const res = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json", "X-Coder": "1" },
    body: JSON.stringify({ username: f.username.value.trim().toLowerCase(), password: f.password.value }) });
  if (res.ok) { location.href = "/"; return; }
  err.textContent = res.status === 429 ? "Too many tries. Wait 15 minutes and try again." : "Wrong username or password.";
});
