(() => {
  "use strict";

  const ENGINE_URL = "/api/voice-engine";
  const GAINS_URL = "/api/voice-output-gains";
  const RESTART_URL = "/api/runtime-restart";
  const SNAPSHOT_URL = "/api/snapshot";
  const LLM_OPTIONS_URL = "/api/llm-options";

  const saveButton = document.querySelector("#llm-save");
  const message = document.querySelector("#llm-message");
  if (!saveButton) return;

  let restartRequired = false;
  let restartInFlight = false;
  let wakeSelectorReady = false;
  let wakeSelectorDirty = false;

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

  function visibleWakeValue() {
    const select = document.querySelector("#wake-word-select");
    if (select) return String(select.value || "").trim();
    const legacy = document.querySelector("#wake-word");
    return String(legacy?.value || "").trim();
  }

  // app-main.js still owns the legacy /api/llm-config request. Until that form is
  // fully migrated, make the visible common selector authoritative at the final
  // request boundary so a stale hidden #wake-word value can never overwrite it.
  if (!window.__lsaWakeCanonicalFetchInstalled) {
    window.__lsaWakeCanonicalFetchInstalled = true;
    const baseFetch = window.fetch.bind(window);
    window.fetch = (input, init = undefined) => {
      const rawUrl = typeof input === "string" ? input : String(input?.url || "");
      let parsedPath = rawUrl;
      try {
        parsedPath = new URL(rawUrl, window.location.href).pathname;
      } catch (_error) {
      }
      if (parsedPath === "/api/llm-config" && init && typeof init.body === "string") {
        try {
          const payload = JSON.parse(init.body);
          payload.wake_word = visibleWakeValue();
          init = { ...init, body: JSON.stringify(payload) };
        } catch (_error) {
        }
      }
      return baseFetch(input, init);
    };
  }

  function setRestartRequired(required) {
    restartRequired = Boolean(required);
    saveButton.dataset.restartRequired = restartRequired ? "1" : "0";
    if (!restartInFlight) saveButton.textContent = restartRequired ? "Restart" : "Save";
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

  function normalizeWakeOption(entry) {
    if (typeof entry === "string") return { value: entry, label: entry };
    if (!entry || typeof entry !== "object") return null;
    const value = String(entry.id || entry.value || entry.name || entry.label || "").trim();
    if (!value) return null;
    return { value, label: String(entry.label || entry.name || value).trim() || value };
  }

  function ensureWakeOption(select, value) {
    const normalized = String(value || "").trim();
    if (!normalized) return;
    if ([...select.options].some((option) => option.value === normalized)) return;
    const option = document.createElement("option");
    option.value = normalized;
    option.textContent = normalized;
    select.append(option);
  }

  function syncWakeSelectorFromCanonical() {
    if (!wakeSelectorReady || wakeSelectorDirty) return;
    const legacy = document.querySelector("#wake-word");
    const select = document.querySelector("#wake-word-select");
    if (!legacy || !select) return;
    const canonical = String(legacy.value || "").trim();
    ensureWakeOption(select, canonical);
    if (select.value !== canonical) select.value = canonical;
  }

  async function ensureWakeSelector() {
    const legacy = document.querySelector("#wake-word");
    if (!legacy) return;
    if (legacy.dataset.unifiedWake === "1") {
      wakeSelectorReady = true;
      syncWakeSelectorFromCanonical();
      return;
    }

    let optionsData = {};
    try {
      const response = await fetch(LLM_OPTIONS_URL, { cache: "no-store" });
      if (response.ok) optionsData = await response.json();
    } catch (_error) {
    }

    const selected = String(optionsData.selected_wake_word || legacy.value || "").trim();
    const known = Array.isArray(optionsData.wake_word_model_files) ? optionsData.wake_word_model_files : [];
    const normalized = [];
    const seen = new Set();
    for (const raw of known) {
      const item = normalizeWakeOption(raw);
      if (!item || seen.has(item.value)) continue;
      seen.add(item.value);
      normalized.push(item);
    }
    if (selected && !seen.has(selected)) normalized.unshift({ value: selected, label: selected });

    const select = document.createElement("select");
    select.id = "wake-word-select";
    select.className = legacy.className;
    const disabled = document.createElement("option");
    disabled.value = "";
    disabled.textContent = "Disabled";
    select.append(disabled);
    for (const item of normalized) {
      const option = document.createElement("option");
      option.value = item.value;
      option.textContent = item.label;
      select.append(option);
    }
    select.value = selected;
    select.addEventListener("change", () => {
      wakeSelectorDirty = true;
      legacy.value = select.value;
      legacy.dispatchEvent(new Event("input", { bubbles: true }));
      legacy.dispatchEvent(new Event("change", { bubbles: true }));
    });

    legacy.dataset.unifiedWake = "1";
    legacy.classList.add("hidden");
    legacy.setAttribute("aria-hidden", "true");
    legacy.tabIndex = -1;
    legacy.insertAdjacentElement("afterend", select);
    wakeSelectorReady = true;
    syncWakeSelectorFromCanonical();
  }

  async function saveExtendedConfig() {
    const requests = [];
    const engine = enginePayload();
    const gains = gainsPayload();
    if (engine) requests.push(postJson(ENGINE_URL, engine));
    if (gains) requests.push(postJson(GAINS_URL, gains));
    if (!requests.length) return false;
    const results = await Promise.all(requests);
    return results.some((item) => item?.restart_required);
  }

  async function waitForGlobalSaveResult(timeoutMs = 15000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      const text = String(message?.textContent || "").trim().toLowerCase();
      if (text.includes("failed") || text.includes("error") || text.includes("échec") || text.includes("erreur")) return false;
      if (!saveButton.disabled && !text.includes("saving") && !text.includes("enregistrement")) return true;
    }
    return false;
  }

  async function verifyWakePersistence() {
    const expected = visibleWakeValue();
    const response = await fetch(LLM_OPTIONS_URL, { cache: "no-store" });
    if (!response.ok) throw new Error(`wake persistence verification failed: HTTP ${response.status}`);
    const data = await response.json();
    const actual = String(data.selected_wake_word || "").trim();
    if (actual !== expected) {
      throw new Error(`wake word was not persisted (expected ${expected || "Disabled"}, got ${actual || "Disabled"})`);
    }
    return true;
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
    if (restartInFlight) return;
    restartInFlight = true;
    saveButton.disabled = true;
    saveButton.textContent = "Restarting…";
    if (message) message.textContent = "Applying configuration…";
    try {
      await postJson(RESTART_URL, {});
      const ready = await waitUntilReady();
      if (!ready) throw new Error("runtime did not become ready before timeout");
      setRestartRequired(false);
      wakeSelectorDirty = false;
      if (message) message.textContent = "Configuration applied.";
      setTimeout(syncWakeSelectorFromCanonical, 0);
    } catch (error) {
      if (message) message.textContent = `Restart failed: ${error.message || error}`;
      setRestartRequired(true);
    } finally {
      restartInFlight = false;
      saveButton.disabled = false;
      saveButton.textContent = restartRequired ? "Restart" : "Save";
    }
  }

  saveButton.addEventListener("click", (event) => {
    if (restartRequired) {
      event.preventDefault();
      event.stopImmediatePropagation();
      restartRuntime();
      return;
    }
    const wakeSelect = document.querySelector("#wake-word-select");
    const legacyWake = document.querySelector("#wake-word");
    if (wakeSelect && legacyWake) {
      legacyWake.value = wakeSelect.value;
      legacyWake.dispatchEvent(new Event("input", { bubbles: true }));
      legacyWake.dispatchEvent(new Event("change", { bubbles: true }));
    }
    saveExtendedConfig()
      .then(async (required) => {
        if (!required) return;
        const globalSaveOk = await waitForGlobalSaveResult();
        if (!globalSaveOk) return;
        try {
          await verifyWakePersistence();
          setRestartRequired(true);
        } catch (error) {
          setRestartRequired(false);
          if (message) message.textContent = `Save failed: ${error.message || error}`;
        }
      })
      .catch((error) => {
        if (message) message.textContent = `Save failed: ${error.message || error}`;
      });
  }, true);

  const observer = new MutationObserver(() => {
    hideDuplicateButtons();
    ensureWakeSelector();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  hideDuplicateButtons();
  ensureWakeSelector();
  window.setInterval(syncWakeSelectorFromCanonical, 500);
})();