(() => {
  "use strict";

  const SNAPSHOT_URL = "/api/snapshot";
  const SAVE_URL = "/api/mcp-realtime-policy";
  const POLL_MS = 2000;
  let busy = false;
  let lastSignature = "";

  function option(label, value, selected) {
    const item = document.createElement("option");
    item.textContent = label;
    item.value = value;
    item.selected = value === selected;
    return item;
  }

  function canonicalPolicies(snapshot) {
    const servers = snapshot?.config?.mcp?.mcpServers;
    if (!servers || typeof servers !== "object" || Array.isArray(servers)) return {};
    const result = {};
    for (const [name, raw] of Object.entries(servers)) {
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;
      const native = raw.native && typeof raw.native === "object" ? raw.native : {};
      const realtime = raw.realtime && typeof raw.realtime === "object" ? raw.realtime : {};
      const permissions = realtime.permissions && typeof realtime.permissions === "object"
        ? realtime.permissions
        : {};
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
    const inputs = [...document.querySelectorAll(".mcp-routing-input[data-server], .mcp-server-options-input[data-server]")];
    const input = inputs.find((item) => item.dataset.server === serverName);
    if (!input) return null;
    return input.closest(".mcp-server-card")
      || input.closest(".service-card")
      || input.closest(".card")
      || input.parentElement?.parentElement
      || null;
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
      if (!response.ok || data.ok === false) {
        throw new Error(data?.error?.message || data?.message || response.statusText || `HTTP ${response.status}`);
      }
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
    section.style.cssText = "margin-top:12px;padding-top:12px;border-top:1px solid var(--border,#d7dde5);display:grid;gap:10px;";

    const title = document.createElement("strong");
    title.textContent = "Realtime MCP";

    const transport = document.createElement("select");
    transport.className = "input rv2d-mcp-transport";
    transport.append(
      option("Auto", "auto", policy.realtime_transport),
      option("Native", "native", policy.realtime_transport),
      option("STDIO", "stdio", policy.realtime_transport)
    );

    const permission = document.createElement("select");
    permission.className = "input rv2d-mcp-permission";
    permission.append(
      option("Open", "open", policy.permission_mode),
      option("Require approval", "approval", policy.permission_mode)
    );

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

    section.append(
      title,
      makeField("Transport", transport),
      makeField("Permission", permission),
      makeField("Native HTTPS URL", nativeUrl),
      actions
    );
    return section;
  }

  function reconcile(policies) {
    for (const [name, policy] of Object.entries(policies)) {
      const card = findCard(name);
      if (!card) continue;
      let section = card.querySelector(`.rv2d-mcp-realtime[data-server="${CSS.escape(name)}"]`);
      if (!section) {
        section = buildSection(name, policy);
        card.appendChild(section);
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
      const signature = JSON.stringify(policies);
      if (signature !== lastSignature || document.querySelectorAll(".rv2d-mcp-realtime").length < Object.keys(policies).length) {
        reconcile(policies);
        lastSignature = signature;
      }
    } catch (_error) {
      // The main UI already reports snapshot/network failures. Keep this add-on quiet.
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
