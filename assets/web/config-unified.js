(() => {
  "use strict";

  const ENGINE_URL = "/api/voice-engine";
  const GAINS_URL = "/api/voice-output-gains";
  const RESTART_URL = "/api/runtime-restart";
  const SNAPSHOT_URL = "/api/snapshot";
  const LLM_OPTIONS_URL = "/api/llm-options";
  const APPLY_POLICY_URL = "/assets/web/config-apply-policy.json";

  const saveButton = document.querySelector("#llm-save");
  const message = document.querySelector("#llm-message");
  const configPanel = document.querySelector("#panel-config");
  if (!saveButton) return;

  let applyPolicy = { default_mode: "engine-restart", hot: [], ignore: [], groups: {} };
  let restartPending = false;
  let restartInFlight = false;
  let saveInFlight = false;
  let generation = 0;
  const dirty = new Map();
  const baseline = new Map();

  function errorMessage(data, response) {
    return data?.error?.message || data?.message || response?.statusText || `HTTP ${response?.status || "?"}`;
  }

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {})
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(errorMessage(data, response));
    return data;
  }

  async function loadApplyPolicy() {
    try {
      const response = await fetch(APPLY_POLICY_URL, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (data && typeof data === "object") {
        applyPolicy = {
          default_mode: data.default_mode === "hot" ? "hot" : "engine-restart",
          hot: Array.isArray(data.hot) ? data.hot : [],
          ignore: Array.isArray(data.ignore) ? data.ignore : [],
          groups: data.groups && typeof data.groups === "object" ? data.groups : {}
        };
      }
    } catch (error) {
      console.warn("Could not load config apply policy; using safe restart-by-default policy", error);
    }
  }

  function matchesAny(target, selectors) {
    if (!(target instanceof Element)) return false;
    return selectors.some((selector) => {
      try {
        return target.matches(selector) || Boolean(target.closest(selector));
      } catch (_error) {
        return false;
      }
    });
  }

  function groupForTarget(target) {
    if (!(target instanceof Element)) return "";
    for (const [name, selector] of Object.entries(applyPolicy.groups || {})) {
      try {
        if (target.matches(selector) || target.closest(selector)) return name;
      } catch (_error) {
      }
    }
    return "";
  }

  function configKey(target) {
    const group = groupForTarget(target);
    if (group) return group;
    if (target.id) return target.id;
    if (target.name) return `name:${target.name}`;
    return "";
  }

  function applyModeForTarget(target) {
    if (matchesAny(target, applyPolicy.ignore || [])) return "ignore";
    if (matchesAny(target, applyPolicy.hot || [])) return "hot";
    return applyPolicy.default_mode || "engine-restart";
  }

  function enginePayload() {
    const section = document.querySelector(".rv2d-voice-engine");
    const select = section?.querySelector(".rv2d-voice-engine-select");
    if (!section || !select || select.disabled) return null;
    return {
      voice_engine: select.value,
      realtime_model: section.querySelector(".rv2d-realtime-model")?.value.trim() || "",
      realtime_voice: section.querySelector(".rv2d-realtime-voice")?.value.trim() || ""
    };
  }

  function gainsPayload() {
    const section = document.querySelector(".rv2d-voice-gains");
    const cloud = section?.querySelector(".rv2d-cloud-gain");
    const local = section?.querySelector(".rv2d-local-gain");
    if (!cloud || !local) return null;
    return { cloud_gain: Number(cloud.value), local_gain: Number(local.value) };
  }

  function serializeTarget(target, key = configKey(target)) {
    if (key === "voice-engine") return JSON.stringify(enginePayload());
    if (key === "voice-output-gains") return JSON.stringify(gainsPayload());
    if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement)) return "";
    if (target instanceof HTMLInputElement && target.type === "radio" && target.name) {
      const checked = configPanel?.querySelector(`input[name="${CSS.escape(target.name)}"]:checked`);
      return String(checked?.value || "");
    }
    if (target instanceof HTMLInputElement && target.type === "checkbox") return target.checked ? "1" : "0";
    if (target instanceof HTMLSelectElement && target.multiple) {
      return JSON.stringify([...target.selectedOptions].map((option) => option.value));
    }
    return String(target.value ?? "");
  }

  function rememberBaseline(target) {
    const mode = applyModeForTarget(target);
    const key = configKey(target);
    if (!key || mode === "ignore" || baseline.has(key)) return;
    baseline.set(key, serializeTarget(target, key));
  }

  function markDirty(target) {
    const mode = applyModeForTarget(target);
    const key = configKey(target);
    if (!key || mode === "ignore") return;
    if (!baseline.has(key)) baseline.set(key, serializeTarget(target, key));
    const current = serializeTarget(target, key);
    if (baseline.get(key) === current) {
      dirty.delete(key);
    } else {
      generation += 1;
      dirty.set(key, { mode, generation, target });
    }
    syncButtonState();
  }

  function syncButtonState() {
    if (restartInFlight) {
      saveButton.disabled = true;
      saveButton.textContent = "Restarting…";
      return;
    }
    if (saveInFlight) {
      saveButton.disabled = true;
      saveButton.textContent = "Saving…";
      return;
    }
    if (dirty.size > 0) {
      saveButton.disabled = false;
      saveButton.textContent = "Save";
      saveButton.dataset.restartRequired = restartPending ? "1" : "0";
      return;
    }
    if (restartPending) {
      saveButton.disabled = false;
      saveButton.textContent = "Restart";
      saveButton.dataset.restartRequired = "1";
      return;
    }
    saveButton.disabled = true;
    saveButton.textContent = "Save";
    saveButton.dataset.restartRequired = "0";
  }

  function hideDuplicateButtons() {
    for (const button of document.querySelectorAll(".rv2d-voice-engine-save, .rv2d-voice-gains-save")) {
      button.classList.add("hidden");
      button.setAttribute("aria-hidden", "true");
      button.tabIndex = -1;
    }
    for (const section of document.querySelectorAll(".rv2d-voice-engine, .rv2d-voice-gains")) {
      const actions = [...section.querySelectorAll("div")].find((item) => item.querySelector(".rv2d-voice-engine-save, .rv2d-voice-gains-save"));
      if (actions) actions.classList.add("hidden");
    }
  }

  async function saveExtendedConfig(keys) {
    const requests = [];
    if (keys.has("voice-engine")) {
      const payload = enginePayload();
      if (payload) requests.push(postJson(ENGINE_URL, payload));
    }
    if (keys.has("voice-output-gains")) {
      const payload = gainsPayload();
      if (payload) requests.push(postJson(GAINS_URL, payload));
    }
    return Promise.all(requests);
  }

  async function waitForGlobalSaveResult(timeoutMs = 15000) {
    const deadline = Date.now() + timeoutMs;
    let observedSaving = false;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      const text = String(message?.textContent || "").trim().toLowerCase();
      if (text.includes("saving") || text.includes("enregistrement")) {
        observedSaving = true;
        continue;
      }
      if (text.includes("failed") || text.includes("error") || text.includes("échec") || text.includes("erreur")) {
        throw new Error(message?.textContent || "configuration save failed");
      }
      if (observedSaving || text.includes("saved") || text.includes("sauvegard")) return true;
    }
    throw new Error("global configuration save did not complete before timeout");
  }

  async function verifyCanonicalConfig(keys) {
    if (!keys.has("wake-word")) return;
    const response = await fetch(LLM_OPTIONS_URL, { cache: "no-store" });
    if (!response.ok) throw new Error(`configuration verification failed: HTTP ${response.status}`);
    const data = await response.json();
    const wake = document.querySelector("#wake-word");
    const expected = String(wake?.value || "").trim();
    const actual = String(data.selected_wake_word || "").trim();
    if (actual !== expected) {
      throw new Error(`wake word was not persisted (expected ${expected || "Disabled"}, got ${actual || "Disabled"})`);
    }
  }

  function globalMode(entries) {
    for (const entry of entries.values()) {
      if (entry.mode === "engine-restart") return "engine-restart";
    }
    return "hot";
  }

  function commitSavedEntries(savedEntries) {
    for (const [key, saved] of savedEntries.entries()) {
      const current = dirty.get(key);
      if (current && current.generation === saved.generation) {
        baseline.set(key, serializeTarget(current.target, key));
        dirty.delete(key);
      }
    }
  }

  async function saveConfiguration() {
    if (saveInFlight || dirty.size === 0) return;
    saveInFlight = true;
    syncButtonState();
    const savedEntries = new Map(dirty);
    const keys = new Set(savedEntries.keys());
    const mode = globalMode(savedEntries);
    try {
      const extendedSave = saveExtendedConfig(keys);
      await waitForGlobalSaveResult();
      await extendedSave;
      await verifyCanonicalConfig(keys);
      commitSavedEntries(savedEntries);
      if (mode === "engine-restart") restartPending = true;
      if (message) {
        if (restartPending) {
          message.textContent = `${savedEntries.size} setting(s) saved. Restart required to apply engine changes.`;
        } else {
          message.textContent = `${savedEntries.size} setting(s) saved and applied without engine restart.`;
        }
      }
    } catch (error) {
      if (message) message.textContent = `Save failed: ${error.message || error}`;
    } finally {
      saveInFlight = false;
      syncButtonState();
    }
  }

  async function waitUntilReady(timeoutMs = 60000) {
    const deadline = Date.now() + timeoutMs;
    let reloadObserved = false;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      try {
        const response = await fetch(SNAPSHOT_URL, { cache: "no-store" });
        if (!response.ok) continue;
        const snapshot = await response.json();
        const runtime = snapshot.runtime_status || {};
        const state = String(runtime.semantic_state || "").toLowerCase();
        if (!runtime.ready || state === "starting") reloadObserved = true;
        if (reloadObserved && runtime.ready && state !== "starting") return true;
      } catch (_error) {
      }
    }
    return false;
  }

  async function restartRuntime() {
    if (restartInFlight || dirty.size > 0 || !restartPending) return;
    restartInFlight = true;
    syncButtonState();
    if (message) message.textContent = "Applying configuration…";
    try {
      await postJson(RESTART_URL, {});
      const ready = await waitUntilReady();
      if (!ready) throw new Error("runtime did not become ready before timeout");
      restartPending = false;
      if (message) message.textContent = "Configuration applied.";
    } catch (error) {
      if (message) message.textContent = `Restart failed: ${error.message || error}`;
    } finally {
      restartInFlight = false;
      syncButtonState();
    }
  }

  saveButton.addEventListener("click", (event) => {
    if (restartPending && dirty.size === 0) {
      event.preventDefault();
      event.stopImmediatePropagation();
      restartRuntime();
      return;
    }
    if (dirty.size === 0) {
      event.preventDefault();
      event.stopImmediatePropagation();
      syncButtonState();
      return;
    }
    // app-main.js owns the canonical /api/llm-config POST. Let that handler run,
    // and coordinate the additional engine/gain endpoints around it.
    window.setTimeout(saveConfiguration, 0);
  }, true);

  if (configPanel) {
    for (const eventName of ["focusin", "pointerdown", "keydown"]) {
      configPanel.addEventListener(eventName, (event) => rememberBaseline(event.target), true);
    }
    configPanel.addEventListener("input", (event) => markDirty(event.target), true);
    configPanel.addEventListener("change", (event) => markDirty(event.target), true);
  }

  const observer = new MutationObserver(() => {
    hideDuplicateButtons();
    syncButtonState();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  loadApplyPolicy().finally(() => {
    hideDuplicateButtons();
    window.setTimeout(syncButtonState, 500);
  });
})();
