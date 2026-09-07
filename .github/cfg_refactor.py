from pathlib import Path
import re


def one(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {n}")
    return text.replace(old, new, 1)


# index.html: markup only. No second CFG controller and no second Save button.
p = Path("assets/web/index.html")
html = p.read_text(encoding="utf-8")
html, n = re.subn(
    r'<div class="field full-row"><button class="small-button" id="voice-config-save" type="button">Save voice settings</button><span class="detail" id="voice-config-message"></span></div>',
    "",
    html,
    count=1,
)
if n != 1:
    raise RuntimeError(f"index secondary save: {n}")
html, n = re.subn(
    r'\n  <script>\s*\(\(\) => \{\s*"use strict";.*?\}\)\(\);\s*</script>(?=\s*</body>)',
    "",
    html,
    count=1,
    flags=re.S,
)
if n != 1:
    raise RuntimeError(f"index inline CFG controller: {n}")
p.write_text(html, encoding="utf-8")


# app.js: one canonical GUI/config controller.
p = Path("assets/web/app.js")
js = p.read_text(encoding="utf-8")
js = one(
    js,
    '    const llmSave = document.querySelector("#llm-save");\n    const llmMessage = document.querySelector("#llm-message");',
    '''    const llmSave = document.querySelector("#llm-save");
    const llmMessage = document.querySelector("#llm-message");
    const panelConfig = document.querySelector("#panel-config");
    const voiceEngine = document.querySelector("#voice-engine");
    const realtimeModel = document.querySelector("#realtime-model");
    const realtimeModelField = document.querySelector("#realtime-model-field");
    const realtimeVoice = document.querySelector("#realtime-voice");
    const realtimeVoiceField = document.querySelector("#realtime-voice-field");
    const speechOutputGain = document.querySelector("#speech-output-gain");
    const speechOutputGainField = document.querySelector("#speech-output-gain-field");
    const speechOutputGainLabel = document.querySelector("#speech-output-gain-label");
    const speechOutputGainHint = document.querySelector("#speech-output-gain-hint");
    const classicSttPromptField = document.querySelector("#classic-stt-prompt-field");
    const classicInterruptField = document.querySelector("#classic-interrupt-field");
    const classicVadDetails = document.querySelector("#classic-vad-details");
    const llmProviderField = document.querySelector("#llm-provider-field");
    const llmModelField = document.querySelector("#llm-model-field");
    const sttInputField = document.querySelector("#stt-input-field");
    const mcpDetails = document.querySelector("#mcp-servers-details");''',
    "app DOM refs",
)
js = one(
    js,
    '    let configBaseline = "";',
    '''    let configBaseline = "";
    let restartRequired = false;
    let runtimeRestarting = false;
    let restartLoadingSeen = false;
    let currentCloudGain = 1;
    let currentLocalGain = 1;
    let currentClassicCloudSpeech = true;
    let lastSnapshot = null;''',
    "app config state",
)
js = one(
    js,
    '''        env_profile: activeEnvProfile,
        connectivity_mode: selectedConnectivityMode(),
        provider: llmProvider.value || "",''',
    '''        env_profile: activeEnvProfile,
        connectivity_mode: selectedConnectivityMode(),
        voice_engine: voiceEngine?.value || "classic",
        realtime_model: realtimeModel?.value.trim() || "",
        realtime_voice: realtimeVoice?.value.trim() || "",
        speech_output_gain: Number(speechOutputGain?.value || 1),
        provider: llmProvider.value || "",''',
    "app config signature",
)

marker = '''    function hasUnsavedConfigChanges() {
      return Boolean(configBaseline) && configSignature() !== configBaseline;
    }
'''
helpers = r'''
    function syncSpeechOutputGainLabel() {
      if (!speechOutputGain) return;
      const offline = selectedConnectivityMode() === "offline";
      const locality = offline ? "Local" : "Cloud";
      speechOutputGainLabel.textContent = `${locality} · ${Number(speechOutputGain.value || 1).toFixed(2)}×`;
      speechOutputGainHint.textContent = offline
        ? "Gain applied to fully local speech output."
        : "Gain applied to cloud-generated speech, including Realtime.";
    }

    function syncVoiceEngineControls() {
      if (!voiceEngine) return;
      const offline = selectedConnectivityMode() === "offline";
      if (offline) voiceEngine.value = "local";
      const realtime = !offline && voiceEngine.value === "openai-realtime";
      voiceEngine.disabled = offline;
      for (const item of voiceEngine.options) item.disabled = offline ? item.value !== "local" : item.value === "local";
      realtimeModelField.classList.toggle("hidden", !realtime);
      realtimeVoiceField.classList.toggle("hidden", !realtime);
      llmProviderField.classList.toggle("hidden", realtime);
      llmModelField.classList.toggle("hidden", realtime);
      classicSttPromptField.classList.toggle("hidden", realtime);
      classicInterruptField.classList.toggle("hidden", realtime);
      sttInputField.classList.toggle("hidden", realtime);
      classicVadDetails.classList.toggle("hidden", realtime);
      for (const element of cloudAudioControls) element.classList.toggle("hidden", realtime || offline);
      for (const field of [ttsSpeedField, elevenlabsVoiceField, openaiTtsVoiceField, ttsTestField]) field.classList.toggle("hidden", realtime);
      webTtsVolumeField.classList.add("hidden");
      backendTtsVolumeField.classList.add("hidden");
      currentClassicCloudSpeech = !offline && ["openai", "elevenlabs"].includes(String(cloudTtsProvider.value || "").toLowerCase());
      speechOutputGainField.classList.toggle("hidden", !(realtime || offline || currentClassicCloudSpeech));
      syncSpeechOutputGainLabel();
    }

    function syncConfigActionState() {
      if (runtimeRestarting) {
        llmSave.textContent = "Restarting…";
        llmSave.disabled = true;
        return;
      }
      const dirty = hasUnsavedConfigChanges();
      llmSave.disabled = false;
      if (dirty && restartRequired) llmSave.textContent = "Save + Restart";
      else if (!dirty && restartRequired) llmSave.textContent = "Restart";
      else llmSave.textContent = "Save";
    }

    function setRestartRequired(value, message = "") {
      restartRequired = restartRequired || Boolean(value);
      if (restartRequired && message) llmMessage.textContent = message;
      syncConfigActionState();
    }

    async function requestRuntimeRestart() {
      if (runtimeRestarting) return;
      runtimeRestarting = true;
      restartLoadingSeen = false;
      llmMessage.textContent = "Restarting…";
      syncConfigActionState();
      try {
        const response = await fetch("/api/runtime-restart", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}"
        });
        await fetchJsonOrThrow(response);
      } catch (error) {
        runtimeRestarting = false;
        llmMessage.textContent = `Restart failed: ${error.message || error}`;
        syncConfigActionState();
      }
    }

    function findMcpConfigServers(value, depth = 0, seen = new Set()) {
      if (!value || typeof value !== "object" || depth > 6 || seen.has(value)) return null;
      seen.add(value);
      if (value.mcpServers && typeof value.mcpServers === "object" && !Array.isArray(value.mcpServers)) return value.mcpServers;
      for (const child of Object.values(value)) {
        const found = findMcpConfigServers(child, depth + 1, seen);
        if (found) return found;
      }
      return null;
    }

    function mcpPolicyMap(snapshot) {
      const servers = findMcpConfigServers(snapshot) || {};
      const result = {};
      for (const [name, raw] of Object.entries(servers)) {
        if (!raw || typeof raw !== "object") continue;
        const native = raw.native && typeof raw.native === "object" ? raw.native : {};
        const realtime = raw.realtime && typeof raw.realtime === "object" ? raw.realtime : {};
        const permissions = realtime.permissions && typeof realtime.permissions === "object" ? realtime.permissions : {};
        result[name] = {
          transport: String(realtime.transport || "auto").toLowerCase(),
          permission: String(permissions.mode || realtime.permission || "open").toLowerCase() === "approval" ? "approval" : "open",
          httpsUrl: String(native.url || ""),
          command: String(raw.command || ""),
          args: Array.isArray(raw.args) ? raw.args.map(String) : [],
          localUrl: String(raw.url || "")
        };
      }
      return result;
    }

    function runtimeMcpStatus(snapshot, name) {
      const items = snapshot?.runtime_status?.mcp;
      return Array.isArray(items) ? items.find((item) => String(item?.name || "") === name) || null : null;
    }

    function displayMcpTransport(value) {
      const normalized = String(value || "").toLowerCase();
      return normalized === "native" ? "HTTPS" : normalized.toUpperCase();
    }

    function makeCfgField(label, control) {
      const field = document.createElement("label");
      field.className = "field";
      const text = document.createElement("span");
      text.textContent = label;
      field.append(text, control);
      return field;
    }

    function makeMcpPolicyControls(policy) {
      const transport = document.createElement("select");
      transport.append(
        option("Auto", "auto", false, policy.transport === "auto"),
        option("HTTPS", "native", false, policy.transport === "native"),
        option("STDIO", "stdio", false, policy.transport === "stdio")
      );
      const permission = document.createElement("select");
      permission.append(
        option("Open", "open", false, policy.permission === "open"),
        option("Require approval", "approval", false, policy.permission === "approval")
      );
      const https = document.createElement("input");
      https.type = "url";
      https.placeholder = "https://…/mcp";
      https.value = policy.httpsUrl || "";
      const httpsField = makeCfgField("Provider HTTPS URL", https);
      const sync = () => {
        httpsField.classList.toggle("hidden", transport.value === "stdio");
        const approval = [...permission.options].find((item) => item.value === "approval");
        if (approval) approval.disabled = transport.value === "stdio";
        if (transport.value === "stdio" && permission.value === "approval") permission.value = "open";
      };
      transport.addEventListener("change", sync);
      sync();
      return { transport, permission, https, httpsField };
    }

    async function saveMcpDefinition(action, body) {
      const response = await fetch("/api/mcp-server", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, ...body })
      });
      return fetchJsonOrThrow(response);
    }

    function ensureAddMcpControl() {
      const toolbar = mcpDetails?.querySelector(".mcp-server-toolbar");
      if (!toolbar || document.querySelector("#cfg-add-mcp")) return;
      const add = document.createElement("button");
      add.id = "cfg-add-mcp";
      add.type = "button";
      add.className = "small-button";
      add.textContent = "+ Add MCP";
      const form = document.createElement("div");
      form.className = "hidden";
      form.style.cssText = "display:grid;gap:8px;margin-top:10px;padding:10px;border:1px solid var(--border,#d7dde5);border-radius:8px";
      const name = document.createElement("input"); name.placeholder = "name";
      const command = document.createElement("input"); command.placeholder = "STDIO command (optional)";
      const args = document.createElement("input"); args.placeholder = '["arg1","arg2"]';
      const localUrl = document.createElement("input"); localUrl.placeholder = "Local MCP URL http://… (optional)";
      const controls = makeMcpPolicyControls({ transport: "auto", permission: "open", httpsUrl: "" });
      const create = document.createElement("button"); create.type = "button"; create.className = "small-button"; create.textContent = "Create MCP";
      const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "small-button"; cancel.textContent = "Cancel";
      const message = document.createElement("span"); message.className = "detail";
      form.append(makeCfgField("Name", name), makeCfgField("STDIO command", command), makeCfgField("STDIO args (JSON)", args), makeCfgField("Local MCP URL", localUrl), makeCfgField("Transport", controls.transport), controls.httpsField, makeCfgField("Permission", controls.permission), create, cancel, message);
      toolbar.append(add, form);
      add.addEventListener("click", () => form.classList.remove("hidden"));
      cancel.addEventListener("click", () => form.classList.add("hidden"));
      create.addEventListener("click", async () => {
        create.disabled = true;
        message.textContent = "Creating…";
        try {
          const data = await saveMcpDefinition("create", { server: {
            name: name.value.trim(), command: command.value.trim(), args: args.value.trim() || "[]", local_url: localUrl.value.trim(),
            realtime_transport: controls.transport.value === "native" ? "https" : controls.transport.value,
            https_url: controls.https.value.trim(), permission_mode: controls.permission.value
          }});
          setRestartRequired(data.restart_required, "MCP created · restart required.");
          form.classList.add("hidden");
          mcpServersSignature = "";
          await refresh();
        } catch (error) {
          message.textContent = `Create failed: ${error.message || error}`;
        } finally { create.disabled = false; }
      });
    }

    function renderMcpCrud(snapshot) {
      ensureAddMcpControl();
      for (const old of document.querySelectorAll(".cfg-mcp-editor")) old.remove();
      const policies = mcpPolicyMap(snapshot);
      for (const card of document.querySelectorAll(".mcp-server-card")) {
        const name = card.querySelector(".mcp-server-name")?.textContent?.trim() || "";
        const policy = policies[name];
        if (!name || !policy) continue;
        const section = document.createElement("div");
        section.className = "cfg-mcp-editor";
        section.style.cssText = "margin-top:10px;padding-top:10px;border-top:1px solid var(--border,#d7dde5);display:grid;gap:8px";
        const runtime = runtimeMcpStatus(snapshot, name);
        if (runtime) {
          const status = document.createElement("div");
          status.className = "detail";
          const configured = displayMcpTransport(runtime.configured_transport || policy.transport);
          const effective = displayMcpTransport(runtime.effective_transport || "");
          status.textContent = effective ? `Configured ${configured} · Effective ${effective}${runtime.healthy === true ? " · healthy" : runtime.healthy === false ? " · unavailable" : ""}` : `Configured ${configured}`;
          section.append(status);
        }
        const command = document.createElement("input"); command.value = policy.command; command.placeholder = "STDIO command";
        const args = document.createElement("input"); args.value = JSON.stringify(policy.args); args.placeholder = '["arg1"]';
        const localUrl = document.createElement("input"); localUrl.value = policy.localUrl; localUrl.placeholder = "Local MCP URL";
        const controls = makeMcpPolicyControls(policy);
        const save = document.createElement("button"); save.type = "button"; save.className = "small-button"; save.textContent = "Save MCP";
        const del = document.createElement("button"); del.type = "button"; del.className = "small-button"; del.textContent = "Delete MCP";
        const message = document.createElement("span"); message.className = "detail";
        save.addEventListener("click", async () => {
          save.disabled = true; message.textContent = "Saving…";
          try {
            const data = await saveMcpDefinition("update", { existing_name: name, server: {
              name, command: command.value.trim(), args: args.value.trim() || "[]", local_url: localUrl.value.trim(),
              realtime_transport: controls.transport.value === "native" ? "https" : controls.transport.value,
              https_url: controls.https.value.trim(), permission_mode: controls.permission.value
            }});
            setRestartRequired(data.restart_required, "MCP saved · restart required.");
            message.textContent = "Saved · restart required.";
          } catch (error) { message.textContent = `Save failed: ${error.message || error}`; }
          finally { save.disabled = false; }
        });
        del.addEventListener("click", async () => {
          if (!window.confirm(`Delete MCP "${name}"?`)) return;
          del.disabled = true;
          try {
            const data = await saveMcpDefinition("delete", { server: name });
            setRestartRequired(data.restart_required, "MCP deleted · restart required.");
            mcpServersSignature = "";
            await refresh();
          } catch (error) { message.textContent = `Delete failed: ${error.message || error}`; }
          finally { del.disabled = false; }
        });
        section.append(makeCfgField("STDIO command", command), makeCfgField("STDIO args (JSON)", args), makeCfgField("Local MCP URL", localUrl), makeCfgField("Transport", controls.transport), controls.httpsField, makeCfgField("Permission", controls.permission), save, del, message);
        card.append(section);
      }
    }
'''
js = one(js, marker, marker + helpers, "app helpers")

js = one(
    js,
    '        const selectedProvider = data.provider || provider || "";\n        setSelectedConnectivityMode(connectivityOverride || data.selected_connectivity_mode || "online");',
    '''        const selectedProvider = data.provider || provider || "";
        setSelectedConnectivityMode(connectivityOverride || data.selected_connectivity_mode || "online");
        voiceEngine.value = data.selected_voice_engine || (selectedConnectivityMode() === "offline" ? "local" : "classic");
        realtimeModel.value = data.selected_realtime_model || "gpt-realtime-2.1";
        realtimeVoice.value = data.selected_realtime_voice || "marin";
        currentCloudGain = Number(data.selected_cloud_tts_output_gain ?? 1);
        currentLocalGain = Number(data.selected_local_tts_output_gain ?? 1);
        speechOutputGain.value = String(selectedConnectivityMode() === "offline" ? currentLocalGain : currentCloudGain);''',
    "app load CFG values",
)
js = one(js, '        llmMessage.textContent = data.message || "";\n        if (shouldMarkClean) {\n          markConfigClean();\n        }', '        syncVoiceEngineControls();\n        llmMessage.textContent = data.message || "";\n        if (shouldMarkClean) {\n          markConfigClean();\n          syncConfigActionState();\n        }', "app load completion")

# Existing MCP editors participate in the same restart-required state and never show the runtime-loading overlay on Save.
js = js.replace('''        if (messageEl) messageEl.textContent = data.message || "Routing saved.";
        setEnvironmentLoading(true);
        mcpServersSignature = "";
        await refresh();''', '''        if (messageEl) messageEl.textContent = data.message || "Routing saved.";
        setRestartRequired(data.restart_required, "Routing saved · restart required.");
        mcpServersSignature = "";
        await refresh();''')
js = js.replace('''        if (messageEl) messageEl.textContent = data.message || "MCP server options saved.";
        setEnvironmentLoading(true);
        mcpServersSignature = "";
        await refresh();''', '''        if (messageEl) messageEl.textContent = data.message || "MCP server options saved.";
        setRestartRequired(data.restart_required, "MCP options saved · restart required.");
        mcpServersSignature = "";
        await refresh();''')

js = one(js, '        const data = await response.json();\n        const snapshotEnv = (data.config && data.config.env) || {};', '        const data = await response.json();\n        lastSnapshot = data;\n        const snapshotEnv = (data.config && data.config.env) || {};', "app snapshot state")
js = one(js, '        renderMcpServers(data.mcp_servers || []);\n        syncMcpRoutingEditors();', '        renderMcpServers(data.mcp_servers || []);\n        syncMcpRoutingEditors();\n        window.setTimeout(() => renderMcpCrud(data), 0);', "app MCP CRUD render")
restart_marker = '        const envProfileChanged = await loadEnvProfiles();'
restart_logic = '''        if (runtimeRestarting) {
          if (environmentLoading.active) restartLoadingSeen = true;
          if (restartLoadingSeen && !environmentLoading.active && data.runtime_status?.ready) {
            runtimeRestarting = false;
            restartRequired = false;
            restartLoadingSeen = false;
            llmMessage.textContent = "Restart complete.";
            llmControlsInitialized = false;
            configBaseline = configSignature();
            syncConfigActionState();
          }
        }
'''
js = one(js, restart_marker, restart_logic + restart_marker, "app restart completion")

listener_marker = '    cloudApiRefresh.addEventListener("click", () => loadCloudApiStatus(true));\n\n    llmSave.addEventListener("click", async () => {'
listener_block = '''    cloudApiRefresh.addEventListener("click", () => loadCloudApiStatus(true));
    voiceEngine.addEventListener("change", () => { syncVoiceEngineControls(); syncConfigActionState(); });
    realtimeModel.addEventListener("input", syncConfigActionState);
    realtimeVoice.addEventListener("input", syncConfigActionState);
    speechOutputGain.addEventListener("input", () => { syncSpeechOutputGainLabel(); syncConfigActionState(); });
    cloudTtsProvider.addEventListener("change", syncVoiceEngineControls);
    panelConfig.addEventListener("input", syncConfigActionState);
    panelConfig.addEventListener("change", syncConfigActionState);
    mcpDetails.addEventListener("toggle", () => { if (mcpDetails.open && lastSnapshot) window.setTimeout(() => renderMcpCrud(lastSnapshot), 0); });

    llmSave.addEventListener("click", async () => {'''
js = one(js, listener_marker, listener_block, "app CFG listeners")
js = one(js, '    llmSave.addEventListener("click", async () => {\n      const provider = llmProvider.value;', '    llmSave.addEventListener("click", async () => {\n      if (!hasUnsavedConfigChanges() && restartRequired) { await requestRuntimeRestart(); return; }\n      const restartAfterSave = restartRequired;\n      const provider = llmProvider.value;', "app Save entry")
js = one(js, '      const speakerProfilesValue = collectSpeakerProfiles();\n      if (!provider) return;', '      const speakerProfilesValue = collectSpeakerProfiles();\n      const voiceEngineValue = voiceEngine.value || (connectivityModeValue === "offline" ? "local" : "classic");\n      const realtimeModelValue = realtimeModel.value.trim() || "gpt-realtime-2.1";\n      const realtimeVoiceValue = realtimeVoice.value.trim() || "marin";\n      const speechGainValue = Number(speechOutputGain.value || 1);\n      const cloudGainValue = connectivityModeValue === "offline" ? currentCloudGain : speechGainValue;\n      const localGainValue = connectivityModeValue === "offline" ? speechGainValue : currentLocalGain;\n      if (!provider) return;', "app Save CFG values")
js = one(js, '            speaker_profiles: speakerProfilesValue\n          })', '            speaker_profiles: speakerProfilesValue,\n            voice_engine: voiceEngineValue,\n            realtime_model: realtimeModelValue,\n            realtime_voice: realtimeVoiceValue,\n            cloud_tts_output_gain: cloudGainValue,\n            local_tts_output_gain: localGainValue\n          })', "app Save payload")

save_old = '''        llmMessage.textContent = data.message || tr("saved", "Saved.");
        cloudApiLoaded = false;
        setEnvironmentLoading(true);
        markConfigClean();
        llmControlsInitialized = false;
        if ((data.stt_language || sttLanguageValue) !== i18nPayload.locale) {
          window.setTimeout(() => window.location.reload(), 250);
          return;
        }
        await refresh();'''
save_new = '''        llmMessage.textContent = data.message || tr("saved", "Saved.");
        cloudApiLoaded = false;
        currentCloudGain = cloudGainValue;
        currentLocalGain = localGainValue;
        markConfigClean();
        restartRequired = restartRequired || Boolean(data.restart_required);
        syncConfigActionState();
        if ((data.stt_language || sttLanguageValue) !== i18nPayload.locale) {
          window.setTimeout(() => window.location.reload(), 250);
          return;
        }
        if (restartAfterSave) await requestRuntimeRestart();'''
js = one(js, save_old, save_new, "app Save completion")
js = one(js, '''      } catch (error) {
        setEnvironmentLoading(false);
        llmMessage.textContent = trf("save_failed", "Save failed: {error}", { error });
      } finally {
        llmSave.disabled = !llmProvider.value;
      }
    });''', '''      } catch (error) {
        llmMessage.textContent = trf("save_failed", "Save failed: {error}", { error });
      } finally {
        if (!runtimeRestarting) syncConfigActionState();
      }
    });''', "app Save finalizer")
p.write_text(js, encoding="utf-8")


# web_monitor_base.py: /api/llm-config owns all voice configuration fields.
p = Path("voice_assistant/web_monitor_base.py")
base = p.read_text(encoding="utf-8")
base = one(base, '                    speaker_profiles = payload.get("speaker_profiles") or []\n                    if not isinstance(speaker_profiles, list):', '''                    speaker_profiles = payload.get("speaker_profiles") or []
                    voice_engine = str(payload.get("voice_engine") or "").strip().lower()
                    realtime_model = str(payload.get("realtime_model") or "").strip()
                    realtime_voice = str(payload.get("realtime_voice") or "").strip()
                    try:
                        cloud_tts_output_gain = float(payload.get("cloud_tts_output_gain") if payload.get("cloud_tts_output_gain") is not None else 1.0)
                        local_tts_output_gain = float(payload.get("local_tts_output_gain") if payload.get("local_tts_output_gain") is not None else 1.0)
                    except (TypeError, ValueError):
                        self.send_error(400, "Speech output gains must be numbers")
                        return
                    if not isinstance(speaker_profiles, list):''', "base parse CFG")
base = one(base, '''                            speaker_margin,
                            speaker_profiles,
                        )''', '''                            speaker_margin,
                            speaker_profiles,
                            voice_engine,
                            realtime_model,
                            realtime_voice,
                            cloud_tts_output_gain,
                            local_tts_output_gain,
                        )''', "base call CFG")
p.write_text(base, encoding="utf-8")


# runtime_web_services.py: one atomic save, no implicit restart.
p = Path("voice_assistant/runtime_web_services.py")
svc = p.read_text(encoding="utf-8")
svc = one(svc, '            "selected_command_ack_sound_file": command_ack,\n            "message": f"Common runtime options loaded from active profile: {self.active_profile()}",', '''            "selected_command_ack_sound_file": command_ack,
            "selected_voice_engine": str(values.get("VOICE_ENGINE") or ("local" if connectivity == "offline" else "classic")).strip().lower(),
            "selected_realtime_model": str(values.get("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip(),
            "selected_realtime_voice": str(values.get("OPENAI_REALTIME_VOICE") or "marin").strip(),
            "selected_cloud_tts_output_gain": self._float(values, "CLOUD_TTS_OUTPUT_GAIN", 1.0),
            "selected_local_tts_output_gain": self._float(values, "LOCAL_TTS_OUTPUT_GAIN", 1.0),
            "message": f"Common runtime options loaded from active profile: {self.active_profile()}",''', "services options CFG")
svc = one(svc, '''        speaker_margin: float,
        speaker_profiles: list[dict[str, Any]],
    ) -> dict[str, Any]:''', '''        speaker_margin: float,
        speaker_profiles: list[dict[str, Any]],
        voice_engine: str,
        realtime_model: str,
        realtime_voice: str,
        cloud_tts_output_gain: float,
        local_tts_output_gain: float,
    ) -> dict[str, Any]:''', "services Save signature")
svc = one(svc, '        updates = {\n            "LLM_PROVIDER": provider,', '''        requested_engine = str(voice_engine or ("local" if active_connectivity == "offline" else "classic")).strip().lower()
        allowed_engines = {"local"} if active_connectivity == "offline" else {"classic", "openai-realtime"}
        if requested_engine not in allowed_engines:
            raise ValueError(f"voice_engine must be one of: {', '.join(sorted(allowed_engines))}")
        cloud_tts_output_gain = max(0.0, min(2.0, float(cloud_tts_output_gain)))
        local_tts_output_gain = max(0.0, min(2.0, float(local_tts_output_gain)))
        if requested_engine == "openai-realtime":
            realtime_model = str(realtime_model or "gpt-realtime-2.1").strip()
            realtime_voice = str(realtime_voice or "marin").strip()
            if not realtime_model or not realtime_voice:
                raise ValueError("Realtime model and voice are required")

        updates = {
            "VOICE_ENGINE": requested_engine,
            "CLOUD_TTS_OUTPUT_GAIN": f"{cloud_tts_output_gain:.2f}",
            "LOCAL_TTS_OUTPUT_GAIN": f"{local_tts_output_gain:.2f}",
            "LLM_PROVIDER": provider,''', "services CFG updates")
svc = one(svc, '        if provider == "ollama":\n            updates["OLLAMA_MODEL"] = model', '        if requested_engine == "openai-realtime":\n            updates["OPENAI_REALTIME_MODEL"] = realtime_model\n            updates["OPENAI_REALTIME_VOICE"] = realtime_voice\n        if provider == "ollama":\n            updates["OLLAMA_MODEL"] = model', "services realtime updates")
svc = one(svc, '''        with self._lock:
            self._write_env(self.active_profile(), updates)
            self._refresh_monitor_config()
            self._reload()
        return {
            "saved": True,
            "provider": provider,
            "model": model,
            "connectivity_mode": active_connectivity,
            "restart_required": True,
            "message": "Configuration saved. Restart LiveStageAssistant to apply it.",
        }''', '''        with self._lock:
            profile = self.active_profile()
            self._write_env(profile, updates)
            self._refresh_monitor_config()
        return {
            "saved": True,
            "provider": provider,
            "model": model,
            "voice_engine": requested_engine,
            "connectivity_mode": active_connectivity,
            "profile": str(profile),
            "restart_required": True,
            "message": f"Saved to {profile} · restart required.",
        }''', "services no implicit restart")
svc = svc.replace('            self._refresh_monitor_config(mcp_config=config)\n            self._reload()\n        return {"ok": True, "mcp_config": str(path), "routing": normalized, "restart_required": True}', '            self._refresh_monitor_config(mcp_config=config)\n        return {"ok": True, "mcp_config": str(path), "routing": normalized, "restart_required": True}')
svc = svc.replace('            self._refresh_monitor_config(mcp_config=config)\n            self._reload()\n        return {"ok": True, "mcp_config": str(path), "options": normalized, "restart_required": True}', '            self._refresh_monitor_config(mcp_config=config)\n        return {"ok": True, "mcp_config": str(path), "options": normalized, "restart_required": True}')
p.write_text(svc, encoding="utf-8")


# web_monitor.py: delete separate voice persistence endpoints. Explicit runtime restart remains.
p = Path("voice_assistant/web_monitor.py")
wm = p.read_text(encoding="utf-8")
wm = re.sub(r'\nVOICE_ENGINE_ONLINE = .*?\nDEFAULT_REALTIME_VOICE = "marin"\n', '\nDEFAULT_RUNTIME_STATUS_FILE = "/tmp/livestageassistant-runtime-status.json"\n', wm, count=1, flags=re.S)
wm = re.sub(r'\n\ndef _auto_env_dir\(\).*?\n\ndef _runtime_status_file\(\)', '\n\ndef _runtime_status_file()', wm, count=1, flags=re.S)
wm = re.sub(r'\n\ndef _bounded_gain\(.*?\n\ndef _display_transport\(', '\n\ndef _display_transport(', wm, count=1, flags=re.S)
wm = re.sub(r'\n    def _save_voice_engine\(.*?\n    def _request_runtime_restart\(', '\n    def _request_runtime_restart(', wm, count=1, flags=re.S)
wm = wm.replace('{"/api/mcp-realtime-policy", "/api/mcp-server", "/api/voice-engine", "/api/voice-output-gains", "/api/runtime-restart"}', '{"/api/mcp-realtime-policy", "/api/mcp-server", "/api/runtime-restart"}')
wm = re.sub(r'\n                        if parsed\.path == "/api/voice-engine":.*?if parsed\.path == "/api/runtime-restart":', '\n                        if parsed.path == "/api/runtime-restart":', wm, count=1, flags=re.S)
wm = re.sub(r'\n                    def _handle_voice_engine_save\(self\).*?\n                    def _handle_runtime_restart\(self\)', '\n                    def _handle_runtime_restart(self)', wm, count=1, flags=re.S)
if any(token in wm for token in ("/api/voice-engine", "/api/voice-output-gains", "_save_voice_engine", "_save_voice_output_gains")):
    raise RuntimeError("dead voice persistence API remains")
p.write_text(wm, encoding="utf-8")


# Final structural checks.
assert "voice-config-save" not in Path("assets/web/index.html").read_text(encoding="utf-8")
assert Path("assets/web/index.html").read_text(encoding="utf-8").count('/assets/web/app.js') == 1
assert "/api/voice-engine" not in Path("assets/web/app.js").read_text(encoding="utf-8")
assert "/api/voice-output-gains" not in Path("assets/web/app.js").read_text(encoding="utf-8")
assert "Save + Restart" in Path("assets/web/app.js").read_text(encoding="utf-8")
