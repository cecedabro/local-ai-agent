// Password fields (chat/SPEC.md §17). ES module, used on the sign-in and settings pages.
//  - every .pw-eye button shows/hides the password field next to it;
//  - #pw-form (settings) changes your own password via POST /api/me/password.

for (const btn of document.querySelectorAll(".pw-eye")) {
  const input = btn.parentElement.querySelector("input");
  btn.addEventListener("click", () => {
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.setAttribute("aria-pressed", String(show));
    const label = show ? "Hide password" : "Show password";
    btn.setAttribute("aria-label", label);
    btn.title = label;
    input.focus();
  });
}

// Back to dots before submitting, so the browser treats it as a password (and offers to save it).
function hideAll(form) {
  for (const btn of form.querySelectorAll(".pw-eye[aria-pressed=true]")) btn.click();
}
document.getElementById("login-form")?.addEventListener("submit", (ev) => hideAll(ev.target), { capture: true });

const form = document.getElementById("pw-form");
if (form) {
  const msg = document.getElementById("pw-msg");
  const say = (text, cls) => { msg.textContent = text; msg.className = "small " + cls; };
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const current = form.current.value.trim(), next = form.new.value.trim(), again = form.again.value.trim();
    if (next !== again) { say("The two new passwords are not the same.", "bad"); form.again.focus(); return; }
    if (next.length < 10) { say("The new password must be at least 10 characters.", "bad"); form.new.focus(); return; }
    const btn = form.querySelector("button.primary");
    btn.disabled = true;
    say("", "");
    try {
      const res = await fetch("/api/me/password", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Coder": "1" },
        body: JSON.stringify({ current, new: next }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        hideAll(form);
        form.current.value = form.new.value = form.again.value = "";
        const n = body.other_sessions_ended || 0;
        say(n ? `Password changed. Signed out on ${n} other device${n === 1 ? "" : "s"}.` : "Password changed.", "ok");
      } else if (res.status === 429) {
        say("Too many wrong tries. Wait 15 minutes and try again.", "bad");
      } else {
        say(body.error || "That did not work. Try again.", "bad");
        if (res.status === 403) form.current.focus();
      }
    } catch {
      say("No connection. Try again.", "bad");
    } finally {
      btn.disabled = false;
    }
  });
}
