(() => {
  "use strict";

  const SNAPSHOT_URL = "/api/snapshot";
  const SAVE_URL = "/api/mcp-realtime-policy";
  const VOICE_ENGINE_SAVE_URL = "/api/voice-engine";
  const POLL_MS = 2000;
  let busy = false;
  let lastSignature = "";
  let lastVoiceEngineSignature = "";

  function option(label, value, selected) {
    const item = document.createElement("option");
    item.textContent = label;
    item.value = value;
    item.selected = value === selected;
    return item;
  }

  function findMcpServers(value, depth = 0, seen = new Set()) {
    if (!value || typeof value !== "object" || depth > 6 || seen.has(value)) return null;
    seen.add(value);
    if (value.mcpServers && typeof value.mcpServers === "object" && !Array.isArray(value.mcpServers)) {
      return value.mcpServers;
    }
    for (const child of Object.values(value)) {
      if (!child || typeof child !== "object") continue;
      const found = findMcpServers(child, depth + 1, seen);
      if (found) return found;
    }
    return null;
  }

  function snapshotEnv(snapshot) {
    if (snapshot?.config?.env && typeof snapshot.config.env === "object") return snapshot.config.env;
    return {};
  }

  function voiceEngineState(snapshot) {
    const env = snapshotEnv(snapshot);
    const connectivity = String(env.CONNECTIVITY_MODE || "online").trim().toLowerCase();
    if (connectivity === "offline") {
      return { connectivity, engine: "local", locked: true };
    }
    const configured = String(env.VOICE_ENGINE || "classic").trim().toLowerCase();
    const engine = configured === "openai-realtime" ? "openai-realtime" : "classic";
    return { connectivity: "online", engine, locked: false };
  }

  function canonicalPolicies(snapshot) {
    const servers = findMcpServers(snapshot);
    if (!servers) return {};
    const result = {};
    for (const [name, raw] of Object.entries(servers)) {
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;
      const native = raw.native && typeof raw.native === "object" ? raw.native : {};
      const realtime = raw.realtime && typeof raw.realtime === "object" ? raw.realtime : {};
      const permissions = realtime.permissions && typeof realtime.permissions === "object" ? realtime.permissions : {};
      const legacyPermission = typeof realtime.permission === "string" ? realtime.permission : "";
      const permission = String(permissions.mode || legacyPermission || "open").toLowerCase();
      result[name] = {
        native_url: String(native.url || ""),
        realtime_transport: String(realtime.transport || "stdio").toLowerCase(),
        permission_mode: permission === "approval" ? "approval" : "open"
      };
    }
    return result;
  }

  function findCard(serverName) {
    const inputs = [...document.querySelectorAll(".mcp-routing-input[data-server-name], .mcp-options-input[data-server-name]")];
    const input = inputs.find((item) => item.dataset.serverName === serverName);
    return input ? input.closest(".mcp-server-card") : null;
  }

  function makeField(labelText, control) {
    const field = document.createElement("label");
    field.className = "field rv2d-mcp-realtime-field";
    const label = document.createElement("span");
    label.textContent = labelText;
    field.append(label, control);
    return field;
  }

  function setMessage(section, text, isError = false) {
    const message = section.querySelector(".rv2d-mcp-realtime-message");
    if (!message) return;
    message.textContent = text;
    message.style.color = isError ? "var(--bad, #b3261e)" : "";
  }

  function setVoiceMessage(section, text, isError = false) {
    const message = section.querySelector(".rv2d-voice-engine-message");
    if (!message) return;
    message.textContent = text;
    message.style.color = isError ? "var(--bad, #b3261e)" : "";
  }

  async function saveVoiceEngine(section) {
    const select = section.querySelector(".rv2d-voice-engine-select");
    const button = section.querySelector(".rv2d-voice-engine-save");
    if (!select || !button || button.disabled) return;
    button.disabled = true;
    setVoiceMessage(section, "Saving...");
    try {
      const response = await fetch(VOICE_ENGINE_SAVE_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice_engine: select.value })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) {
        throw new Error(data?.error?.message || data?.message || response.statusText || `HTTP ${response.status}`);
      }
      select.value = data.voice_engine || select.value;
      setVoiceMessage(section, "Saved. Restart required.");
      lastVoiceEngineSignature = "";
    } catch (error) {
      setVoiceMessage(section, `Save failed: ${error.message || error}`, true);
    } finally {
      button.disabled = false;
    }
  }

  function buildVoiceEngineSection(state) {
    const section = document.createElement("div");
    section.className = "rv2d-voice-engine";
    section.style.cssText = "margin-top:12px;padding-top:12px;border-top:1px solid var(--border,#d7dde5);display:grid;gap:8px;";

    const title = document.createElement("strong");
    title.textContent = "Voice / AI engine";

    const select = document.createElement("select");
    select.className = "input rv2d-voice-engine-select";
    if (state.locked) {
      select.append(option("Local", "local", "local"));
      select.disabled = true;
      select.title = "Offline mode is always fully local.";
    } else {
      select.append(
        option("Classic", "classic", state.engine),
        option("OpenAI Realtime", "openai-realtime", state.engine)
      );
    }

    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:8px;align-items:center;flex-wrap:wrap;";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "small-button rv2d-voice-engine-save";
    save.textContent = "Save engine";
    save.disabled = state.locked;
    save.addEventListener("click", () => saveVoiceEngine(section));
    const message = document.createElement("span");
    message.className = "rv2d-voice-engine-message";
    message.style.cssText = "font-size:12px;opacity:.8;";
    message.textContent = state.locked
      ? "Offline profile: local engine only."
      : "Applies after livestageassistant restart.";
    actions.append(save, message);

    section.append(title, makeField("Engine", select), actions);
    return section;
  }

  function reconcileVoiceEngine(state) {
    const connectivity = document.querySelector("#connectivity-mode");
    const host = connectivity?.closest(".field") || connectivity?.parentElement;
    if (!host) return;
    let section = host.querySelector(".rv2d-voice-engine");
    if (!section) {
      section = buildVoiceEngineSection(state);
      host.appendChild(section);
      return;
    }
    const select = section.querySelector(".rv2d-voice-engine-select");
    const save = section.querySelector(".rv2d-voice-engine-save");
    if (!select || !save) return;
    const needsLocked = state.locked;
    const isLocked = select.disabled;
    if (needsLocked !== isLocked) {
      section.replaceWith(buildVoiceEngineSection(state));
      return;
    }
    if (document.activeElement !== select) select.value = state.engine;
  }

  async function saveSection(section) {
    if (section.dataset.saving === "1") return;
    const server = section.dataset.server || "";
    const transport = section.querySelector(".rv2d-mcp-transport")?.value || "stdio";
    const permission = section.querySelector(".rv2d-mcp-permission")?.value || "open";
    const nativeUrl = section.querySelector(".rv2d-mcp-native-url")?.value.trim() || "";
    const button = section.querySelector(".rv2d-mcp-save");
    section.dataset.saving = "1";
    if (button) button.disabled = true;
    setMessage(section, "Saving...");
    try {
      const response = await fetch(SAVE_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          server,
          policy: {
            realtime_transport: transport,
            permission_mode: permission,
            native_url: nativeUrl
          }
        })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) throw new Error(data?.error?.message || data?.message || response.statusText || `HTTP ${response.status}`);
      const policy = data.policy || {};
      section.querySelector(".rv2d-mcp-transport").value = policy.realtime_transport || transport;
      section.querySelector(".rv2d-mcp-permission").value = policy.permission_mode || permission;
      section.querySelector(".rv2d-mcp-native-url").value = policy.native_url ?? nativeUrl;
      setMessage(section, "Saved");
      lastSignature = "";
    } catch (error) {
      setMessage(section, `Save failed: ${error.message || error}`, true);
    } finally {
      section.dataset.saving = "0";
      if (button) button.disabled = false;
    }
  }

  function buildSection(serverName, policy) {
    const section = document.createElement("section");
    section.className = "rv2d-mcp-realtime";
    section.dataset.server = serverName;
    section.style.cssText = "margin-top:12px;padding:12px 0;border-top:1px solid var(--border,#d7dde5);display:grid;gap:10px;";

    const title = document.createElement("strong");
    title.textContent = "Realtime MCP";

    const transport = document.createElement("select");
    transport.className = "input rv2d-mcp-transport";
    transport.append(option("Auto", "auto", policy.realtime_transport), option("Native", "native", policy.realtime_transport), option("STDIO", "stdio", policy.realtime_transport));

    const permission = document.createElement("select");
    permission.className = "input rv2d-mcp-permission";
    permission.append(option("Open", "open", policy.permission_mode), option("Require approval", "approval", policy.permission_mode));

    const nativeUrl = document.createElement("input");
    nativeUrl.type = "url";
    nativeUrl.className = "input rv2d-mcp-native-url";
    nativeUrl.placeholder = "https://…/mcp";
    nativeUrl.value = policy.native_url || "";
    nativeUrl.autocomplete = "off";

    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:8px;align-items:center;flex-wrap:wrap;";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "small-button rv2d-mcp-save";
    save.textContent = "Save realtime";
    save.addEventListener("click", () => saveSection(section));
    const message = document.createElement("span");
    message.className = "rv2d-mcp-realtime-message";
    message.style.cssText = "font-size:12px;opacity:.8;";
    actions.append(save, message);

    section.append(title, makeField("Transport", transport), makeField("Permission", permission), makeField("Native HTTPS URL", nativeUrl), actions);
    return section;
  }

  function reconcile(policies) {
    for (const [name, policy] of Object.entries(policies)) {
      const card = findCard(name);
      if (!card) continue;
      let section = [...card.querySelectorAll(".rv2d-mcp-realtime")].find((item) => item.dataset.server === name);
      if (!section) {
        section = buildSection(name, policy);
        const anchor = card.querySelector(".mcp-routing-box");
        if (anchor) card.insertBefore(section, anchor);
        else card.appendChild(section);
        continue;
      }
      if (section.dataset.saving === "1") continue;
      const transport = section.querySelector(".rv2d-mcp-transport");
      const permission = section.querySelector(".rv2d-mcp-permission");
      const nativeUrl = section.querySelector(".rv2d-mcp-native-url");
      if (transport && document.activeElement !== transport) transport.value = policy.realtime_transport;
      if (permission && document.activeElement !== permission) permission.value = policy.permission_mode;
      if (nativeUrl && document.activeElement !== nativeUrl) nativeUrl.value = policy.native_url;
    }
  }

  async function refreshPolicies() {
    if (busy) return;
    busy = true;
    try {
      const response = await fetch(SNAPSHOT_URL, { cache: "no-store" });
      if (!response.ok) return;
      const snapshot = await response.json();
      const policies = canonicalPolicies(snapshot);
      const state = voiceEngineState(snapshot);
      const signature = JSON.stringify(policies);
      const voiceSignature = JSON.stringify(state);
      if (voiceSignature !== lastVoiceEngineSignature || !document.querySelector(".rv2d-voice-engine")) {
        reconcileVoiceEngine(state);
        lastVoiceEngineSignature = voiceSignature;
      }
      if (signature !== lastSignature || document.querySelectorAll(".rv2d-mcp-realtime").length < Object.keys(policies).length) {
        reconcile(policies);
        lastSignature = signature;
      }
    } catch (_error) {
    } finally {
      busy = false;
    }
  }

  const observer = new MutationObserver(() => {
    if (!busy) window.setTimeout(refreshPolicies, 0);
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  refreshPolicies();
  window.setInterval(refreshPolicies, POLL_MS);
})();
