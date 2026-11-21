// A small glass dialog instead of the browser's prompt() and confirm() (UX review UI-2). ES module, no inline styles.
//   ask({title, text?, fields?, ok?, danger?}) -> Promise: null when cancelled, else {name: value} of the fields
//   (or {} when there are no fields: a confirmation).
//   fields: [{name, label, value?, placeholder?, options?: [[value, label], ...]}]; options -> a <select>.

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export function ask({ title, text = "", fields = [], ok = "OK", danger = false } = {}) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "gdialog";
    const inputs = fields.map((f, i) => {
      const id = `gd-${f.name}-${i}`;
      const control = f.options
        ? `<select id="${id}" name="${esc(f.name)}">${f.options.map(([v, l]) =>
          `<option value="${esc(v)}"${String(v) === String(f.value ?? "") ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>`
        : `<input id="${id}" name="${esc(f.name)}" value="${esc(f.value ?? "")}" placeholder="${esc(f.placeholder ?? "")}" autocomplete="off">`;
      return `<label for="${id}">${esc(f.label)}</label>${control}`;
    }).join("");
    dlg.innerHTML = `<form method="dialog"><h3>${esc(title)}</h3>${text ? `<p>${esc(text)}</p>` : ""}` +
      (inputs ? `<div class="gd-fields">${inputs}</div>` : "") +
      `<div class="gd-actions"><button type="button" value="cancel" class="gd-cancel">Cancel</button>` +
      `<button value="ok" class="${danger ? "danger" : "primary"}">${esc(ok)}</button></div></form>`;
    document.body.appendChild(dlg);
    const form = dlg.querySelector("form");
    let result = null;
    dlg.querySelector(".gd-cancel").addEventListener("click", () => dlg.close());
    form.addEventListener("submit", () => {
      result = {};
      for (const f of fields) result[f.name] = String(form.elements[f.name].value || "").trim();
    });
    dlg.addEventListener("close", () => { dlg.remove(); resolve(result); });
    dlg.showModal();
    const first = dlg.querySelector("input,select") || dlg.querySelector("button.primary,button.danger");
    first?.focus();
    if (first?.select) first.select();
  });
}
