// Own one task-list/category read at a time. Polls coalesce; explicit refreshes
// retire pre-mutation reads. Only GETs use this helper, never mutation requests.
export function createTaskListReader({ read, onStart, onData, onError, onSettled }, clock = globalThis) {
  let active = false;
  let current = null;

  function retire() {
    const old = current;
    current = null;
    if (!old) return;
    clock.clearTimeout(old.timer);
    old.controller.abort();
  }

  function activate() {
    retire();
    active = true;
  }

  function deactivate() {
    active = false;
    retire();
  }

  function run(replace = true) {
    if (!active || (current && !replace)) return false;
    retire();
    const owner = { controller: new AbortController(), timer: undefined };
    current = owner;
    onStart(replace);

    function finish(error, value) {
      if (!active || current !== owner) return;
      current = null;
      clock.clearTimeout(owner.timer);
      try {
        if (error) onError(error);
        else {
          try {
            onData(value);
          } catch (invalid) {
            onError(invalid);
          }
        }
      } finally {
        onSettled();
      }
    }

    // A slow read is not replaced by every poll, and a hanging read cannot own
    // the page indefinitely. Abort is best-effort; ownership also rejects late
    // callbacks from transports (including mocks) that ignore AbortSignal.
    owner.timer = clock.setTimeout(() => {
      finish(new Error("Read timed out. Try again."));
      owner.controller.abort();
    }, 15_000);
    Promise.resolve()
      .then(() => {
        if (!active || current !== owner) return undefined;
        return read(owner.controller.signal);
      })
      .then(
        (value) => finish(null, value),
        (error) => finish(error || new Error("Read failed.")),
      );
    return true;
  }

  return Object.freeze({ activate, deactivate, retire, run });
}
