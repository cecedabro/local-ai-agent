// The app icon picker on the account page (chat/SPEC.md §13.4). ES module.

/**
 * TODO (task E15). Wire #icon-pick:
 *   1. GET /api/me (fetch, same origin); mark the button[data-icon] whose data-icon equals me.icon with class "on".
 *   2. Click on a button[data-icon]: PATCH /api/me {icon: its data-icon} with headers {"X-Coder": "1",
 *      "Content-Type": "application/json"}; ok -> move class "on" to it and set #icon-msg text to
 *      "Saved. Remove Dewdrop from your home screen and add it again to see the new icon."; not ok -> #icon-msg
 *      "That did not work, try again."
 */
async function start() {
  // TODO (task E15): see the comment above.
}

start();
