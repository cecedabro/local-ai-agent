// Passkeys in the browser (chat/SPEC.md §10). Human-owned. A classic script used by the sign-in,
// invite and account pages; it wires up whichever of their elements exist.
(function () {
  const H = { "Content-Type": "application/json", "X-Coder": "1" };
  const supported = !!(window.PublicKeyCredential && navigator.credentials && navigator.credentials.create);

  const b64uToBuf = (s) => {
    const b = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));
    return Uint8Array.from(b, (c) => c.charCodeAt(0)).buffer;
  };
  const bufToB64u = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

  function creationOptions(o) {
    if (PublicKeyCredential.parseCreationOptionsFromJSON) return PublicKeyCredential.parseCreationOptionsFromJSON(o);
    return { ...o, challenge: b64uToBuf(o.challenge), user: { ...o.user, id: b64uToBuf(o.user.id) },
      excludeCredentials: (o.excludeCredentials || []).map((c) => ({ ...c, id: b64uToBuf(c.id) })) };
  }
  function requestOptions(o) {
    if (PublicKeyCredential.parseRequestOptionsFromJSON) return PublicKeyCredential.parseRequestOptionsFromJSON(o);
    return { ...o, challenge: b64uToBuf(o.challenge),
      allowCredentials: (o.allowCredentials || []).map((c) => ({ ...c, id: b64uToBuf(c.id) })) };
  }
  function toJSON(cred) {
    if (cred.toJSON) return cred.toJSON();
    const r = cred.response, out = { id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      authenticatorAttachment: cred.authenticatorAttachment, response: { clientDataJSON: bufToB64u(r.clientDataJSON) } };
    if (r.attestationObject) out.response.attestationObject = bufToB64u(r.attestationObject);
    if (r.authenticatorData) {
      out.response.authenticatorData = bufToB64u(r.authenticatorData);
      out.response.signature = bufToB64u(r.signature);
      if (r.userHandle) out.response.userHandle = bufToB64u(r.userHandle);
    }
    return out;
  }
  async function post(path, body) {
    return fetch(path, { method: "POST", headers: H, body: JSON.stringify(body || {}), credentials: "same-origin" });
  }

  /** Add a passkey for the signed-in user. Resolves to true when it was saved. */
  async function addPasskey(name) {
    const opts = await (await post("/api/passkeys/register/options")).json();
    const cred = await navigator.credentials.create({ publicKey: creationOptions(opts) });
    const res = await post("/api/passkeys/register/verify", { credential: toJSON(cred), name });
    return res.ok;
  }
  /** Sign in with any passkey of this site. Resolves to the response (ok, 401 or 429). */
  async function signIn() {
    const opts = await (await post("/api/passkeys/login/options")).json();
    const cred = await navigator.credentials.get({ publicKey: requestOptions(opts) });
    return post("/api/passkeys/login/verify", { credential: toJSON(cred) });
  }
  function deviceName() {
    const ua = navigator.userAgent;
    return /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Mac/.test(ua) ? "Mac" : /Android/.test(ua) ? "Android phone"
      : /Windows/.test(ua) ? "Windows PC" : "Passkey";
  }
  window.dewdropPasskeys = { supported, addPasskey, signIn };

  document.addEventListener("DOMContentLoaded", () => {
    // sign-in page
    const signBtn = document.getElementById("passkey-signin");
    if (signBtn) {
      signBtn.hidden = !supported;
      signBtn.addEventListener("click", async () => {
        const err = document.getElementById("login-error");
        err.textContent = "";
        try {
          const res = await signIn();
          if (res.ok) { location.href = "/"; return; }
          err.textContent = res.status === 429 ? "Too many tries. Wait 15 minutes and try again." : "That passkey did not work.";
        } catch (e) { err.textContent = "No passkey was used."; }
      });
    }
    // invite page: offered after the account is made (invite.js shows #passkey-offer)
    const offerBtn = document.getElementById("passkey-add-now");
    if (offerBtn) {
      offerBtn.addEventListener("click", async () => {
        try { await addPasskey(deviceName()); } catch (e) { /* skipped: password still works */ }
        location.href = "/";
      });
    }
    // account page
    const list = document.getElementById("passkey-list");
    if (list) {
      const draw = async () => {
        const items = await (await fetch("/api/passkeys", { headers: { "X-Coder": "1" } })).json();
        list.innerHTML = items.length ? items.map((p) => `<li><b>${p.name.replace(/[&<>"]/g, "")}</b>
          <span class="muted small">added ${new Date(p.created).toLocaleDateString()}${p.last_used ? " · last used " + new Date(p.last_used).toLocaleDateString() : ""}</span>
          <button class="link small" data-remove="${p.id}">Remove</button></li>`).join("")
          : '<li class="muted small">No passkeys yet.</li>';
      };
      list.addEventListener("click", async (ev) => {
        const b = ev.target.closest("[data-remove]");
        if (b && confirm("Remove this passkey? You can still sign in with your password.")) {
          await fetch("/api/passkeys/" + b.dataset.remove, { method: "DELETE", headers: { "X-Coder": "1" } });
          draw();
        }
      });
      const add = document.getElementById("passkey-add");
      const msg = document.getElementById("passkey-msg");
      add.hidden = !supported;
      if (!supported) msg.textContent = "This browser does not support passkeys.";
      add.addEventListener("click", async () => {
        msg.textContent = "";
        try { msg.textContent = (await addPasskey(deviceName())) ? "Passkey added." : "That did not work. Try again."; }
        catch (e) { msg.textContent = "No passkey was added."; }
        draw();
      });
      draw();
    }
  });
})();
