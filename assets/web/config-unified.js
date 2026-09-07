(() => {
  "use strict";

  const ENGINE_URL = "/api/voice-engine";
  const GAINS_URL = "/api/voice-output-gains";
  const RESTART_URL = "/api/runtime-restart";
  const SNAPSHOT_URL = "/api/snapshot";
  const LLM_OPTIONS_URL = "/api/llm-options";

  const saveButton = document.querySelector("#llm-save");
  const message = document.querySelector("#llm-message");
  const configPanel = document.querySelector("#panel-config");
  if (!saveButton) return;

  let restartRequired = false;
  let restartInFlight = false;
  let saveGeneration = 0;

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

  function setRestartRequired(required) {
    restartRequired = Boolean(required);
    saveButton.dataset.restartRequired = restartRequired ? "1" : "0";
    if (!restartInFlight) saveButton.textContent = restartRequired ? "Restart" : "Save";
  }

  function markConfigDirty() {
    saveGeneration += 1;
    if (restartRequired && !restartInFlight) {
      setRestartRequired(false);
      if (message) message.textContent = "Unsaved changes.";
    }
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

  async function waitForGlobalSaveResult(generation, timeoutMs = 15000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      if (generation !== saveGeneration) return false;
      const text = String(message?.textContent || "").trim().toLowerCase();
      if (text.includes("failed") || text.includes("error") || text.includes("échec") || text.includes("erreur")) return false;
      if (!saveButton.disabled && !text.includes("saving") && !text.includes("enregistrement")) return true;
    }
    return false;
  }

  async function verifyCanonicalConfig() {
    const response = await fetch(LLM_OPTIONS_URL, { cache: "no-store" });
    if (!response.ok) throw new Error(`configuration verification failed: HTTP ${response.status}`);
    const data = await response.json();
    const wake = document.querySelector("#wake-word");
    if (wake) {
      const expected = String(wake.value || "").trim();
      const actual = String(data.selected_wake_word || "").trim();
      if (actual !== expected) {
        throw new Error(`wake word was not persisted (expected ${expected || "Disabled"}, got ${actual || "Disabled"})`);
      }
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
      if (message) message.textContent = "Configuration applied.";
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

    const generationAtSave = saveGeneration;
    saveExtendedConfig()
      .then(async (required) => {
        const globalSaveOk = await waitForGlobalSaveResult(generationAtSave);
        if (!globalSaveOk) return;
        await verifyCanonicalConfig();
        if (generationAtSave !== saveGeneration) return;
        if (required) setRestartRequired(true);
      })
      .catch((error) => {
        setRestartRequired(false);
        if (message) message.textContent = `Save failed: ${error.message || error}`;
      });
  }, true);

  if (configPanel) {
    configPanel.addEventListener("input", (event) => {
      if (event.target === saveButton) return;
      markConfigDirty();
    }, true);
    configPanel.addEventListener("change", (event) => {
      if (event.target === saveButton) return;
      markConfigDirty();
    }, true);
  }

  const observer = new MutationObserver(hideDuplicateButtons);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  hideDuplicateButtons();
})();
