    const i18nPayload = window.LSA_I18N_PAYLOAD || { locale: "fr", messages: {}, available_locales: [] };
    const i18nMessages = i18nPayload.messages || {};
    function tr(key, fallback = "") {
      return Object.prototype.hasOwnProperty.call(i18nMessages, key) ? i18nMessages[key] : fallback;
    }
    function trf(key, fallback, values = {}) {
      return tr(key, fallback).replace(/\{([a-zA-Z0-9_]+)\}/g, (match, name) => (
        Object.prototype.hasOwnProperty.call(values, name) ? values[name] : match
      ));
    }
    function applyI18n(root = document) {
      for (const element of root.querySelectorAll("[data-i18n]")) {
        element.textContent = tr(element.dataset.i18n, element.textContent);
      }
      for (const element of root.querySelectorAll("[data-i18n-title]")) {
        element.title = tr(element.dataset.i18nTitle, element.title);
      }
      for (const element of root.querySelectorAll("[data-i18n-aria-label]")) {
        element.setAttribute("aria-label", tr(element.dataset.i18nAriaLabel, element.getAttribute("aria-label") || ""));
      }
      for (const element of root.querySelectorAll("[data-i18n-placeholder]")) {
        element.placeholder = tr(element.dataset.i18nPlaceholder, element.placeholder);
      }
    }

    const stateEl = document.querySelector("#state");
    const configEl = document.querySelector("#config");
    const logsEl = document.querySelector("#logs");
    const sttPromptEl = document.querySelector("#stt-prompt");
    const assistantSystemPromptEl = document.querySelector("#assistant-system-prompt");
    const promptEl = document.querySelector("#prompt");
    const metaEl = document.querySelector("#meta");
    const messagesEl = document.querySelector("#messages");
    const chatPanel = document.querySelector("#chat-panel");
    const vncUrl = document.querySelector("#vnc-url");
    const vncViewOnly = document.querySelector("#vnc-view-only");
    const vncConnect = document.querySelector("#vnc-connect");
    const vncFrame = document.querySelector("#vnc-frame");
    const vncStatus = document.querySelector("#vnc-status");
    const toastOverlay = document.querySelector("#toast-overlay");
    const toastPanel = document.querySelector("#toast-panel");
    const toastTitle = document.querySelector("#toast-title");
    const toastDetail = document.querySelector("#toast-detail");
    const toastOk = document.querySelector("#toast-ok");
    const sessionList = document.querySelector("#session-list");
    const sessionNew = document.querySelector("#session-new");
    const injectForm = document.querySelector("#inject-form");
    const commandField = document.querySelector("#command-field");
    const soundwave = document.querySelector("#soundwave");
    const injectCommand = document.querySelector("#inject-command");
    const composerAttach = document.querySelector("#composer-attach");
    const composerFile = document.querySelector("#composer-file");
    const injectStop = document.querySelector("#inject-stop");
    const webConversation = document.querySelector("#web-conversation");
    const webMic = document.querySelector("#web-mic");
    const composerSpeaker = document.querySelector("#composer-speaker");
    const settingsOpen = document.querySelector("#settings-open");
    const settingsClose = document.querySelector("#settings-close");
    const settingsOverlay = document.querySelector("#settings-overlay");
    const sessionLoading = document.querySelector("#session-loading");
    const sessionLoadingTitle = document.querySelector("#session-loading-title");
    const sessionLoadingDetail = document.querySelector("#session-loading-detail");
    const sessionSummaryPopover = document.querySelector("#session-summary-popover");
    const tabs = Array.from(document.querySelectorAll(".tab"));
    const panels = Array.from(document.querySelectorAll(".tab-panel"));
	    const llmProvider = document.querySelector("#llm-provider");
	    const llmModel = document.querySelector("#llm-model");
	    const sessionContextSize = document.querySelector("#session-context-size");
    const sessionContextSizeLabel = document.querySelector("#session-context-size-label");
    const mcpAgentMaxSteps = document.querySelector("#mcp-agent-max-steps");
    const mcpAgentMaxStepsLabel = document.querySelector("#mcp-agent-max-steps-label");
    const mcpToolRoutingInputs = Array.from(document.querySelectorAll('input[name="mcp-tool-routing"]'));
    const interruptConversationInputs = Array.from(document.querySelectorAll('input[name="interrupt-conversation"]'));
    const envProfile = document.querySelector("#env-profile");
    const connectivityAutoBadge = document.querySelector("#connectivity-auto-badge");
    const connectivityModeInputs = Array.from(document.querySelectorAll('input[name="connectivity-mode"]'));
    const cloudAudioControls = Array.from(document.querySelectorAll(".cloud-audio-control"));
    const offlineAudioSummary = document.querySelector("#offline-audio-summary");
	    const wakeWord = document.querySelector("#wake-word");
    const cloudTtsProvider = document.querySelector("#cloud-tts-provider");
    const ttsOutputInputs = Array.from(document.querySelectorAll('input[name="tts-output"]'));
    const sttInputInputs = Array.from(document.querySelectorAll('input[name="stt-input"]'));
    const elevenlabsVoiceField = document.querySelector("#elevenlabs-voice-field");
    const elevenlabsVoice = document.querySelector("#elevenlabs-voice");
    const openaiTtsVoiceField = document.querySelector("#openai-tts-voice-field");
    const openaiTtsVoice = document.querySelector("#openai-tts-voice");
    const ttsSpeedField = document.querySelector("#tts-speed-field");
    const openaiTtsSpeed = document.querySelector("#openai-tts-speed");
    const openaiTtsSpeedLabel = document.querySelector("#openai-tts-speed-label");
    const webTtsVolumeField = document.querySelector("#web-tts-volume-field");
    const webTtsVolume = document.querySelector("#web-tts-volume");
    const webTtsVolumeLabel = document.querySelector("#web-tts-volume-label");
    const backendTtsVolumeField = document.querySelector("#backend-tts-volume-field");
    const backendTtsVolume = document.querySelector("#backend-tts-volume");
    const backendTtsVolumeLabel = document.querySelector("#backend-tts-volume-label");
    const ttsTestField = document.querySelector("#tts-test-field");
    const ttsTest = document.querySelector("#tts-test");
    const vadSpeechThreshold = document.querySelector("#vad-speech-threshold");
    const vadSpeechThresholdLabel = document.querySelector("#vad-speech-threshold-label");
    const vadNegativeThreshold = document.querySelector("#vad-negative-threshold");
    const vadNegativeThresholdLabel = document.querySelector("#vad-negative-threshold-label");
    const vadMinSpeechMs = document.querySelector("#vad-min-speech-ms");
    const vadMinSpeechMsLabel = document.querySelector("#vad-min-speech-ms-label");
    const vadMinSilenceMs = document.querySelector("#vad-min-silence-ms");
    const vadMinSilenceMsLabel = document.querySelector("#vad-min-silence-ms-label");
    const vadSpeechPadMs = document.querySelector("#vad-speech-pad-ms");
    const vadSpeechPadMsLabel = document.querySelector("#vad-speech-pad-ms-label");
    const vadMaxSpeechSeconds = document.querySelector("#vad-max-speech-seconds");
    const vadMaxSpeechSecondsLabel = document.querySelector("#vad-max-speech-seconds-label");
    const vadPresetButtons = Array.from(document.querySelectorAll(".vad-preset"));
    const speakerRecognitionGroup = document.querySelector("#speaker-recognition-group");
    const speakerRecognitionInputs = Array.from(document.querySelectorAll('input[name="speaker-recognition"]'));
    const speakerBackend = document.querySelector("#speaker-backend");
    const speakerThreshold = document.querySelector("#speaker-threshold");
    const speakerThresholdLabel = document.querySelector("#speaker-threshold-label");
    const speakerMargin = document.querySelector("#speaker-margin");
    const speakerMarginLabel = document.querySelector("#speaker-margin-label");
    const speakerProfileGrid = document.querySelector("#speaker-profile-grid");
    const vadControls = [
      vadSpeechThreshold,
      vadNegativeThreshold,
      vadMinSpeechMs,
      vadMinSilenceMs,
      vadSpeechPadMs,
      vadMaxSpeechSeconds
    ];
    const cloudApiDetails = document.querySelector("#cloud-api-details");
    const cloudApiRefresh = document.querySelector("#cloud-api-refresh");
    const cloudApiGrid = document.querySelector("#cloud-api-grid");
    const mcpServerGrid = document.querySelector("#mcp-server-grid");
    const mcpAdminRouteInputs = Array.from(document.querySelectorAll('input[name="mcp-admin-route"]'));
    const browserAudioInputField = document.querySelector("#browser-audio-input-field");
    const browserAudioInput = document.querySelector("#browser-audio-input");
    const browserAudioTest = document.querySelector("#browser-audio-test");
    const browserAudioMeter = document.querySelector("#browser-audio-meter .vu-fill");
    const browserAudioOutputField = document.querySelector("#browser-audio-output-field");
    const browserAudioOutput = document.querySelector("#browser-audio-output");
    const browserAudioRefresh = document.querySelector("#browser-audio-refresh");
    const backendAudioInputField = document.querySelector("#backend-audio-input-field");
    const backendAudioInput = document.querySelector("#backend-audio-input");
    const backendAudioTest = document.querySelector("#backend-audio-test");
    const backendAudioMeter = document.querySelector("#backend-audio-meter .vu-fill");
    const backendAudioOutputField = document.querySelector("#backend-audio-output-field");
    const backendAudioOutput = document.querySelector("#backend-audio-output");
    const backendAudioMonitorModeField = document.querySelector("#backend-audio-monitor-mode-field");
    const backendAudioMonitorModeInputs = Array.from(document.querySelectorAll('input[name="backend-audio-monitor-mode"]'));
    const backendAudioMonitorVolumeField = document.querySelector("#backend-audio-monitor-volume-field");
    const backendAudioMonitorVolume = document.querySelector("#backend-audio-monitor-volume");
    const backendAudioMonitorVolumeLabel = document.querySelector("#backend-audio-monitor-volume-label");
    const backendAudioOutputPanField = document.querySelector("#backend-audio-output-pan-field");
    const backendAudioOutputPan = document.querySelector("#backend-audio-output-pan");
    const backendAudioOutputPanLabel = document.querySelector("#backend-audio-output-pan-label");
    const sttLanguage = document.querySelector("#stt-language");
    const thinkingSound = document.querySelector("#thinking-sound");
    const commandAckSoundField = document.querySelector("#command-ack-sound-field");
    const commandAckSound = document.querySelector("#command-ack-sound");
    const llmSave = document.querySelector("#llm-save");
    const llmMessage = document.querySelector("#llm-message");
    const ttsTestPhrase = "Bonjour je suis l'assistant vocal live stage assistant, comment puis-je vous aider";
    const speakerEmbeddingPreparationMessage = window.LSA_SPEAKER_EMBEDDING_PREPARATION_MESSAGE || "";
    const composerTextUploadMaxBytes = 64 * 1024;
    const composerAudioUploadMaxBytes = 20 * 1024 * 1024;
    applyI18n();
    const vadPresets = {
      "quick-word": {
        vadSpeechThreshold: 0.42,
        vadNegativeThreshold: 0.25,
        vadMinSpeechMs: 80,
        vadMinSilenceMs: 450,
        vadSpeechPadMs: 120,
        vadMaxSpeechSeconds: 6
      },
      "noise-filter": {
        vadSpeechThreshold: 0.62,
        vadNegativeThreshold: 0.42,
        vadMinSpeechMs: 180,
        vadMinSilenceMs: 700,
        vadSpeechPadMs: 100,
        vadMaxSpeechSeconds: 8
      },
      "slow-soft": {
        vadSpeechThreshold: 0.38,
        vadNegativeThreshold: 0.22,
        vadMinSpeechMs: 120,
        vadMinSilenceMs: 1600,
        vadSpeechPadMs: 180,
        vadMaxSpeechSeconds: 12
      }
    };
    let llmControlsInitialized = false;
    let llmOptionsLoading = false;
    let envProfilesLoading = false;
    let activeEnvProfile = "";
    let envProfileSwitchingEnabled = false;
    let connectivityLocked = false;
    let configBaseline = "";
    let speakerRecognitionUnavailableReason = "";
    let speakerRecognitionEnvEnabled = false;
    let speakerRecognitionRuntimeEnabled = false;
    let speakerProfileChoices = [];
    let environmentLoadingActive = false;
    let profileLoadingActive = false;
    let vncConnectTimer = null;
    let vncUrlDirty = false;
    let currentVncFrameUrl = "";
    let currentSnapshotEnvFile = "";
    let metaErrorUntil = 0;
    let lastServerMessages = [];
    let pendingMessages = [];
    let composerLocked = false;
    let cancelRequestInFlight = false;
    let interruptConversationEnabled = false;
    let openSessionMenuId = "";
    let openSessionSummaryId = "";
    let sessionSummaryPinned = false;
    let sessionSummaryHoverTimer = null;
    let sessionSummaryAnchor = null;
    let sessionSummaryHoverId = "";
    let sessionSummaryCache = new Map();
    let lastPointer = { x: -1, y: -1 };
    let webAudio = { enabled: false, stt_enabled: false, tts_enabled: false, tts_output: "silent" };
    let mediaRecorder = null;
    let mediaStream = null;
    let recordedChunks = [];
    let isRecording = false;
    let recordingTimer = null;
    let recordingAudioContext = null;
    let recordingAnalyser = null;
    let recordingVad = null;
    let recordingMonitorId = null;
    let recordingSpeechDetected = false;
    let recordingStartedAt = 0;
    let soundwaveAnimationId = null;
    let soundwaveStartedAt = 0;
    let conversationEnabled = false;
    let conversationRecorder = null;
    let conversationStream = null;
    let conversationAudioContext = null;
    let conversationAnalyser = null;
    let conversationVad = null;
    let conversationChunks = [];
    let conversationMonitorId = null;
    let conversationSpeechDetected = false;
    let conversationRestartTimer = null;
    let conversationDiscard = false;
    let conversationStopStreamAfterSegment = false;
    let lastSpokenAssistantMessageId = null;
    let messagesHydrated = false;
    const seenAssistantMessageIds = new Set();
    let webTtsPlaying = false;
    let webTtsAudioContext = null;
    let webTtsUnlocked = false;
    let pendingWebTtsMessage = null;
    let selectedBrowserAudioInput = window.localStorage.getItem("browser-audio-input") || "";
    let selectedBrowserAudioOutput = window.localStorage.getItem("browser-audio-output") || "";
    let backendAudioCapabilities = { input: false, output: false };
    let browserAudioCapabilities = { input: false, output: typeof Audio !== "undefined", outputSelection: false };
    let browserAudioTestStream = null;
    let browserAudioTestContext = null;
    let browserAudioTestAnalyser = null;
    let browserAudioTestAnimationId = null;
    let backendAudioTestTimer = null;
    let backendAudioTestRequestActive = false;
    let cloudApiLoaded = false;
    let cloudApiLoading = false;
    let mcpServersSignature = "";
    let lastMcpServers = [];
    let currentWebTtsSource = null;
    let currentWebTtsAudio = null;
    let thinkingAudio = null;
    let thinkingAudioUrl = "";
    let commandAckSoundUrl = "/assets/ring.wav";
    let thinkingAudioPlaying = false;
    let lastBrowserCommandAckAt = 0;
    let ortModulePromise = null;
    let sileroSessionPromise = null;

    function setMeta(text, mode = "normal", holdMs = 0) {
      metaEl.textContent = text;
      metaEl.classList.toggle("error", mode === "error");
      metaErrorUntil = mode === "error" && holdMs > 0 ? Date.now() + holdMs : 0;
    }

    let toastTimer = null;

    function hideToast() {
      window.clearTimeout(toastTimer);
      toastOverlay.classList.remove("open");
      toastOverlay.setAttribute("aria-hidden", "true");
    }

    function showToast(title, detail = "", mode = "ok", holdMs = 5000) {
      window.clearTimeout(toastTimer);
      toastTitle.textContent = title;
      toastDetail.textContent = detail;
      toastPanel.classList.toggle("ok", mode !== "error");
      toastPanel.classList.toggle("error", mode === "error");
      toastOverlay.classList.add("open");
      toastOverlay.setAttribute("aria-hidden", "false");
      if (holdMs > 0) {
        toastTimer = window.setTimeout(hideToast, holdMs);
      }
    }

    function conciseClientTtsError(error) {
      const raw = String(error && error.message ? error.message : error || "").trim();
      const lowered = raw.toLowerCase();
      try {
        const parsed = JSON.parse(raw);
        const payload = parsed.error || parsed;
        if (payload && payload.message) return String(payload.message);
      } catch (_) {}
      if (
        lowered.includes("quota_exceeded") ||
        lowered.includes("insufficient_quota") ||
        lowered.includes("exceeds your quota") ||
        lowered.includes("0 credits remaining")
      ) {
        return "Plus de crédit TTS disponible.";
      }
      if (
        lowered.includes("invalid_api_key") ||
        lowered.includes("invalid api key") ||
        lowered.includes("unauthorized") ||
        lowered.includes("status_code: 401") ||
        lowered.includes("status code: 401")
      ) {
        return "Clé API TTS invalide ou refusée.";
      }
      if (
        lowered.includes("rate_limit") ||
        lowered.includes("rate limit") ||
        lowered.includes("too many requests") ||
        lowered.includes("status_code: 429") ||
        lowered.includes("status code: 429")
      ) {
        return "Limite TTS atteinte, réessaie dans un moment.";
      }
      if (lowered.includes("credit") || lowered.includes("billing") || lowered.includes("payment")) {
        return "Problème de crédit ou facturation TTS.";
      }
      const text = raw.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
      return text ? `Erreur TTS web: ${text.slice(0, 140)}` : "Erreur TTS web.";
    }

    function formatNumber(value) {
      if (value === null || value === undefined || value === "") return "n/a";
      const number = Number(value);
      if (!Number.isFinite(number)) return String(value);
      return new Intl.NumberFormat(undefined).format(number);
    }

    function formatCurrency(value, currency = "usd") {
      const number = Number(value || 0);
      if (!Number.isFinite(number)) return "n/a";
      try {
        return new Intl.NumberFormat(undefined, { style: "currency", currency: String(currency || "usd").toUpperCase() }).format(number);
      } catch (_) {
        return `${number.toFixed(2)} ${String(currency || "usd").toUpperCase()}`;
      }
    }

    function cloudApiCard(provider, item) {
      const status = item && item.status ? item.status : "unknown";
      const statusClass = status === "ok" ? "ok" : status === "missing" || status === "unavailable" ? "warn" : "bad";
      const lines = Array.isArray(item && item.lines) ? item.lines : [];
      const maskedKey = item && item.masked_key ? item.masked_key : "non configurée";
      return `
        <div class="cloud-api-card">
          <div class="cloud-api-title">
            <span>${escapeHtml(provider)}</span>
            <span class="cloud-api-status ${statusClass}">${escapeHtml(status)}</span>
          </div>
          <div class="cloud-api-key">Key: ${escapeHtml(maskedKey)}</div>
          ${lines.map((line) => `<div class="cloud-api-line">${escapeHtml(line)}</div>`).join("")}
        </div>
      `;
    }

    function renderCloudApiStatus(data) {
      const openai = data.openai || {};
      const elevenlabs = data.elevenlabs || {};
      const openaiLines = Array.isArray(openai.lines) ? openai.lines.slice() : [];
      if (openai.cost_7d) {
        openaiLines.unshift(`Coût 7 jours: ${formatCurrency(openai.cost_7d.value, openai.cost_7d.currency)}`);
      }
      const elevenLines = Array.isArray(elevenlabs.lines) ? elevenlabs.lines.slice() : [];
      if (elevenlabs.characters) {
        elevenLines.unshift(
          `Caractères restants: ${formatNumber(elevenlabs.characters.remaining)} / ${formatNumber(elevenlabs.characters.limit)}`
        );
        elevenLines.unshift(`Caractères utilisés: ${formatNumber(elevenlabs.characters.used)}`);
      }
      cloudApiGrid.innerHTML = [
        cloudApiCard("OpenAI", { ...openai, lines: openaiLines }),
        cloudApiCard("ElevenLabs", { ...elevenlabs, lines: elevenLines })
      ].join("");
    }

    async function loadCloudApiStatus(force = false) {
      if (cloudApiLoading || (!force && cloudApiLoaded)) return;
      cloudApiLoading = true;
      cloudApiRefresh.disabled = true;
      cloudApiGrid.innerHTML = '<div class="cloud-api-line">Chargement...</div>';
      try {
        const response = await fetch("/api/cloud-api-status", { cache: "no-store" });
        const text = await response.text();
        if (!response.ok) throw new Error(text);
        renderCloudApiStatus(JSON.parse(text));
        cloudApiLoaded = true;
      } catch (error) {
        cloudApiGrid.innerHTML = `<div class="cloud-api-line">${escapeHtml(`Cloud API unavailable: ${error}`)}</div>`;
      } finally {
        cloudApiLoading = false;
        cloudApiRefresh.disabled = false;
      }
    }

    function escapeHtml(value) {
      return String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function selectedMcpAdminRoute() {
      const checked = mcpAdminRouteInputs.find((input) => input.checked);
      return checked && checked.value === "direct" ? "direct" : "proxy";
    }

    function setSelectedMcpAdminRoute(value) {
      const normalized = value === "direct" ? "direct" : "proxy";
      for (const input of mcpAdminRouteInputs) {
        input.checked = input.value === normalized;
      }
    }

    function mcpServerAdminUrl(server, route) {
      if (route === "direct") return server.admin_url || "";
      return server.proxy_admin_url || server.admin_url || "";
    }

    function mcpServerRouteDetail(route) {
      if (route === "direct") {
        return "Direct mode loads the MCP server from this browser. Use it only when this device can reach that address.";
      }
      return "HTTP proxy mode loads the MCP admin page through LiveStageAssistant, so only the backend needs access.";
    }

    function renderMcpServers(servers) {
      const items = Array.isArray(servers) ? servers : [];
      lastMcpServers = items;
      const route = selectedMcpAdminRoute();
      const signature = JSON.stringify(items.map((item) => [
        item.name || "",
        item.type || "",
        item.admin_url || "",
        item.proxy_admin_url || "",
        Boolean(item.embeddable),
        Boolean(item.auth_required),
        item.routing || "",
        JSON.stringify(item.env_options || {}),
        item.detail || ""
      ])) + "|" + route;
      if (signature === mcpServersSignature) return;
      mcpServersSignature = signature;
      mcpServerGrid.replaceChildren();

      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "mcp-server-empty";
        empty.textContent = "No MCP servers loaded from the active config.";
        mcpServerGrid.append(empty);
        return;
      }

      for (const server of items) {
        const card = document.createElement("div");
        card.className = "mcp-server-card";

        const head = document.createElement("div");
        head.className = "mcp-server-head";

        const title = document.createElement("div");
        title.className = "mcp-server-title";
        const name = document.createElement("div");
        name.className = "mcp-server-name";
        name.textContent = server.name || "MCP server";
        const url = document.createElement("div");
        url.className = "mcp-server-url";
        url.textContent = server.admin_url || server.detail || "No browser admin URL";
        title.append(name, url);

        const actions = document.createElement("div");
        actions.className = "mcp-server-actions";
        const badge = document.createElement("span");
        badge.className = "inline-badge";
        badge.textContent = server.auth_required ? "Auth" : (server.type || "MCP");
        actions.append(badge);
        const selectedUrl = mcpServerAdminUrl(server, route);
        if (selectedUrl) {
          const open = document.createElement("a");
          open.className = "mcp-server-open";
          open.href = selectedUrl;
          open.target = "_blank";
          open.rel = "noreferrer";
          open.textContent = route === "direct" ? "Open direct" : "Open via proxy";
          actions.append(open);
        }
        const alternateUrl = route === "direct" ? server.proxy_admin_url : server.admin_url;
        if (alternateUrl && alternateUrl !== selectedUrl) {
          const alternate = document.createElement("a");
          alternate.className = "mcp-server-open";
          alternate.href = alternateUrl;
          alternate.target = "_blank";
          alternate.rel = "noreferrer";
          alternate.textContent = route === "direct" ? "Proxy" : "Direct";
          actions.append(alternate);
        }

        head.append(title, actions);
        card.append(head);

        const routingBox = document.createElement("div");
        routingBox.className = "mcp-routing-box";
        const routingDisabled = !selectedMcpToolRoutingEnabled();
        const routingDisabledReason = "Tool Routing is Off. Enable Config -> IA model -> Tool Routing to edit routing words.";
        routingBox.classList.toggle("disabled", routingDisabled);
        routingBox.title = routingDisabled ? routingDisabledReason : "";
        const routingLabel = document.createElement("label");
        routingLabel.textContent = "Routing words";
        routingLabel.title = "assistantOptions.routing";
        const routingRow = document.createElement("div");
        routingRow.className = "mcp-routing-row";
        const routingInput = document.createElement("textarea");
        routingInput.className = "mcp-routing-input";
        routingInput.dataset.serverName = server.name || "";
        routingInput.value = server.routing || "";
        routingInput.placeholder = "mixer,mix,volume,bus";
        routingInput.spellcheck = false;
        routingInput.disabled = routingDisabled;
        routingInput.title = routingDisabled ? routingDisabledReason : "assistantOptions.routing";
        const routingSave = document.createElement("button");
        routingSave.className = "mcp-routing-save";
        routingSave.type = "button";
        routingSave.textContent = "Save";
        routingSave.disabled = routingDisabled;
        routingSave.title = routingDisabled ? routingDisabledReason : "Save routing words";
        const routingMessage = document.createElement("div");
        routingMessage.className = "mcp-routing-message";
        routingMessage.textContent = routingDisabled
          ? "Tool Routing is Off; enable it to edit these words."
          : "Comma-separated words; max 10 per server, no duplicates.";
        routingSave.addEventListener("click", () => saveMcpRouting(routingMessage));
        routingRow.append(routingInput, routingSave);
        routingBox.append(routingLabel, routingRow, routingMessage);
        card.append(routingBox);

        const hasEnvOptions = server.env_options && Object.keys(server.env_options).length > 0;
        const isStdioServer = !server.admin_url && String(server.type || "stdio") === "stdio";
        if (hasEnvOptions || isStdioServer) {
          const optionsBox = document.createElement("div");
          optionsBox.className = "mcp-routing-box";
          const optionsLabel = document.createElement("label");
          optionsLabel.textContent = "Server env options";
          optionsLabel.title = "mcpServers.<server>.env";
          const optionsRow = document.createElement("div");
          optionsRow.className = "mcp-routing-row";
          const optionsInput = document.createElement("textarea");
          optionsInput.className = "mcp-options-input";
          optionsInput.dataset.serverName = server.name || "";
          optionsInput.value = JSON.stringify(server.env_options || {}, null, 2);
          optionsInput.placeholder = '{\\n  "XMS_SPEAKER_MAP": {\\n    "laurent": { "bus": "Laurent", "channel": "Talk Laurent" }\\n  }\\n}';
          optionsInput.spellcheck = false;
          optionsInput.title = "JSON object saved into mcpServers.<server>.env. Nested objects are stored as compact JSON strings.";
          const optionsSave = document.createElement("button");
          optionsSave.className = "mcp-routing-save";
          optionsSave.type = "button";
          optionsSave.textContent = "Save";
          optionsSave.title = "Save MCP server env options";
          const optionsMessage = document.createElement("div");
          optionsMessage.className = "mcp-routing-message";
          optionsMessage.textContent = "Advanced JSON env options; saved to the active MCP config.";
          optionsSave.addEventListener("click", () => saveMcpServerOptions(optionsMessage));
          optionsRow.append(optionsInput, optionsSave);
          optionsBox.append(optionsLabel, optionsRow, optionsMessage);
          card.append(optionsBox);
        }

        if (server.embeddable && selectedUrl) {
          const placeholder = document.createElement("div");
          placeholder.className = "mcp-server-placeholder";

          const note = document.createElement("div");
          note.className = "detail";
          note.textContent = mcpServerRouteDetail(route);

          const load = document.createElement("button");
          load.className = "mcp-server-load";
          load.type = "button";
          load.textContent = "Load frame";
          load.addEventListener("click", () => {
            const frame = document.createElement("iframe");
            frame.className = "mcp-server-frame";
            frame.title = `${server.name || "MCP server"} admin`;
            frame.referrerPolicy = "no-referrer";
            frame.src = selectedUrl;
            placeholder.replaceWith(frame);
          });

          placeholder.append(note, load);
          card.append(placeholder);
        } else {
          const empty = document.createElement("div");
          empty.className = "mcp-server-empty";
          empty.textContent = server.detail || "This MCP server does not expose a browser page.";
          card.append(empty);
        }

        mcpServerGrid.append(card);
      }
    }

    async function saveMcpRouting(messageEl) {
      if (!selectedMcpToolRoutingEnabled()) {
        if (messageEl) messageEl.textContent = "Tool Routing is Off; enable it to edit routing words.";
        return;
      }
      const routing = {};
      for (const input of mcpServerGrid.querySelectorAll(".mcp-routing-input")) {
        const name = input.dataset.serverName || "";
        if (name) routing[name] = input.value || "";
      }
      if (messageEl) messageEl.textContent = tr("saving", "Saving...");
      try {
        const response = await fetch("/api/mcp-routing", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ routing })
        });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        if (messageEl) messageEl.textContent = data.message || "Routing saved.";
        setEnvironmentLoading(true);
        mcpServersSignature = "";
        await refresh();
      } catch (error) {
        if (messageEl) messageEl.textContent = trf("save_failed", "Save failed: {error}", { error });
      }
    }

    async function saveMcpServerOptions(messageEl) {
      const options = {};
      for (const input of mcpServerGrid.querySelectorAll(".mcp-options-input")) {
        const name = input.dataset.serverName || "";
        if (!name) continue;
        try {
          const parsed = JSON.parse(input.value || "{}");
          if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
            throw new Error("expected a JSON object");
          }
          options[name] = parsed;
        } catch (error) {
          if (messageEl) messageEl.textContent = `Invalid JSON for ${name}: ${error.message || error}`;
          return;
        }
      }
      if (messageEl) messageEl.textContent = tr("saving", "Saving...");
      try {
        const response = await fetch("/api/mcp-server-options", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ options })
        });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        if (messageEl) messageEl.textContent = data.message || "MCP server options saved.";
        setEnvironmentLoading(true);
        mcpServersSignature = "";
        await refresh();
      } catch (error) {
        if (messageEl) messageEl.textContent = trf("save_failed", "Save failed: {error}", { error });
      }
    }

    function syncMcpRoutingEditors() {
      const disabled = !selectedMcpToolRoutingEnabled();
      const reason = "Tool Routing is Off. Enable Config -> IA model -> Tool Routing to edit routing words.";
      for (const box of mcpServerGrid.querySelectorAll(".mcp-routing-box")) {
        box.classList.toggle("disabled", disabled);
        box.title = disabled ? reason : "";
      }
      for (const input of mcpServerGrid.querySelectorAll(".mcp-routing-input")) {
        input.disabled = disabled;
        input.title = disabled ? reason : "assistantOptions.routing";
      }
      for (const button of mcpServerGrid.querySelectorAll(".mcp-routing-save")) {
        button.disabled = disabled;
        button.title = disabled ? reason : "Save routing words";
      }
      for (const message of mcpServerGrid.querySelectorAll(".mcp-routing-message")) {
        message.textContent = disabled
          ? "Tool Routing is Off; enable it to edit these words."
          : "Comma-separated words; max 10 per server, no duplicates.";
      }
    }

    function isStopCommand(value) {
      const normalized = String(value || "")
        .toLowerCase()
        .normalize("NFD")
        .replace(/[\\u0300-\\u036f]/g, "")
        .replace(/[^\\w'-]+/g, " ")
        .trim();
      if (!normalized) return false;
      const stopWords = new Set(["stop", "stoppe", "stope", "arrete", "arreter", "annule", "annuler", "cancel"]);
      return normalized.split(/\\s+/).some((word) => stopWords.has(word));
    }

    function setVncStatus(value) {
      const status = value || "hors ligne";
      vncStatus.textContent = status;
      vncStatus.classList.toggle("online", status.startsWith("connecté") || status.startsWith("Connecté"));
      vncStatus.classList.toggle("offline", status.startsWith("hors ligne"));
    }

    function noVncUrlFromInput(value) {
      const raw = String(value || "").trim();
      if (!raw) return "";
      const lowerRaw = raw.toLowerCase();
      if (lowerRaw.startsWith("http://") || lowerRaw.startsWith("https://")) return raw;

      const parsed = new URL(lowerRaw.startsWith("vnc:") ? `http:${raw.slice(4)}` : raw);
      const params = new URLSearchParams();
      params.set("host", parsed.hostname);
      params.set("port", parsed.port || "5900");
      params.set("autoconnect", "1");
      params.set("resize", "scale");
      params.set("viewOnly", vncViewOnly.checked ? "1" : "0");
      const password = parsed.searchParams.get("password") || "ronron";
      if (password) params.set("password", password);
      return `/vnc.html?${params.toString()}`;
    }

    async function saveRemoteScreenUrl() {
      const nextUrl = vncUrl.value.trim();
      const response = await fetch("/api/remote-screen-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vnc_url: nextUrl, view_only: Boolean(vncViewOnly.checked) })
      });
      if (!response.ok) throw new Error(await response.text());
      vncUrlDirty = false;
      return response.json();
    }

    function disconnectVnc(status = "hors ligne") {
      window.clearTimeout(vncConnectTimer);
      currentVncFrameUrl = "";
      vncFrame.src = "about:blank";
      setVncStatus(status);
    }

    async function connectVnc({ save = false, force = false } = {}) {
      let frameUrl = "";
      try {
        if (save) await saveRemoteScreenUrl();
        frameUrl = noVncUrlFromInput(vncUrl.value);
      } catch (error) {
        setVncStatus("hors ligne");
        metaEl.textContent = `VNC URL error: ${error}`;
        return;
      }
      if (!frameUrl) {
        setVncStatus("hors ligne");
        return;
      }
      if (!force && frameUrl === currentVncFrameUrl && vncFrame.src) {
        return;
      }
      setVncStatus("connexion...");
      window.clearTimeout(vncConnectTimer);
      vncConnectTimer = window.setTimeout(() => setVncStatus("hors ligne"), 5000);
      try {
        const targetUrl = new URL(frameUrl, window.location.href);
        if (targetUrl.origin === window.location.origin) {
          const response = await fetch(targetUrl.href, { method: "GET", cache: "no-store" });
          if (!response.ok) {
            window.clearTimeout(vncConnectTimer);
            setVncStatus("hors ligne");
            metaEl.textContent = `noVNC indisponible: ${targetUrl.pathname}`;
            return;
          }
        }
      } catch (error) {
        window.clearTimeout(vncConnectTimer);
        setVncStatus("hors ligne");
        metaEl.textContent = `noVNC indisponible: ${error}`;
        return;
      }
      if (currentVncFrameUrl && (force || currentVncFrameUrl !== frameUrl)) {
        vncFrame.src = "about:blank";
        await new Promise((resolve) => window.setTimeout(resolve, 0));
      }
      currentVncFrameUrl = frameUrl;
      vncFrame.src = frameUrl;
    }

    function ledClass(status) {
      const value = String(status || "unknown").toLowerCase();
      if (["online", "initialized", "ready", "ok", "configured"].includes(value)) return "ok";
      if (["initializing", "reload", "unknown", "warning"].includes(value)) return "warn";
      if (["offline", "error", "failed"].includes(value)) return "bad";
      return "idle";
    }

    function tile(title, status, detail) {
      return `<div class="tile">
        <div class="tile-title"><span class="led ${ledClass(status)}"></span><span>${escapeHtml(title)}</span></div>
        <div>${escapeHtml(status || "unknown")}</div>
        <div class="detail">${escapeHtml(detail || "")}</div>
      </div>`;
    }

    function messageBubble(message) {
      const role = message.role === "user" ? "user" : "assistant";
      const pending = message.pending ? " pending" : "";
      const contextIncluded = message.context_included ? " context-included" : "";
      return `<div class="message-row ${role}${pending}${contextIncluded}">
        <div class="bubble">${escapeHtml(message.text)}</div>
      </div>`;
    }

    function canHoverSessionSummary() {
      return window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    }

    function sessionSummaryForId(sessionId) {
      return String(sessionSummaryCache.get(sessionId) || "").trim();
    }

    function clearSessionSummaryHoverTimer() {
      if (sessionSummaryHoverTimer) {
        window.clearTimeout(sessionSummaryHoverTimer);
        sessionSummaryHoverTimer = null;
      }
    }

    function setSessionSummaryButtonsExpanded(sessionId, expanded) {
      for (const button of sessionList.querySelectorAll(".session-summary-button")) {
        const row = button.closest(".session-row");
        button.setAttribute("aria-expanded", expanded && row?.dataset.sessionId === sessionId ? "true" : "false");
      }
    }

    function placeSessionSummaryPopover(anchorRect) {
      if (!anchorRect) return;
      const compact = window.matchMedia("(max-width: 720px), (hover: none), (pointer: coarse)").matches;
      const gap = 8;
      const margin = 12;
      const rect = sessionSummaryPopover.getBoundingClientRect();
      let left = compact ? anchorRect.left : anchorRect.right + gap;
      let top = compact ? anchorRect.bottom + gap : anchorRect.top;
      left = Math.max(margin, Math.min(left, window.innerWidth - rect.width - margin));
      top = Math.max(margin, Math.min(top, window.innerHeight - rect.height - margin));
      sessionSummaryPopover.style.left = `${left}px`;
      sessionSummaryPopover.style.top = `${top}px`;
    }

    function openSessionSummary(sessionId, anchorElement, { pinned = false } = {}) {
      const summary = sessionSummaryForId(sessionId);
      if (!summary || !anchorElement) return;
      closeSessionMenus();
      clearSessionSummaryHoverTimer();
      if (openSessionSummaryId === sessionId && sessionSummaryPopover.classList.contains("open")) {
        sessionSummaryPinned = sessionSummaryPinned || Boolean(pinned);
        sessionSummaryPopover.textContent = summary;
        setSessionSummaryButtonsExpanded(sessionId, true);
        return;
      }
      openSessionSummaryId = sessionId;
      sessionSummaryPinned = Boolean(pinned);
      sessionSummaryHoverId = "";
      const rect = anchorElement.getBoundingClientRect();
      sessionSummaryAnchor = {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom
      };
      sessionSummaryPopover.textContent = summary;
      sessionSummaryPopover.classList.add("open");
      placeSessionSummaryPopover(sessionSummaryAnchor);
      setSessionSummaryButtonsExpanded(sessionId, true);
    }

    function closeSessionSummary() {
      clearSessionSummaryHoverTimer();
      openSessionSummaryId = "";
      sessionSummaryPinned = false;
      sessionSummaryAnchor = null;
      sessionSummaryHoverId = "";
      sessionSummaryPopover.classList.remove("open");
      sessionSummaryPopover.textContent = "";
      sessionSummaryPopover.style.left = "";
      sessionSummaryPopover.style.top = "";
      setSessionSummaryButtonsExpanded("", false);
    }

    function pointerIsOnSessionSummaryTarget(sessionId) {
      if (lastPointer.x < 0 || lastPointer.y < 0) return false;
      const target = document.elementFromPoint(lastPointer.x, lastPointer.y);
      if (!target) return false;
      if (target.closest("#session-summary-popover")) return true;
      const row = target.closest(".session-row");
      return row?.dataset.sessionId === sessionId;
    }

    function scheduleSessionSummary(sessionId) {
      if (openSessionSummaryId === sessionId && sessionSummaryPopover.classList.contains("open")) {
        return;
      }
      clearSessionSummaryHoverTimer();
      sessionSummaryHoverId = sessionId;
      sessionSummaryHoverTimer = window.setTimeout(() => {
        if (openSessionSummaryId === sessionId && sessionSummaryPopover.classList.contains("open")) return;
        const row = Array.from(sessionList.querySelectorAll(".session-row"))
          .find((candidate) => candidate.dataset.sessionId === sessionId);
        if (!row || !pointerIsOnSessionSummaryTarget(sessionId)) return;
        openSessionSummary(sessionId, row);
      }, 650);
    }

    function closeSessionSummaryAfterPointerCheck(sessionId) {
      window.setTimeout(() => {
        if (
          (openSessionSummaryId === sessionId || sessionSummaryHoverId === sessionId) &&
          !pointerIsOnSessionSummaryTarget(sessionId)
        ) {
          closeSessionSummary();
        }
      }, 80);
    }

    function syncOpenSessionSummaryAfterRender() {
      if (openSessionSummaryId && !sessionSummaryForId(openSessionSummaryId)) {
        closeSessionSummary();
        return;
      }
      if (openSessionSummaryId) {
        sessionSummaryPopover.textContent = sessionSummaryForId(openSessionSummaryId);
        setSessionSummaryButtonsExpanded(openSessionSummaryId, true);
        if (!sessionSummaryPinned && !pointerIsOnSessionSummaryTarget(openSessionSummaryId)) {
          closeSessionSummary();
        }
      }
    }

    function sessionButton(session, activeId) {
      const active = session.id === activeId ? " active" : "";
      const menuOpen = session.id === openSessionMenuId ? " menu-open" : "";
      const llmSummary = String(session.llm_summary || "").trim();
      const hasSummary = Boolean(llmSummary);
      const summaryClass = hasSummary ? " has-summary" : "";
      const summaryButton = hasSummary
        ? `<button class="session-summary-button" type="button" title="Afficher le llm_summary" aria-label="Afficher le llm_summary" aria-expanded="${session.id === openSessionSummaryId ? "true" : "false"}">i</button>`
        : "";
      const summaryTime = Number(session.llm_summary_updated_at || 0);
      const summaryLabel = summaryTime
        ? new Date(summaryTime * 1000).toLocaleString("fr-FR")
        : "No summary";
      return `<div class="session-row${active}${menuOpen}${summaryClass}" data-session-id="${escapeHtml(session.id)}" data-session-title="${escapeHtml(session.title || "Untitled session")}">
        <button class="session-main" type="button">
          <span class="session-title">${escapeHtml(session.title || "Untitled session")}</span>
          <span class="session-meta">${escapeHtml(summaryLabel)}</span>
        </button>
        <button class="session-menu-button" type="button" title="Session actions" aria-label="Session actions">...</button>
        ${summaryButton}
        <div class="session-menu">
          <button class="session-menu-action" type="button" data-session-action="rename">Rename</button>
          <button class="session-menu-action" type="button" data-session-action="clear">Clear conversation</button>
          <button class="session-menu-action" type="button" data-session-action="save-context">Save context</button>
          <button class="session-menu-action danger" type="button" data-session-action="delete">Delete</button>
        </div>
      </div>`;
    }

    function renderSessions(sessionContext) {
      const context = sessionContext || {};
      const sessions = context.sessions || [];
      const activeId = context.active_id || "";
      sessionSummaryCache = new Map(
        sessions
          .map((session) => [String(session.id || ""), String(session.llm_summary || "").trim()])
          .filter(([sessionId, summary]) => sessionId && summary)
      );
      if (sessions.length === 0) {
        openSessionMenuId = "";
        closeSessionSummary();
        sessionList.innerHTML = `<div class="session-meta">No session</div>`;
        return;
      }
      if (openSessionMenuId && !sessions.some((session) => session.id === openSessionMenuId)) {
        openSessionMenuId = "";
      }
      if (openSessionSummaryId && !sessions.some((session) => session.id === openSessionSummaryId)) {
        closeSessionSummary();
      }
      sessionList.innerHTML = sessions.map((session) => sessionButton(session, activeId)).join("");
      syncOpenSessionSummaryAfterRender();
    }

    function syncSessionContextSizeLabel() {
      const value = Number(sessionContextSize.value || 0);
      sessionContextSizeLabel.textContent = value === 0 ? "Off" : String(value);
    }

    function syncMcpAgentMaxStepsLabel() {
      mcpAgentMaxStepsLabel.textContent = String(mcpAgentMaxSteps.value || 20);
    }

    function setSessionContextSize(value) {
      const nextValue = Math.max(0, Math.min(12000, Number(value || 0)));
      sessionContextSize.value = String(nextValue);
      syncSessionContextSizeLabel();
    }

    function setMcpAgentMaxSteps(value) {
      const nextValue = Math.max(5, Math.min(60, Number(value || 20)));
      mcpAgentMaxSteps.value = String(Math.round(nextValue));
      syncMcpAgentMaxStepsLabel();
    }

    function summaryLineForMessage(message) {
      const role = message.role === "user" ? "User" : "Assistant";
      const text = String(message.text || "").replace(/\\s+/g, " ").trim();
      return text ? `${role}: ${text}` : "";
    }

    function contextIncludedMessageIds(messages, maxChars) {
      const limit = Math.max(0, Math.min(12000, Number(maxChars || 0)));
      const included = new Set();
      if (limit === 0) return included;

      let candidates = [...(messages || [])];
      if (candidates.length > 0 && candidates[candidates.length - 1].role === "user") {
        candidates = candidates.slice(0, -1);
      }

      let total = 0;
      const selected = [];
      for (let index = candidates.length - 1; index >= 0; index -= 1) {
        const message = candidates[index];
        const line = summaryLineForMessage(message);
        if (!line) continue;
        let lineLength = line.length + 1;
        if (selected.length > 0 && total + lineLength > limit) break;
        selected.push(message.id);
        if (lineLength > limit) lineLength = limit + 1;
        total += lineLength;
      }

      for (const id of selected) included.add(String(id));
      return included;
    }

    function withContextPreview(messages) {
      const includedIds = contextIncludedMessageIds(messages, sessionContextSize.value);
      return (messages || []).map((message) => ({
        ...message,
        context_included: includedIds.has(String(message.id))
      }));
    }

    function thinkingBubble() {
      return `<div class="message-row assistant pending" aria-live="polite" aria-label="Assistant is thinking">
        <div class="bubble">
          <div class="thinking-bubble">
            <span class="thinking-dot"></span>
            <span class="thinking-dot"></span>
            <span class="thinking-dot"></span>
          </div>
        </div>
      </div>`;
    }

    function setComposerLocked(locked) {
      const wasLocked = composerLocked;
      composerLocked = Boolean(locked);
      injectForm.classList.toggle("busy", composerLocked);
      injectStop.classList.toggle("visible", composerLocked);
      injectStop.disabled = !composerLocked || cancelRequestInFlight;
      webConversation.disabled = !webAudio.stt_enabled;
      webMic.disabled = (composerLocked && !interruptConversationEnabled) || !webAudio.stt_enabled || isRecording || conversationEnabled;
      composerAttach.disabled = composerLocked || isRecording || conversationEnabled;
      injectCommand.placeholder = composerLocked ? "Assistant is thinking..." : "Message";
      if (wasLocked && !composerLocked && !settingsOverlay.classList.contains("open")) {
        window.setTimeout(() => injectCommand.focus({ preventScroll: true }), 0);
      }
      syncComposerSpeakerControl();
    }

    function setRecording(recording) {
      isRecording = Boolean(recording);
      webMic.classList.toggle("recording", isRecording);
      webMic.innerHTML = isRecording ? "&#9632;" : "🎙️";
      webMic.title = isRecording ? "Stop recording" : "Voice input";
      webMic.setAttribute("aria-label", isRecording ? "Stop recording" : "Voice input");
      webMic.disabled = (composerLocked && !interruptConversationEnabled && !isRecording) || !webAudio.stt_enabled || conversationEnabled;
      composerAttach.disabled = composerLocked || isRecording || conversationEnabled;
      injectCommand.placeholder = isRecording ? "Recording..." : (composerLocked ? "Assistant is thinking..." : "Message");
    }

    function updateConversationButton() {
      webConversation.classList.toggle("active", conversationEnabled);
      webConversation.title = conversationEnabled ? "Stop conversation mode" : "Conversation mode";
      webConversation.setAttribute("aria-label", conversationEnabled ? "Stop conversation mode" : "Conversation mode");
      webConversation.disabled = !webAudio.stt_enabled;
      webMic.disabled = (composerLocked && !interruptConversationEnabled && !isRecording) || !webAudio.stt_enabled || conversationEnabled;
      composerAttach.disabled = composerLocked || isRecording || conversationEnabled;
    }

    function clearRecordingTimer() {
      if (recordingTimer) {
        window.clearTimeout(recordingTimer);
        recordingTimer = null;
      }
    }

    function vadSettings() {
      const speechThreshold = Number(webAudio.vad_speech_threshold || 0.5);
      const negativeThreshold = Number(webAudio.vad_negative_threshold || Math.max(0.01, speechThreshold - 0.15));
      return {
        speechThreshold,
        negativeThreshold,
        minSpeechMs: Math.max(0, Number(webAudio.vad_min_speech_ms || 120)),
        minSilenceMs: Math.max(100, Number(webAudio.vad_min_silence_ms || 650)),
        maxSpeechMs: Math.max(1000, Number(webAudio.vad_max_speech_seconds || 8) * 1000)
      };
    }

    async function loadOrtModule() {
      if (!ortModulePromise) {
        ortModulePromise = import(webAudio.vad_ort_url || "/assets/web/static/vendor/onnxruntime-web/ort.wasm.min.mjs").then((module) => {
          const ort = module.default || module;
          ort.env.wasm.wasmPaths = webAudio.vad_ort_wasm_path || "/assets/web/static/vendor/onnxruntime-web/";
          ort.env.wasm.numThreads = 2;
          return ort;
        });
      }
      return ortModulePromise;
    }

    async function loadSileroSession() {
      if (!sileroSessionPromise) {
        sileroSessionPromise = loadOrtModule().then((ort) =>
          ort.InferenceSession.create(webAudio.vad_model_url || "/assets/web/static/vendor/silero-vad/silero_vad_v6.onnx", {
            executionProviders: ["wasm"]
          })
        );
      }
      return sileroSessionPromise;
    }

    function resampleTo16k(input, sourceRate) {
      if (!sourceRate || Math.abs(sourceRate - 16000) < 1) return Array.from(input);
      const ratio = sourceRate / 16000;
      const length = Math.max(1, Math.floor(input.length / ratio));
      const output = new Float32Array(length);
      for (let i = 0; i < length; i += 1) {
        const position = i * ratio;
        const left = Math.floor(position);
        const right = Math.min(input.length - 1, left + 1);
        const weight = position - left;
        output[i] = input[left] * (1 - weight) + input[right] * weight;
      }
      return output;
    }

    class BrowserSileroVad {
      constructor({ onStart, onEnd, onIdle }) {
        this.onStart = onStart;
        this.onEnd = onEnd;
        this.onIdle = onIdle;
        this.settings = vadSettings();
        this.windowSamples = 512;
        this.contextSamples = 64;
        this.pending = [];
        this.context = new Float32Array(this.contextSamples);
        this.h = new Float32Array(128);
        this.c = new Float32Array(128);
        this.processing = false;
        this.active = true;
        this.speechDetected = false;
        this.candidateMs = 0;
        this.silenceMs = 0;
        this.startedAt = Date.now();
        this.processor = null;
        this.source = null;
      }

      async attach(audioContext, source) {
        this.ort = await loadOrtModule();
        this.session = await loadSileroSession();
        this.processor = audioContext.createScriptProcessor(2048, 1, 1);
        this.processor.onaudioprocess = (event) => {
          event.outputBuffer.getChannelData(0).fill(0);
          this.push(event.inputBuffer.getChannelData(0), audioContext.sampleRate);
        };
        this.source = source;
        source.connect(this.processor);
        this.processor.connect(audioContext.destination);
      }

      close() {
        this.active = false;
        if (this.source && this.processor) {
          try {
            this.source.disconnect(this.processor);
          } catch (error) {}
        }
        if (this.processor) {
          this.processor.disconnect();
          this.processor.onaudioprocess = null;
        }
        this.processor = null;
        this.source = null;
      }

      push(input, sourceRate) {
        if (!this.active) return;
        for (const sample of resampleTo16k(input, sourceRate)) this.pending.push(sample);
        this.processQueue();
      }

      async processQueue() {
        if (this.processing || !this.session || !this.ort) return;
        this.processing = true;
        try {
          while (this.active && this.pending.length >= this.windowSamples) {
            const windowSamples = this.pending.splice(0, this.windowSamples);
            const modelInput = new Float32Array(this.windowSamples + this.contextSamples);
            modelInput.set(this.context, 0);
            modelInput.set(windowSamples, this.contextSamples);
            const results = await this.session.run({
              input: new this.ort.Tensor("float32", modelInput, [1, this.windowSamples + this.contextSamples]),
              h: new this.ort.Tensor("float32", this.h, [1, 1, 128]),
              c: new this.ort.Tensor("float32", this.c, [1, 1, 128])
            });
            const probability = Number(results.speech_probs.data[0] || 0);
            this.h = new Float32Array(results.hn.data);
            this.c = new Float32Array(results.cn.data);
            this.context = Float32Array.from(windowSamples.slice(-this.contextSamples));
            this.update(probability);
          }
        } finally {
          this.processing = false;
        }
      }

      update(probability) {
        const chunkMs = this.windowSamples / 16000 * 1000;
        if (probability >= this.settings.speechThreshold) {
          this.candidateMs += chunkMs;
          this.silenceMs = 0;
          if (!this.speechDetected && this.candidateMs >= this.settings.minSpeechMs) {
            this.speechDetected = true;
            if (this.onStart) this.onStart();
          }
        } else if (this.speechDetected && probability < this.settings.negativeThreshold) {
          this.silenceMs += chunkMs;
          if (this.silenceMs >= this.settings.minSilenceMs && this.onEnd) this.onEnd();
        } else if (!this.speechDetected) {
          this.candidateMs = 0;
        }
        if (this.speechDetected && Date.now() - this.startedAt >= this.settings.maxSpeechMs && this.onEnd) {
          this.onEnd();
        } else if (!this.speechDetected && Date.now() - this.startedAt >= 25000 && this.onIdle) {
          this.onIdle();
        }
      }
    }

    function blobToBase64(blob) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => {
          const result = String(reader.result || "");
          resolve(result.includes(",") ? result.split(",").pop() : result);
        };
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
    }

    function browserAudioConstraints() {
      if (!selectedBrowserAudioInput) return { audio: true };
      return { audio: { deviceId: { exact: selectedBrowserAudioInput } } };
    }

    function supportsBrowserAudioOutputSelection() {
      return typeof HTMLMediaElement !== "undefined" && "setSinkId" in HTMLMediaElement.prototype;
    }

    async function applyBrowserAudioOutput(audio) {
      if (!audio || !selectedBrowserAudioOutput || typeof audio.setSinkId !== "function") return;
      await audio.setSinkId(selectedBrowserAudioOutput);
    }

    function setVuMeter(fill, level) {
      if (!fill) return;
      const normalized = Math.max(0, Math.min(1, Number(level || 0)));
      fill.style.height = `${Math.round(normalized * 100)}%`;
    }

    function stopBrowserAudioTest() {
      window.cancelAnimationFrame(browserAudioTestAnimationId);
      browserAudioTestAnimationId = null;
      if (browserAudioTestStream) {
        for (const track of browserAudioTestStream.getTracks()) track.stop();
      }
      browserAudioTestStream = null;
      if (browserAudioTestContext) {
        browserAudioTestContext.close().catch(() => {});
      }
      browserAudioTestContext = null;
      browserAudioTestAnalyser = null;
      browserAudioTest.textContent = "Test";
      setVuMeter(browserAudioMeter, 0);
    }

    async function startBrowserAudioTest() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
      stopBackendAudioTest();
      try {
        browserAudioTestStream = await navigator.mediaDevices.getUserMedia(browserAudioConstraints());
        browserAudioTestContext = new (window.AudioContext || window.webkitAudioContext)();
        const source = browserAudioTestContext.createMediaStreamSource(browserAudioTestStream);
        browserAudioTestAnalyser = browserAudioTestContext.createAnalyser();
        browserAudioTestAnalyser.fftSize = 1024;
        source.connect(browserAudioTestAnalyser);
        const data = new Uint8Array(browserAudioTestAnalyser.fftSize);
        browserAudioTest.textContent = "Stop";
        const tick = () => {
          if (!browserAudioTestAnalyser) return;
          browserAudioTestAnalyser.getByteTimeDomainData(data);
          let sum = 0;
          let peak = 0;
          for (const value of data) {
            const centered = (value - 128) / 128;
            sum += centered * centered;
            peak = Math.max(peak, Math.abs(centered));
          }
          const rms = Math.sqrt(sum / data.length);
          setVuMeter(browserAudioMeter, Math.max(rms * 4, peak * 0.75));
          browserAudioTestAnimationId = window.requestAnimationFrame(tick);
        };
        tick();
      } catch (error) {
        stopBrowserAudioTest();
        metaEl.textContent = `browser audio test unavailable: ${error}`;
      }
    }

    function toggleBrowserAudioTest() {
      if (browserAudioTestStream) stopBrowserAudioTest();
      else startBrowserAudioTest();
    }

    function stopBackendAudioTest() {
      window.clearInterval(backendAudioTestTimer);
      backendAudioTestTimer = null;
      backendAudioTestRequestActive = false;
      backendAudioTest.textContent = "Test";
      setVuMeter(backendAudioMeter, 0);
    }

    async function pollBackendAudioLevel() {
      if (backendAudioTestRequestActive) return;
      backendAudioTestRequestActive = true;
      try {
        const response = await fetch("/api/backend-audio-level", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ device: backendAudioInput.value || "" })
        });
        const text = await response.text();
        const data = text ? JSON.parse(text) : {};
        if (!response.ok || data.ok === false) {
          const message = data.error && data.error.message ? data.error.message : data.detail || text;
          throw new Error(message || "backend audio level unavailable");
        }
        setVuMeter(backendAudioMeter, Math.max(Number(data.level || 0), Number(data.peak || 0) * 0.7));
      } catch (error) {
        stopBackendAudioTest();
        metaEl.textContent = `backend audio test unavailable: ${error}`;
      } finally {
        backendAudioTestRequestActive = false;
      }
    }

    function startBackendAudioTest() {
      stopBrowserAudioTest();
      backendAudioTest.textContent = "Stop";
      pollBackendAudioLevel();
      backendAudioTestTimer = window.setInterval(pollBackendAudioLevel, 450);
    }

    function toggleBackendAudioTest() {
      if (backendAudioTestTimer) stopBackendAudioTest();
      else startBackendAudioTest();
    }

    async function loadBrowserAudioDevices(requestPermission = false) {
      browserAudioInput.replaceChildren(option("Default browser input", "", false, !selectedBrowserAudioInput));
      browserAudioOutput.replaceChildren(option("Default browser output", "", false, !selectedBrowserAudioOutput));
      if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
        browserAudioCapabilities = { input: false, output: typeof Audio !== "undefined", outputSelection: false };
        browserAudioInput.replaceChildren(option("Browser devices unavailable", "", true, true));
        browserAudioOutput.replaceChildren(option("Browser devices unavailable", "", true, true));
        browserAudioInput.disabled = true;
        browserAudioOutput.disabled = true;
        browserAudioRefresh.disabled = true;
        syncAudioCapabilityControls();
        return;
      }

      let permissionStream = null;
      if (requestPermission && navigator.mediaDevices.getUserMedia) {
        try {
          permissionStream = await navigator.mediaDevices.getUserMedia(browserAudioConstraints());
        } catch (error) {
          metaEl.textContent = `browser audio devices unavailable: ${error}`;
        } finally {
          if (permissionStream) {
            for (const track of permissionStream.getTracks()) track.stop();
          }
        }
      }

      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const inputs = devices.filter((device) => device.kind === "audioinput");
        const outputs = devices.filter((device) => device.kind === "audiooutput");
        const canSelectOutput = supportsBrowserAudioOutputSelection();
        browserAudioCapabilities = {
          input: inputs.length > 0,
          output: typeof Audio !== "undefined",
          outputSelection: canSelectOutput && outputs.length > 0
        };

        browserAudioInput.replaceChildren(option("Default browser input", "", false, !selectedBrowserAudioInput));
        inputs.forEach((device, index) => {
          const label = device.label || `Microphone ${index + 1}`;
          browserAudioInput.appendChild(option(label, device.deviceId, false, device.deviceId === selectedBrowserAudioInput));
        });
        if (selectedBrowserAudioInput && ![...browserAudioInput.options].some((item) => item.value === selectedBrowserAudioInput)) {
          browserAudioInput.appendChild(option(`${selectedBrowserAudioInput} (current unavailable)`, selectedBrowserAudioInput, false, true));
        }

        if (!canSelectOutput) {
          browserAudioOutput.replaceChildren(option("Output selection unsupported", "", true, true));
        } else {
          browserAudioOutput.replaceChildren(option("Default browser output", "", false, !selectedBrowserAudioOutput));
          outputs.forEach((device, index) => {
            const label = device.label || `Speaker ${index + 1}`;
            browserAudioOutput.appendChild(option(label, device.deviceId, false, device.deviceId === selectedBrowserAudioOutput));
          });
          if (selectedBrowserAudioOutput && ![...browserAudioOutput.options].some((item) => item.value === selectedBrowserAudioOutput)) {
            browserAudioOutput.appendChild(option(`${selectedBrowserAudioOutput} (current unavailable)`, selectedBrowserAudioOutput, false, true));
          }
        }

        browserAudioInput.disabled = inputs.length === 0;
        browserAudioOutput.disabled = !canSelectOutput || browserAudioOutput.options.length === 0;
        browserAudioRefresh.disabled = false;
        syncAudioCapabilityControls();
      } catch (error) {
        browserAudioCapabilities = { input: false, output: typeof Audio !== "undefined", outputSelection: false };
        browserAudioInput.replaceChildren(option("Could not list devices", "", true, true));
        browserAudioOutput.replaceChildren(option("Could not list devices", "", true, true));
        browserAudioInput.disabled = true;
        browserAudioOutput.disabled = true;
        syncAudioCapabilityControls();
        metaEl.textContent = `browser audio devices unavailable: ${error}`;
      }
    }

    function base64ToArrayBuffer(base64) {
      const binary = window.atob(base64);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      return bytes.buffer;
    }

    async function unlockWebTtsAudio() {
      if (!webAudio.tts_enabled || webTtsUnlocked) return;
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return;
      try {
        if (!webTtsAudioContext) webTtsAudioContext = new AudioContextClass();
        if (webTtsAudioContext.state === "suspended") await webTtsAudioContext.resume();
        const buffer = webTtsAudioContext.createBuffer(1, 1, 22050);
        const source = webTtsAudioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(webTtsAudioContext.destination);
        source.start(0);
        webTtsUnlocked = webTtsAudioContext.state === "running";
      } catch (error) {
        webTtsUnlocked = false;
      }
    }

    async function unlockWebTtsAudioFromUserGesture() {
      await unlockWebTtsAudio();
      if (!webTtsUnlocked || !pendingWebTtsMessage) return;
      const message = pendingWebTtsMessage;
      pendingWebTtsMessage = null;
      lastSpokenAssistantMessageId = message.id;
      playBrowserCommandAckSound();
      playWebTts(message.text || "");
    }

    function deferWebTtsUntilUserGesture(message) {
      pendingWebTtsMessage = message;
      setMeta("Audio navigateur bloqué: clique dans la page pour activer le TTS.", "warn", 15000);
    }

    async function ensureWebAudioContext() {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return null;
      if (!webTtsAudioContext) webTtsAudioContext = new AudioContextClass();
      if (webTtsAudioContext.state === "suspended") {
        try {
          await webTtsAudioContext.resume();
        } catch (error) {
          return null;
        }
      }
      return webTtsAudioContext.state === "running" ? webTtsAudioContext : null;
    }

    async function playBrowserCommandAckSound() {
      if (!commandAckSoundEnabled() || !webAudio.tts_enabled || webAudio.tts_output !== "browser") return;
      const nowMs = Date.now();
      if (nowMs - lastBrowserCommandAckAt < 900) return;
      lastBrowserCommandAckAt = nowMs;
      try {
        const audio = new Audio(commandAckSoundUrl);
        audio.preload = "auto";
        audio.volume = Math.max(0, Math.min(1, Number(webAudio.tts_volume ?? 1)));
        await applyBrowserAudioOutput(audio);
        await audio.play();
      } catch (error) {}
    }

    async function playWebTtsBuffer(audioBase64, volume = null) {
      if (!webTtsAudioContext || webTtsAudioContext.state !== "running") return false;
      try {
        const arrayBuffer = base64ToArrayBuffer(audioBase64);
        const audioBuffer = await webTtsAudioContext.decodeAudioData(arrayBuffer.slice(0));
        const source = webTtsAudioContext.createBufferSource();
        const gain = webTtsAudioContext.createGain();
        source.buffer = audioBuffer;
        source.playbackRate.value = 1;
        gain.gain.value = Math.max(0, Math.min(1, Number(volume ?? webAudio.tts_volume ?? 1)));
        source.connect(gain);
        gain.connect(webTtsAudioContext.destination);
        currentWebTtsSource = source;
        stopThinkingAudio();
        await new Promise((resolve, reject) => {
          source.addEventListener("ended", resolve, { once: true });
          try {
            source.start(0);
          } catch (error) {
            reject(error);
          }
        });
        return true;
      } catch (error) {
        currentWebTtsSource = null;
        return false;
      }
    }

    async function playWebTtsElement(audioBase64, mimeType, volume = null) {
      const arrayBuffer = base64ToArrayBuffer(audioBase64);
      const blob = new Blob([arrayBuffer], { type: mimeType || "audio/mpeg" });
      const objectUrl = URL.createObjectURL(blob);
      const audio = new Audio(objectUrl);
      currentWebTtsAudio = audio;
      audio.playbackRate = Math.max(0.6, Math.min(1.8, Number(webAudio.tts_speed || 1)));
      audio.volume = Math.max(0, Math.min(1, Number(volume ?? webAudio.tts_volume ?? 1)));
      audio.preservesPitch = true;
      audio.mozPreservesPitch = true;
      audio.webkitPreservesPitch = true;
      await applyBrowserAudioOutput(audio);
      try {
        await new Promise((resolve, reject) => {
          audio.addEventListener("ended", resolve, { once: true });
          audio.addEventListener("error", reject, { once: true });
          audio.play()
            .then(() => stopThinkingAudio())
            .catch(reject);
        });
      } finally {
        URL.revokeObjectURL(objectUrl);
      }
    }

    function stopMediaStream() {
      stopSoundwave();
      clearRecordingTimer();
      if (recordingMonitorId) {
        window.cancelAnimationFrame(recordingMonitorId);
        recordingMonitorId = null;
      }
      if (recordingVad) {
        recordingVad.close();
        recordingVad = null;
      }
      if (recordingAudioContext) {
        recordingAudioContext.close().catch(() => {});
        recordingAudioContext = null;
      }
      if (mediaStream) {
        for (const track of mediaStream.getTracks()) {
          track.stop();
        }
      }
      mediaStream = null;
      recordingAnalyser = null;
    }

    function analyserRms(analyser) {
      if (!analyser) return 0;
      const data = new Uint8Array(analyser.fftSize);
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const value of data) {
        const centered = (value - 128) / 128;
        sum += centered * centered;
      }
      return Math.sqrt(sum / data.length);
    }

    function activeSoundwaveAnalyser() {
      if (isRecording && recordingAnalyser) return recordingAnalyser;
      if (
        conversationEnabled &&
        conversationRecorder &&
        conversationRecorder.state !== "inactive" &&
        conversationAnalyser
      ) {
        return conversationAnalyser;
      }
      return null;
    }

    function resizeSoundwaveCanvas() {
      const rect = soundwave.getBoundingClientRect();
      const scale = window.devicePixelRatio || 1;
      const width = Math.max(1, Math.floor(rect.width * scale));
      const height = Math.max(1, Math.floor(rect.height * scale));
      if (soundwave.width !== width || soundwave.height !== height) {
        soundwave.width = width;
        soundwave.height = height;
      }
      return { width, height, scale };
    }

    function drawSoundwave() {
      if (!soundwaveAnimationId) return;
      const ctx = soundwave.getContext("2d");
      if (!ctx) return;

      const { width, height, scale } = resizeSoundwaveCanvas();
      const cssWidth = width / scale;
      const cssHeight = height / scale;
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      ctx.clearRect(0, 0, cssWidth, cssHeight);

      const time = (Date.now() - soundwaveStartedAt) / 1000;
      const centerY = cssHeight / 2;
      const padding = 10;
      const usableWidth = Math.max(1, cssWidth - padding * 2);
      const analyser = activeSoundwaveAnalyser();
      const samples = analyser ? new Uint8Array(analyser.fftSize) : null;
      if (analyser && samples) analyser.getByteTimeDomainData(samples);

      const gradient = ctx.createLinearGradient(padding, 0, cssWidth - padding, 0);
      gradient.addColorStop(0, "rgba(16, 185, 129, 0.25)");
      gradient.addColorStop(0.5, "rgba(59, 130, 246, 0.85)");
      gradient.addColorStop(1, "rgba(16, 185, 129, 0.25)");
      ctx.lineWidth = 2.4;
      ctx.lineCap = "round";
      ctx.strokeStyle = gradient;
      ctx.beginPath();

      const points = 96;
      for (let point = 0; point < points; point += 1) {
        const ratio = point / (points - 1);
        const x = padding + ratio * usableWidth;
        let normalized = Math.sin(ratio * Math.PI * 8 + time * 4.5) * 0.12;
        if (samples) {
          const sampleIndex = Math.min(samples.length - 1, Math.floor(ratio * samples.length));
          normalized = (samples[sampleIndex] - 128) / 128;
        }
        const envelope = Math.sin(ratio * Math.PI);
        const y = centerY + normalized * envelope * cssHeight * 0.42;
        if (point === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();

      soundwaveAnimationId = window.requestAnimationFrame(drawSoundwave);
    }

    function startSoundwave() {
      commandField.classList.add("soundwave-active");
      soundwaveStartedAt = Date.now();
      if (soundwaveAnimationId) window.cancelAnimationFrame(soundwaveAnimationId);
      soundwaveAnimationId = window.requestAnimationFrame(drawSoundwave);
    }

    function stopSoundwave() {
      commandField.classList.remove("soundwave-active");
      if (soundwaveAnimationId) {
        window.cancelAnimationFrame(soundwaveAnimationId);
        soundwaveAnimationId = null;
      }
      const ctx = soundwave.getContext("2d");
      if (ctx) ctx.clearRect(0, 0, soundwave.width, soundwave.height);
    }

    async function requestSilentCancel() {
      stopWebTts();
      if (cancelRequestInFlight) return;
      cancelRequestInFlight = true;
      try {
        const response = await fetch("/api/cancel-command", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({})
        });
        if (!response.ok) throw new Error(await response.text());
      } finally {
        cancelRequestInFlight = false;
      }
    }

    function activeSpeakerProfilesFromConfig() {
      return collectSpeakerProfiles()
        .filter((profile) => profile.enabled && profile.name)
        .map((profile) => ({ name: profile.name }));
    }

    function composerSpeakerDisabledReason() {
      if (!speakerRecognitionEnvEnabled) return "Speaker recognition is disabled in the active config.";
      if (!speakerRecognitionRuntimeEnabled) return speakerRecognitionUnavailableReason || "Speaker recognition backend is unavailable.";
      if (speakerProfileChoices.length === 0) return "No active speaker profile is configured.";
      return "";
    }

    function syncComposerSpeakerControl() {
      const previousValue = composerSpeaker.value || window.localStorage.getItem("lsaComposerSpeaker") || "auto";
      composerSpeaker.replaceChildren();
      composerSpeaker.appendChild(option("Auto detect", "auto", false, previousValue === "auto"));
      composerSpeaker.appendChild(option("Unknown", "unknown", false, previousValue === "unknown"));
      for (const profile of speakerProfileChoices) {
        composerSpeaker.appendChild(option(profile.name, `profile:${profile.name}`, false, previousValue === `profile:${profile.name}`));
      }
      if (![...composerSpeaker.options].some((item) => item.value === previousValue)) {
        composerSpeaker.value = "auto";
        window.localStorage.setItem("lsaComposerSpeaker", "auto");
      } else {
        composerSpeaker.value = previousValue;
      }
      const disabledReason = composerSpeakerDisabledReason();
      composerSpeaker.disabled = Boolean(disabledReason);
      composerSpeaker.title = disabledReason || "Speaker profile for browser commands";
    }

    function forcedComposerSpeakerPayload() {
      if (composerSpeaker.disabled) return null;
      const selected = composerSpeaker.value || "auto";
      if (selected === "auto") return null;
      if (selected === "unknown") {
        return {
          speaker: "unknown",
          speaker_confidence: 0,
          speaker_backend: "none",
          speaker_context_explicit: true
        };
      }
      if (selected.startsWith("profile:")) {
        const speaker = selected.slice("profile:".length).trim();
        if (speaker) {
          return {
            speaker,
            speaker_confidence: 1,
            speaker_backend: "none",
            speaker_context_explicit: true
          };
        }
      }
      return null;
    }

    function speakerPayloadForSubmit(options = {}) {
      const forcedSpeaker = forcedComposerSpeakerPayload();
      if (forcedSpeaker) return forcedSpeaker;
      return {
        speaker: options.speaker || "unknown",
        speaker_confidence: Number(options.speakerConfidence || 0),
        speaker_backend: options.speakerBackend || "none",
        speaker_context_explicit: Boolean(options.speakerContextExplicit)
      };
    }

    async function submitCommand(command, options = {}) {
      const cleanedCommand = command.trim();
      if (!cleanedCommand) return;
      const shouldInterrupt = Boolean(options.interrupt) && interruptConversationEnabled && (composerLocked || webTtsPlaying);
      if (composerLocked && !shouldInterrupt) return;
      if (shouldInterrupt) {
        await requestSilentCancel();
      }

      pendingMessages.push({
        id: `pending-${Date.now()}`,
        role: "user",
        text: cleanedCommand,
        pending: true,
        sentAt: Date.now()
      });
      setComposerLocked(true);
      renderMessages(lastServerMessages, true);
      try {
        const speakerPayload = speakerPayloadForSubmit(options);
        const response = await fetch("/api/inject-command", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            command: cleanedCommand,
            speaker: speakerPayload.speaker,
            speaker_confidence: speakerPayload.speaker_confidence,
            speaker_backend: speakerPayload.speaker_backend,
            speaker_context_explicit: speakerPayload.speaker_context_explicit
          })
        });
        if (!response.ok) throw new Error(await response.text());
        injectCommand.value = "";
        autoSizeComposer();
        await refresh();
      } catch (error) {
        pendingMessages = pendingMessages.filter((message) => message.text !== cleanedCommand);
        setComposerLocked(false);
        renderMessages(lastServerMessages, false);
        metaEl.textContent = `inject failed: ${error}`;
      }
    }

    async function submitComposerCommand() {
      await unlockWebTtsAudio();
      if (composerLocked && interruptConversationEnabled) {
        const command = injectCommand.value.trim();
        if (command) {
          await submitCommand(command, { interrupt: true });
          return;
        }
      }
      if (composerLocked) {
        await cancelCommand();
        return;
      }
      const command = injectCommand.value.trim();
      if (!command) return;
      if (isStopCommand(command)) {
        await cancelCommand(true);
        injectCommand.value = "";
        autoSizeComposer();
        return;
      }
      await submitCommand(command, { interrupt: interruptConversationEnabled });
    }

    function fileLooksLikeText(file) {
      const name = String(file.name || "").toLowerCase();
      const type = String(file.type || "").toLowerCase();
      return type.startsWith("text/") || name.endsWith(".txt") || name.endsWith(".md");
    }

    function fileLooksLikeWav(file) {
      const name = String(file.name || "").toLowerCase();
      const type = String(file.type || "").toLowerCase();
      return type.includes("wav") || name.endsWith(".wav");
    }

    async function handleComposerFile(file) {
      if (!file) return;
      if (fileLooksLikeText(file)) {
        if (file.size > composerTextUploadMaxBytes) {
          setMeta("text file too large: max 64 KB", "error", 5000);
          return;
        }
        const text = await file.text();
        injectCommand.value = text.trim();
        autoSizeComposer();
        injectCommand.focus({ preventScroll: true });
        setMeta(`loaded text file: ${file.name}`);
        return;
      }
      if (fileLooksLikeWav(file)) {
        if (file.size > composerAudioUploadMaxBytes) {
          setMeta("WAV file too large: max 20 MB", "error", 5000);
          return;
        }
        if (!webAudio.stt_enabled) {
          setMeta("audio file STT unavailable in current web audio configuration", "error", 5000);
          return;
        }
        const applyWakeWord = Boolean((wakeWord.value || "").trim());
        await handleRecordedAudio(file, { applyWakeWord });
        return;
      }
      setMeta("unsupported file type: use WAV audio or TXT text", "error", 5000);
    }

    async function handleRecordedAudio(blob, options = {}) {
      if (!blob || blob.size === 0) return;
      webMic.disabled = true;
      injectCommand.placeholder = "Transcribing...";
      try {
        const audioBase64 = await blobToBase64(blob);
        const response = await fetch("/api/web-transcribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            audio_base64: audioBase64,
            mime_type: blob.type || "audio/webm",
            apply_wake_word: Boolean(options.applyWakeWord)
          })
        });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        if (data.accepted === false) {
          metaEl.textContent = data.message || "voice ignored";
          if (options.conversation) scheduleConversationRestart();
          return;
        }
        const text = String(data.command_text || data.text || "").trim();
        if (text) {
          if (!options.conversation) injectCommand.value = text;
          autoSizeComposer();
          const forcedSpeaker = forcedComposerSpeakerPayload();
          await submitCommand(text, {
            interrupt: Boolean(options.conversation),
            speaker: forcedSpeaker ? forcedSpeaker.speaker : (data.speaker || "unknown"),
            speakerConfidence: forcedSpeaker ? forcedSpeaker.speaker_confidence : (data.speaker_confidence || 0),
            speakerBackend: forcedSpeaker ? forcedSpeaker.speaker_backend : (data.speaker_backend || "none"),
            speakerContextExplicit: forcedSpeaker ? forcedSpeaker.speaker_context_explicit : false
          });
        } else if (options.conversation) {
          scheduleConversationRestart();
        }
      } catch (error) {
        metaEl.textContent = `voice input failed: ${error}`;
      } finally {
        setRecording(false);
      }
    }

    async function startWebRecording() {
      if (!webAudio.stt_enabled || isRecording) return;
      if (composerLocked && !interruptConversationEnabled) return;
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
        metaEl.textContent = "voice input unavailable in this browser";
        return;
      }

      try {
        recordedChunks = [];
        recordingSpeechDetected = false;
        recordingStartedAt = Date.now();
        mediaStream = await navigator.mediaDevices.getUserMedia(browserAudioConstraints());
        loadBrowserAudioDevices(false);
        if (AudioContextClass) {
          recordingAudioContext = new AudioContextClass();
          const source = recordingAudioContext.createMediaStreamSource(mediaStream);
          recordingAnalyser = recordingAudioContext.createAnalyser();
          recordingAnalyser.fftSize = 2048;
          source.connect(recordingAnalyser);
          recordingVad = new BrowserSileroVad({
            onStart: () => {
              recordingSpeechDetected = true;
            },
            onEnd: () => stopWebRecording(),
            onIdle: () => stopWebRecording()
          });
          await recordingVad.attach(recordingAudioContext, source);
        }
        mediaRecorder = new MediaRecorder(mediaStream);
        mediaRecorder.addEventListener("dataavailable", (event) => {
          if (event.data && event.data.size > 0) recordedChunks.push(event.data);
        });
        mediaRecorder.addEventListener("stop", () => {
          const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || "audio/webm" });
          const hadRecordingAnalyser = Boolean(recordingAnalyser);
          stopMediaStream();
          if (hadRecordingAnalyser && !recordingSpeechDetected) {
            injectCommand.placeholder = "Message";
            setRecording(false);
            metaEl.textContent = "voice ignored: not enough speech";
            return;
          }
          injectCommand.placeholder = "Transcribing...";
          handleRecordedAudio(blob);
        });
        mediaRecorder.start();
        setRecording(true);
        startSoundwave();
        const maxRecordingMs = Math.max(1000, Number(webAudio.vad_max_speech_seconds || 8) * 1000 + 1000);
        recordingTimer = window.setTimeout(() => stopWebRecording(), maxRecordingMs);
      } catch (error) {
        stopMediaStream();
        setRecording(false);
        metaEl.textContent = `microphone unavailable: ${error}`;
      }
    }

    function stopWebRecording() {
      if (recordingVad) {
        recordingVad.close();
        recordingVad = null;
      }
      if (mediaRecorder && mediaRecorder.state !== "inactive") {
        clearRecordingTimer();
        webMic.disabled = true;
        mediaRecorder.stop();
      }
    }

    function clearConversationRestartTimer() {
      if (conversationRestartTimer) {
        window.clearTimeout(conversationRestartTimer);
        conversationRestartTimer = null;
      }
    }

    function stopConversationMonitor() {
      if (conversationMonitorId) {
        window.cancelAnimationFrame(conversationMonitorId);
        conversationMonitorId = null;
      }
    }

    function stopConversationStream() {
      stopConversationMonitor();
      stopSoundwave();
      if (conversationVad) {
        conversationVad.close();
        conversationVad = null;
      }
      if (conversationAudioContext) {
        conversationAudioContext.close().catch(() => {});
        conversationAudioContext = null;
      }
      if (conversationStream) {
        for (const track of conversationStream.getTracks()) {
          track.stop();
        }
      }
      conversationStream = null;
      conversationAnalyser = null;
      conversationRecorder = null;
    }

    function stopConversationSegment() {
      stopConversationMonitor();
      stopSoundwave();
      if (conversationVad) {
        conversationVad.close();
        conversationVad = null;
      }
      conversationRecorder = null;
    }

    function stopConversationRecording(discard = false, closeStream = false) {
      conversationDiscard = discard;
      conversationStopStreamAfterSegment = Boolean(closeStream);
      if (conversationVad) {
        conversationVad.close();
        conversationVad = null;
      }
      if (conversationRecorder && conversationRecorder.state !== "inactive") {
        conversationRecorder.stop();
      } else {
        if (conversationStopStreamAfterSegment) stopConversationStream();
        else stopConversationSegment();
        conversationStopStreamAfterSegment = false;
      }
    }

    async function attachConversationVad() {
      if (!conversationAudioContext || !conversationStream || conversationVad) return;
      const source = conversationAudioContext.createMediaStreamSource(conversationStream);
      conversationVad = new BrowserSileroVad({
        onStart: () => {
          conversationSpeechDetected = true;
        },
        onEnd: () => stopConversationRecording(false),
        onIdle: () => stopConversationRecording(true)
      });
      await conversationVad.attach(conversationAudioContext, source);
    }

    async function ensureConversationMicrophone() {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder || !AudioContextClass) {
        throw new Error("conversation mode unavailable in this browser");
      }

      const hasLiveStream = conversationStream
        && conversationStream.getAudioTracks().some((track) => track.readyState === "live");
      if (hasLiveStream && conversationAudioContext && conversationAnalyser) {
        if (conversationAudioContext.state === "suspended") {
          await conversationAudioContext.resume();
        }
        await attachConversationVad();
        return;
      }

      stopConversationStream();
      conversationStream = await navigator.mediaDevices.getUserMedia(browserAudioConstraints());
      loadBrowserAudioDevices(false);
      conversationAudioContext = new AudioContextClass();
      const source = conversationAudioContext.createMediaStreamSource(conversationStream);
      conversationAnalyser = conversationAudioContext.createAnalyser();
      conversationAnalyser.fftSize = 2048;
      source.connect(conversationAnalyser);
      await attachConversationVad();
    }

    async function startConversationListening() {
      if (!conversationEnabled || !webAudio.stt_enabled || conversationRecorder) return;
      if ((composerLocked || webTtsPlaying) && !interruptConversationEnabled) return;

      try {
        await ensureConversationMicrophone();
        conversationChunks = [];
        conversationSpeechDetected = false;
        conversationDiscard = false;
        conversationStopStreamAfterSegment = false;
        conversationRecorder = new MediaRecorder(conversationStream);
        conversationRecorder.addEventListener("dataavailable", (event) => {
          if (event.data && event.data.size > 0) conversationChunks.push(event.data);
        });
        conversationRecorder.addEventListener("stop", () => {
          const shouldDiscard = conversationDiscard || !conversationSpeechDetected;
          const blob = new Blob(conversationChunks, { type: conversationRecorder.mimeType || "audio/webm" });
          const shouldCloseStream = conversationStopStreamAfterSegment;
          if (shouldCloseStream) stopConversationStream();
          else stopConversationSegment();
          conversationStopStreamAfterSegment = false;
          if (shouldCloseStream) return;
          if (!shouldDiscard) {
            handleRecordedAudio(blob, { applyWakeWord: true, conversation: true }).finally(() => {
              scheduleConversationRestart();
            });
          } else {
            scheduleConversationRestart();
          }
        });
        conversationRecorder.start();
        startSoundwave();
        metaEl.textContent = "conversation listening...";
      } catch (error) {
        stopConversationStream();
        metaEl.textContent = `conversation microphone unavailable: ${error}`;
        conversationEnabled = false;
        updateConversationButton();
      }
    }

    function scheduleConversationRestart(delayMs = 250) {
      clearConversationRestartTimer();
      if (!conversationEnabled) return;
      if ((composerLocked || webTtsPlaying) && !interruptConversationEnabled) return;
      conversationRestartTimer = window.setTimeout(() => startConversationListening(), delayMs);
    }

    function setConversationEnabled(enabled) {
      conversationEnabled = Boolean(enabled);
      updateConversationButton();
      if (conversationEnabled) {
        if (isRecording) stopWebRecording();
        scheduleConversationRestart(0);
      } else {
        clearConversationRestartTimer();
        stopConversationRecording(true, true);
        stopConversationStream();
      }
    }

    async function playWebTts(text, options = {}) {
      const force = options.force === true;
      if ((!force && !webAudio.tts_enabled) || !text) return;
      try {
        webTtsPlaying = true;
        const payload = { text };
        if (options.provider) payload.provider = options.provider;
        if (options.model) payload.model = options.model;
        if (options.voice) payload.voice = options.voice;
        if (options.speed) payload.speed = options.speed;
        const response = await fetch("/api/web-tts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        if (!response.ok) {
          const responseText = await response.text();
          throw new Error(responseText);
        }
        const data = await response.json();
        if (!data.audio_base64 || !data.mime_type) return;
        try {
          await playWebTtsElement(data.audio_base64, data.mime_type, options.volume);
        } catch (audioElementError) {
          const playedWithContext = await playWebTtsBuffer(data.audio_base64, options.volume);
          if (!playedWithContext) throw audioElementError;
        }
      } catch (error) {
        setMeta(conciseClientTtsError(error), "error", 12000);
        if (force) throw error;
      } finally {
        currentWebTtsSource = null;
        currentWebTtsAudio = null;
        webTtsPlaying = false;
        scheduleConversationRestart(250);
      }
    }

    function stopWebTts() {
      if (currentWebTtsSource) {
        try {
          currentWebTtsSource.stop(0);
        } catch (error) {}
        currentWebTtsSource = null;
      }
      if (currentWebTtsAudio) {
        const audio = currentWebTtsAudio;
        currentWebTtsAudio.pause();
        currentWebTtsAudio.currentTime = 0;
        currentWebTtsAudio = null;
        audio.dispatchEvent(new Event("ended"));
      }
      webTtsPlaying = false;
    }

    async function startThinkingAudio() {
      if (!webAudio.tts_enabled || webAudio.tts_output !== "browser" || !thinkingAudioUrl || thinkingAudioPlaying) return;
      try {
        if (!thinkingAudio || thinkingAudio.src !== new URL(thinkingAudioUrl, window.location.href).href) {
          thinkingAudio = new Audio(thinkingAudioUrl);
          thinkingAudio.loop = true;
        }
        thinkingAudio.volume = Math.max(0, Math.min(1, Number(webAudio.tts_volume ?? 1)));
        await applyBrowserAudioOutput(thinkingAudio);
        thinkingAudioPlaying = true;
        thinkingAudio.currentTime = 0;
        await thinkingAudio.play();
      } catch (error) {
        thinkingAudioPlaying = false;
      }
    }

    function stopThinkingAudio() {
      if (!thinkingAudio) {
        thinkingAudioPlaying = false;
        return;
      }
      thinkingAudio.pause();
      thinkingAudio.currentTime = 0;
      thinkingAudioPlaying = false;
    }

    async function cancelCommand(force = false) {
      stopWebTts();
      if ((!force && !composerLocked) || cancelRequestInFlight) return;
      cancelRequestInFlight = true;
      injectStop.disabled = true;
      injectCommand.placeholder = "Cancelling...";
      try {
        const response = await fetch("/api/cancel-command", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({})
        });
        if (!response.ok) throw new Error(await response.text());
        await refresh();
      } catch (error) {
        metaEl.textContent = `cancel failed: ${error}`;
      } finally {
        cancelRequestInFlight = false;
        setComposerLocked(composerLocked);
      }
    }

    function renderMessages(serverMessages, showThinking = false) {
      const knownUserMessages = (serverMessages || []).filter((message) => message.role === "user");
      pendingMessages = pendingMessages.filter((pending) => {
        return !knownUserMessages.some((message) => {
          const serverTime = Number(message.created_at || 0) * 1000;
          return message.text === pending.text && serverTime >= pending.sentAt - 1000;
        });
      });

      const rows = [...withContextPreview(serverMessages || []), ...pendingMessages];
      const shouldStick = chatPanel.scrollTop + chatPanel.clientHeight >= chatPanel.scrollHeight - 24;
      if (rows.length === 0) {
        messagesEl.innerHTML = `<div class="empty-state">Live Stage Assistant</div>`;
      } else {
        messagesEl.innerHTML = rows.map(messageBubble).join("") + (showThinking ? thinkingBubble() : "");
      }
      if (shouldStick) {
        chatPanel.scrollTop = chatPanel.scrollHeight;
      }
    }

    function option(label, value, disabled, selected) {
      const opt = document.createElement("option");
      opt.textContent = label;
      opt.value = value;
      opt.disabled = Boolean(disabled);
      opt.selected = Boolean(selected);
      return opt;
    }

    function configSignature() {
      return JSON.stringify({
        env_profile: activeEnvProfile,
        connectivity_mode: selectedConnectivityMode(),
        provider: llmProvider.value || "",
        model: llmModel.value || "",
        session_context_size: Number(sessionContextSize.value || 0),
        mcp_agent_max_steps: Number(mcpAgentMaxSteps.value || 20),
        mcp_tool_routing_enabled: selectedMcpToolRoutingEnabled(),
        interrupt_conversation_enabled: selectedInterruptConversationEnabled(),
        wake_word: wakeWord.value.trim(),
        stt_prompt: sttPromptEl.value.trim(),
        system_prompt: assistantSystemPromptEl.value.trim(),
        cloud_tts_provider: cloudTtsProvider.value || "",
        tts_output: selectedTtsOutput(),
        stt_input: selectedSttInput(),
        backend_audio_input_device: backendAudioInput.value || "",
        backend_audio_output_device: backendAudioOutput.value || "",
        backend_audio_output_pan: Number(backendAudioOutputPan.value || 0),
        backend_audio_monitor_mode: selectedBackendAudioMonitorMode(),
        backend_audio_monitor_volume: Number(backendAudioMonitorVolume.value || 1),
        voice_id: elevenlabsVoice.value || "",
        thinking_sound_file: thinkingSound.value || "",
        command_ack_sound_enabled: commandAckSound.getAttribute("aria-checked") === "true",
        openai_tts_voice: openaiTtsVoice.value || "",
        openai_tts_speed: Number(openaiTtsSpeed.value || 1),
        web_tts_volume: Number(webTtsVolume.value || 1),
        backend_tts_volume: Number(backendTtsVolume.value || 1),
        vad_speech_threshold: Number(vadSpeechThreshold.value || 0.5),
        vad_negative_threshold: Number(vadNegativeThreshold.value || 0.35),
        vad_min_speech_ms: Number(vadMinSpeechMs.value || 120),
        vad_min_silence_ms: Number(vadMinSilenceMs.value || 650),
        vad_speech_pad_ms: Number(vadSpeechPadMs.value || 100),
        vad_max_speech_seconds: Number(vadMaxSpeechSeconds.value || 8),
        speaker_recognition_enabled: selectedSpeakerRecognitionEnabled(),
        speaker_backend: speakerBackend.value || "resemblyzer",
        speaker_threshold: Number(speakerThreshold.value || 0.75),
        speaker_margin: Number(speakerMargin.value || 0.10),
        speaker_profiles: collectSpeakerProfiles()
      });
    }

    function selectedInterruptConversationEnabled() {
      const selected = interruptConversationInputs.find((input) => input.checked);
      return selected ? selected.value === "on" : false;
    }

    function markConfigClean() {
      configBaseline = configSignature();
    }

    function hasUnsavedConfigChanges() {
      return Boolean(configBaseline) && configSignature() !== configBaseline;
    }

    async function loadEnvProfiles() {
      if (envProfilesLoading) return false;
      envProfilesLoading = true;
      let profileChanged = false;
      try {
        const response = await fetch("/api/env-profiles", { cache: "no-store" });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        const current = data.current || "";
        profileChanged = Boolean(activeEnvProfile) && current && current !== activeEnvProfile;
        activeEnvProfile = current;
        envProfile.replaceChildren();
        for (const profile of data.profiles || []) {
          envProfile.appendChild(option(profile.label || profile.id, profile.id, false, profile.id === current || profile.selected));
        }
        if (current && envProfile.value !== current) {
          envProfile.value = current;
        }
        envProfileSwitchingEnabled = data.switching_enabled !== false;
        connectivityLocked = data.connectivity_locked === true;
        connectivityAutoBadge.classList.toggle("hidden", data.auto_mode !== true);
        envProfile.disabled = !envProfileSwitchingEnabled || envProfile.options.length <= 1;
        if (data.message && !llmMessage.textContent) {
          llmMessage.textContent = data.message;
        }
      } catch (error) {
        envProfile.replaceChildren(option("Env profiles unavailable", "", true, true));
        envProfile.disabled = true;
        connectivityAutoBadge.classList.add("hidden");
        llmMessage.textContent = `Env profiles unavailable: ${error}`;
      } finally {
        envProfilesLoading = false;
      }
      return profileChanged;
    }

    async function switchEnvProfile(nextEnvProfile) {
      if (!nextEnvProfile || nextEnvProfile === activeEnvProfile) {
        envProfile.value = activeEnvProfile;
        return;
      }
      if (hasUnsavedConfigChanges()) {
        const confirmed = window.confirm("Unsaved config changes will be discarded. Switch env profile anyway?");
        if (!confirmed) {
          envProfile.value = activeEnvProfile;
          return;
        }
      }

      envProfile.disabled = true;
      llmSave.disabled = true;
      stopBrowserAudioTest();
      stopBackendAudioTest();
      setEnvironmentLoading(true);
      disconnectVnc("reconnexion VNC...");
      llmMessage.textContent = `Switching to ${nextEnvProfile}...`;
      try {
        const response = await fetch("/api/env-profile", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ env_file: nextEnvProfile })
        });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        activeEnvProfile = data.env_file || nextEnvProfile;
        llmControlsInitialized = false;
        configBaseline = "";
        llmMessage.textContent = data.message || "Env profile switched.";
        await loadEnvProfiles();
        await loadLlmOptions("", "");
        markConfigClean();
        await refresh();
      } catch (error) {
        setEnvironmentLoading(false);
        envProfile.value = activeEnvProfile;
        llmMessage.textContent = `Env switch failed: ${error}`;
        connectVnc({ force: true });
      } finally {
        envProfile.disabled = !envProfileSwitchingEnabled || envProfile.options.length <= 1;
        llmSave.disabled = !llmProvider.value;
      }
    }

    function syncOpenAiSpeedLabel() {
      openaiTtsSpeedLabel.textContent = `${Number(openaiTtsSpeed.value || 1).toFixed(2)}x`;
    }

    function syncTtsVolumeLabels() {
      webTtsVolumeLabel.textContent = `${Math.round(Number(webTtsVolume.value || 1) * 100)}%`;
      backendTtsVolumeLabel.textContent = `${Math.round(Number(backendTtsVolume.value || 1) * 100)}%`;
    }

    function syncBackendAudioMonitorVolumeLabel() {
      backendAudioMonitorVolumeLabel.textContent = `${Math.round(Number(backendAudioMonitorVolume.value || 1) * 100)}%`;
    }

    function syncBackendAudioOutputPanLabel() {
      const value = Number(backendAudioOutputPan.value || 0);
      if (Math.abs(value) < 0.025) {
        backendAudioOutputPanLabel.textContent = "Centre";
      } else if (value < 0) {
        backendAudioOutputPanLabel.textContent = `Gauche ${Math.round(Math.abs(value) * 100)}%`;
      } else {
        backendAudioOutputPanLabel.textContent = `Droite ${Math.round(value * 100)}%`;
      }
    }

    function selectedBackendAudioMonitorMode() {
      const checked = backendAudioMonitorModeInputs.find((input) => input.checked);
      return checked ? checked.value : "off";
    }

    function setSelectedBackendAudioMonitorMode(value) {
      const nextValue = ["rejected", "passthrough", "off"].includes(value) ? value : "off";
      for (const input of backendAudioMonitorModeInputs) {
        input.checked = input.value === nextValue;
      }
    }

    function setCommandAckSoundEnabled(enabled) {
      commandAckSound.setAttribute("aria-checked", enabled ? "true" : "false");
      commandAckSound.textContent = enabled ? "On" : "Off";
      commandAckSound.classList.toggle("enabled", enabled);
    }

    function commandAckSoundEnabled() {
      return commandAckSound.getAttribute("aria-checked") === "true";
    }

    function syncVadLabels() {
      vadSpeechThresholdLabel.textContent = Number(vadSpeechThreshold.value || 0.5).toFixed(2);
      vadNegativeThresholdLabel.textContent = Number(vadNegativeThreshold.value || 0.35).toFixed(2);
      vadMinSpeechMsLabel.textContent = `${Number(vadMinSpeechMs.value || 120)} ms`;
      vadMinSilenceMsLabel.textContent = `${Number(vadMinSilenceMs.value || 650)} ms`;
      vadSpeechPadMsLabel.textContent = `${Number(vadSpeechPadMs.value || 100)} ms`;
      vadMaxSpeechSecondsLabel.textContent = `${Number(vadMaxSpeechSeconds.value || 8).toFixed(1)} s`;
    }

    function setVadControls(data) {
      vadSpeechThreshold.value = String(data.selected_vad_speech_threshold ?? 0.5);
      vadNegativeThreshold.value = String(data.selected_vad_negative_threshold ?? 0.35);
      vadMinSpeechMs.value = String(data.selected_vad_min_speech_ms ?? 120);
      vadMinSilenceMs.value = String(data.selected_vad_min_silence_ms ?? 650);
      vadSpeechPadMs.value = String(data.selected_vad_speech_pad_ms ?? 100);
      vadMaxSpeechSeconds.value = String(data.selected_vad_max_speech_seconds ?? 8);
      syncVadLabels();
    }

    function selectedSpeakerRecognitionEnabled() {
      if (speakerRecognitionUnavailableReason) {
        return speakerRecognitionEnvEnabled;
      }
      const selected = speakerRecognitionInputs.find((input) => input.checked);
      return selected ? selected.value === "on" : false;
    }

    function setSpeakerRecognitionEnabled(enabled) {
      speakerRecognitionInputs.forEach((input) => {
        input.checked = enabled ? input.value === "on" : input.value === "off";
      });
    }

    function speakerRuntimeUnavailableReason(data) {
      const runtime = data.speaker_recognition_runtime || {};
      if (runtime.requested && !runtime.enabled && runtime.unavailable_reason) {
        return `Speaker recognition unavailable: ${runtime.unavailable_reason}`;
      }
      return "";
    }

    function setSpeakerControlsDisabled(disabled, reason = "") {
      if (speakerRecognitionGroup) {
        speakerRecognitionGroup.classList.toggle("disabled", disabled);
        speakerRecognitionGroup.title = disabled ? reason : "";
      }
      const controls = [
        ...speakerRecognitionInputs,
        speakerBackend,
        speakerThreshold,
        speakerMargin,
        ...speakerProfileGrid.querySelectorAll("input, button")
      ];
      for (const control of controls) {
        control.disabled = Boolean(disabled);
        control.title = disabled ? reason : "";
        const label = control.closest("label");
        if (label) label.title = disabled ? reason : "";
      }
    }

    function syncSpeakerLabels() {
      speakerThresholdLabel.textContent = Number(speakerThreshold.value || 0.75).toFixed(2);
      speakerMarginLabel.textContent = Number(speakerMargin.value || 0.10).toFixed(2);
    }

    function renderSpeakerProfiles(profiles) {
      speakerProfileGrid.replaceChildren();
      const rows = profiles && profiles.length ? profiles : Array.from({ length: 5 }, (_, index) => ({ index: index + 1 }));
      for (let index = 1; index <= 5; index += 1) {
        const profile = rows.find((item) => Number(item.index) === index) || { index };
        const row = document.createElement("div");
        row.className = "speaker-profile-row";
        row.dataset.index = String(index);

        const name = document.createElement("input");
        name.type = "text";
        name.placeholder = `speaker ${index}`;
        name.value = profile.name || `speaker_${index}`;
        name.dataset.role = "name";
        name.addEventListener("input", () => {
          speakerProfileChoices = activeSpeakerProfilesFromConfig();
          syncComposerSpeakerControl();
        });

        const uploadWrap = document.createElement("div");
        uploadWrap.className = "speaker-upload";
        const samples = Array.isArray(profile.samples) && profile.samples.length
          ? profile.samples
          : Array.from({ length: 3 }, (_, sampleOffset) => ({
              index: sampleOffset + 1,
              filename: `profil${index}_${sampleOffset + 1}.wav`,
              ready: false
            }));
        for (let sampleIndex = 1; sampleIndex <= 3; sampleIndex += 1) {
          const sample = samples.find((item) => Number(item.index) === sampleIndex) || { index: sampleIndex };
          const sampleWrap = document.createElement("label");
          sampleWrap.className = `speaker-sample${sample.ready ? " ready" : ""}`;
          sampleWrap.title = sample.wav_path || `data/speaker_profiles/profil${index}_${sampleIndex}.wav`;
          const led = document.createElement("span");
          led.className = "speaker-sample-led";
          led.setAttribute("aria-hidden", "true");
          const file = document.createElement("input");
          file.type = "file";
          file.accept = ".wav,audio/wav,audio/x-wav";
          file.dataset.role = "file";
          file.dataset.sampleIndex = String(sampleIndex);
          file.setAttribute("aria-label", trf("speaker_sample_upload", "Sample {index}", { index: sampleIndex }));
          file.addEventListener("change", () => {
            if (file.files && file.files[0]) uploadSpeakerProfile(row, sampleIndex);
          });
          sampleWrap.append(led, file);
          uploadWrap.append(sampleWrap);
        }

        const enabledLabel = document.createElement("label");
        const enabled = document.createElement("input");
        enabled.type = "checkbox";
        enabled.checked = Boolean(profile.enabled);
        enabled.dataset.role = "enabled";
        enabled.addEventListener("change", () => {
          speakerProfileChoices = activeSpeakerProfilesFromConfig();
          syncComposerSpeakerControl();
        });
        enabledLabel.appendChild(enabled);
        enabledLabel.append(" actif");

        const status = document.createElement("span");
        status.className = "speaker-status";
        status.textContent = profile.status || tr("speaker_samples_missing", "0/3 samples");

        const path = document.createElement("span");
        path.className = "speaker-path";
        path.dataset.role = "path";
        path.title = profile.embedding_ready && profile.embedding_path ? profile.embedding_path : "";
        path.textContent = profile.embedding_ready
          ? tr("speaker_voiceprint_ready", "empreinte vocale calculée")
          : tr("speaker_voiceprint_unavailable", "empreinte vocale indisponible");

        row.append(name, uploadWrap, enabledLabel, status, path);
        speakerProfileGrid.appendChild(row);
      }
    }

    function readFileAsDataUrl(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.addEventListener("load", () => resolve(String(reader.result || "")));
        reader.addEventListener("error", () => reject(reader.error || new Error("File read failed")));
        reader.readAsDataURL(file);
      });
    }

    async function uploadSpeakerProfile(row, sampleIndex) {
      const nameInput = row.querySelector('[data-role="name"]');
      const pathLabel = row.querySelector('[data-role="path"]');
      const fileInput = row.querySelector(`[data-role="file"][data-sample-index="${sampleIndex}"]`);
      const enabledInput = row.querySelector('[data-role="enabled"]');
      const status = row.querySelector(".speaker-status");
      const profileName = nameInput.value.trim();
      const file = fileInput.files && fileInput.files[0];
      if (!profileName) {
        status.textContent = tr("speaker_name_required", "name required");
        return;
      }
      if (!file) {
        status.textContent = tr("speaker_choose_wav", "choose wav");
        return;
      }
      if (!file.name.toLowerCase().endsWith(".wav")) {
        status.textContent = tr("speaker_wav_only", "wav only");
        return;
      }
      status.textContent = tr("speaker_embedding_progress", "embedding...");
      setProfileLoading(true, tr("preparing_voice_profile", "Préparation du profil vocal"), speakerEmbeddingPreparationMessage);
      if (webAudio.tts_enabled && webAudio.tts_output === "browser") {
        playWebTts(speakerEmbeddingPreparationMessage);
      }
      try {
        const dataUrl = await readFileAsDataUrl(file);
        const response = await fetch("/api/speaker-profile-upload", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            profile_name: profileName,
            profile_index: Number(row.dataset.index || 0),
            sample_index: sampleIndex,
            filename: file.name,
            audio_base64: dataUrl
          })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
          throw new Error(data.error?.message || data.message || response.statusText || "Upload failed");
        }
        if (pathLabel) {
          pathLabel.textContent = data.embedding_ready
            ? tr("speaker_voiceprint_ready", "empreinte vocale calculée")
            : tr("speaker_voiceprint_unavailable", "empreinte vocale indisponible");
          pathLabel.title = data.embedding_ready && data.embedding_path ? data.embedding_path : "";
        }
        for (const sample of data.samples || []) {
          const input = row.querySelector(`[data-role="file"][data-sample-index="${sample.index}"]`);
          const sampleWrap = input ? input.closest(".speaker-sample") : null;
          if (sampleWrap) {
            sampleWrap.classList.toggle("ready", Boolean(sample.ready));
            sampleWrap.title = sample.wav_path || sample.filename || "";
          }
        }
        enabledInput.checked = true;
        speakerProfileChoices = activeSpeakerProfilesFromConfig();
        syncComposerSpeakerControl();
        status.textContent = data.embedding_status || data.status || "ready";
        fileInput.value = "";
        const generated = String(data.embedding_status || "").toLowerCase().includes("ready");
        setProfileLoading(
          true,
          generated ? tr("speaker_profile_done", "Génération du profil faite") : tr("speaker_profile_file_saved", "Fichier profil sauvegardé"),
          generated
            ? trf("speaker_profile_ready_detail", "Profil {index} prêt: {path}", {
                index: row.dataset.index || "",
                path: data.embedding_path || ""
              })
            : trf("speaker_profile_pending_detail", "{message} {status} Le WAV est sauvegardé.", {
                message: speakerEmbeddingPreparationMessage,
                status: data.embedding_status || tr("speaker_embedding_not_generated", "Embedding non généré pour l'instant.")
              }),
          "done"
        );
        window.setTimeout(() => setProfileLoading(false), 1400);
      } catch (error) {
        status.textContent = tr("speaker_upload_failed", "upload failed");
        setProfileLoading(
          true,
          tr("profile_generation_error", "Erreur génération profil"),
          String(error.message || error),
          "error"
        );
        window.setTimeout(() => setProfileLoading(false), 2600);
      }
    }

    function collectSpeakerProfiles() {
      return Array.from(speakerProfileGrid.querySelectorAll(".speaker-profile-row")).map((row) => ({
        index: Number(row.dataset.index || 0),
        name: row.querySelector('[data-role="name"]').value.trim(),
        enabled: row.querySelector('[data-role="enabled"]').checked
      }));
    }

    function setSpeakerControls(data) {
      const unavailableReason = speakerRuntimeUnavailableReason(data);
      const runtime = data.speaker_recognition_runtime || {};
      speakerRecognitionEnvEnabled = Boolean(data.selected_speaker_recognition_enabled);
      speakerRecognitionRuntimeEnabled = Boolean(runtime.enabled);
      speakerRecognitionUnavailableReason = unavailableReason;
      setSpeakerRecognitionEnabled(unavailableReason ? false : speakerRecognitionEnvEnabled);
      speakerBackend.value = data.selected_speaker_backend || "resemblyzer";
      speakerThreshold.value = String(data.selected_speaker_threshold ?? 0.75);
      speakerMargin.value = String(data.selected_speaker_margin ?? 0.10);
      renderSpeakerProfiles(data.speaker_profiles || []);
      speakerProfileChoices = activeSpeakerProfilesFromConfig();
      syncComposerSpeakerControl();
      setSpeakerControlsDisabled(Boolean(unavailableReason), unavailableReason);
      syncSpeakerLabels();
    }

    function applyVadPreset(name) {
      const preset = vadPresets[name];
      if (!preset) return;
      vadSpeechThreshold.value = String(preset.vadSpeechThreshold);
      vadNegativeThreshold.value = String(preset.vadNegativeThreshold);
      vadMinSpeechMs.value = String(preset.vadMinSpeechMs);
      vadMinSilenceMs.value = String(preset.vadMinSilenceMs);
      vadSpeechPadMs.value = String(preset.vadSpeechPadMs);
      vadMaxSpeechSeconds.value = String(preset.vadMaxSpeechSeconds);
      syncVadLabels();
      llmMessage.textContent = tr("stt_example_applied", "STT example applied. Save to persist.");
    }

    async function testSelectedTtsVoice() {
      const provider = cloudTtsProvider.value || "none";
      if (!["openai", "elevenlabs"].includes(provider)) return;
      const voice = provider === "elevenlabs" ? elevenlabsVoice.value : openaiTtsVoice.value;
      const speed = Number(openaiTtsSpeed.value || 1);
      const volume = Number(webTtsVolume.value || 1);
      const backendVolume = Number(backendTtsVolume.value || 1);
      const backendPan = Number(backendAudioOutputPan.value || 0);
      const output = selectedTtsOutput();
      ttsTest.disabled = true;
      llmMessage.textContent = tr("testing_voice", "Testing voice...");
      try {
        if (output === "backend") {
          const response = await fetch("/api/backend-tts-test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              text: ttsTestPhrase,
              provider,
              model: provider === "openai" ? "gpt-4o-mini-tts" : "",
              voice,
              speed,
              volume: backendVolume,
              pan: backendPan,
              output_device: backendAudioOutput.value || ""
            })
          });
          if (!response.ok) {
            const text = await response.text();
            try {
              const data = JSON.parse(text);
              throw new Error(data.error?.message || text);
            } catch (parseError) {
              if (parseError instanceof SyntaxError) throw new Error(text);
              throw parseError;
            }
          }
        } else if (output === "browser") {
          await playWebTts(ttsTestPhrase, {
            force: true,
            provider,
            model: provider === "openai" ? "gpt-4o-mini-tts" : "",
            voice,
            speed,
            volume
          });
        } else {
          throw new Error("TTS Output is Silent");
        }
        llmMessage.textContent = tr("voice_test_played", "Voice test played.");
      } catch (error) {
        llmMessage.textContent = trf("voice_test_failed", "Voice test failed: {error}", { error });
      } finally {
        syncTtsProviderControls();
      }
    }

    function syncAudioDeviceVisibility() {
      const output = selectedTtsOutput();
      const sttInput = selectedSttInput();
      syncBackendAudioMonitorControls();
      const backendOutputNeeded = output === "backend" || selectedBackendAudioMonitorMode() !== "off";
      browserAudioInputField.classList.toggle("hidden", !["both", "browser"].includes(sttInput));
      backendAudioInputField.classList.toggle("hidden", !["both", "backend"].includes(sttInput));
      browserAudioOutputField.classList.toggle("hidden", output !== "browser");
      backendAudioOutputField.classList.toggle("hidden", !backendOutputNeeded);
      backendAudioOutputPanField.classList.toggle("hidden", !backendOutputNeeded);
      if (browserAudioInputField.classList.contains("hidden") && browserAudioTestStream) stopBrowserAudioTest();
      if (backendAudioInputField.classList.contains("hidden") && backendAudioTestTimer) stopBackendAudioTest();
      browserAudioTest.disabled = browserAudioInputField.classList.contains("hidden") || !browserAudioCapabilities.input;
      backendAudioTest.disabled = backendAudioInputField.classList.contains("hidden") || !backendAudioCapabilities.input;
      backendAudioOutputPan.disabled = backendAudioOutputPanField.classList.contains("hidden") || !backendAudioCapabilities.output;
      backendAudioOutputPanField.title = backendAudioOutputPan.disabled
        ? "BACKEND_AUDIO_OUTPUT_PAN - actif avec TTS Output Backend ou le monitoring backend actif"
        : "BACKEND_AUDIO_OUTPUT_PAN";
    }

    function backendAudioMonitorModeAvailable(value) {
      if (value === "off") return true;
      if (!backendAudioCapabilities.input || !backendAudioCapabilities.output) return false;
      if (value === "rejected" && !wakeWord.value.trim()) return false;
      return value === "rejected" || value === "passthrough";
    }

    function backendAudioMonitorModeReason(value) {
      if (value === "off") return "";
      if (!backendAudioCapabilities.input || !backendAudioCapabilities.output) {
        return "BACKEND_AUDIO_MONITOR_MODE nécessite une entrée et une sortie audio backend disponibles.";
      }
      if (value === "rejected" && !wakeWord.value.trim()) {
        return "BACKEND_AUDIO_MONITOR_MODE=rejected nécessite un wake word actif.";
      }
      return "";
    }

    function syncBackendAudioMonitorControls() {
      for (const input of backendAudioMonitorModeInputs) {
        setSegmentOptionEnabled(input, backendAudioMonitorModeAvailable(input.value), backendAudioMonitorModeReason(input.value));
      }
      if (!backendAudioMonitorModeAvailable(selectedBackendAudioMonitorMode())) {
        setSelectedBackendAudioMonitorMode("off");
      }
      const mode = selectedBackendAudioMonitorMode();
      const enabled = mode !== "off" && backendAudioMonitorModeAvailable(mode);
      backendAudioMonitorVolume.disabled = !enabled;
      backendAudioMonitorModeField.title = backendAudioCapabilities.input && backendAudioCapabilities.output
        ? "BACKEND_AUDIO_MONITOR_MODE"
        : "BACKEND_AUDIO_MONITOR_MODE - nécessite une entrée et une sortie backend disponibles";
      backendAudioMonitorVolumeField.title = enabled
        ? "BACKEND_AUDIO_MONITOR_VOLUME"
        : "BACKEND_AUDIO_MONITOR_VOLUME - actif seulement avec Rejected ou Pass through";
    }

    function syncCommandAckSoundControls() {
      commandAckSound.disabled = !backendAudioCapabilities.output && !webAudio.tts_enabled;
      commandAckSoundField.title = commandAckSound.disabled
        ? "COMMAND_ACK_SOUND_ENABLED - nécessite une sortie audio backend ou TTS navigateur disponible"
        : "COMMAND_ACK_SOUND_ENABLED";
    }

    function setSegmentOptionEnabled(input, enabled, reason = "") {
      const label = input.closest("label");
      if (label) {
        label.classList.remove("hidden");
        label.title = enabled ? "" : reason;
      }
      input.disabled = !enabled;
      input.title = enabled ? "" : reason;
    }

    function sttInputAvailable(value) {
      if (value === "silent") return true;
      if (value === "browser") return browserAudioCapabilities.input;
      if (value === "backend") return backendAudioCapabilities.input;
      if (value === "both") return browserAudioCapabilities.input && backendAudioCapabilities.input;
      return false;
    }

    function sttInputUnavailableReason(value) {
      const offline = selectedConnectivityMode() === "offline";
      if (offline && value === "browser") return "Le mode offline utilise uniquement l'entrée micro backend.";
      if (offline && value === "both") return "Le mode offline ne combine pas micro navigateur et micro backend.";
      if (value === "browser") return "Aucun micro navigateur n'est disponible. Autorise le micro puis clique Refresh, ou ouvre l'app en HTTPS/localhost.";
      if (value === "backend") return "Aucune entrée audio backend n'est détectée par PyAudio.";
      if (value === "both") return "Both nécessite à la fois un micro navigateur et une entrée audio backend.";
      return "";
    }

    function firstAvailableSttInput(preferred) {
      if (sttInputAvailable(preferred)) return preferred;
      const fallbacks = preferred === "both"
        ? ["browser", "backend", "silent"]
        : ["both", "browser", "backend", "silent"];
      return fallbacks.find(sttInputAvailable) || "silent";
    }

    function ttsOutputAvailable(value) {
      if (value === "silent") return true;
      if (value === "browser") return browserAudioCapabilities.output;
      if (value === "backend") return backendAudioCapabilities.output;
      return false;
    }

    function ttsOutputUnavailableReason(value) {
      const offline = selectedConnectivityMode() === "offline";
      const provider = cloudTtsProvider.value || "none";
      if (offline && value === "browser") return "Le mode offline force la sortie locale backend ou Silent.";
      if (!offline && provider === "none" && value !== "silent") return "Sélectionne un fournisseur TTS cloud pour activer cette sortie.";
      if (value === "backend") return "Aucune sortie audio backend n'est détectée par PyAudio.";
      if (value === "browser") return "Le navigateur ne permet pas la lecture audio.";
      return "";
    }

    function firstAvailableTtsOutput(preferred) {
      if (ttsOutputAvailable(preferred)) return preferred;
      const fallbacks = preferred === "browser"
        ? ["backend", "silent"]
        : ["browser", "silent"];
      return fallbacks.find(ttsOutputAvailable) || "silent";
    }

    function syncAudioCapabilityControls() {
      const offline = selectedConnectivityMode() === "offline";
      const provider = cloudTtsProvider.value || "none";
      const forceSilentTts = !offline && provider === "none";
      for (const input of sttInputInputs) {
        const enabled = sttInputAvailable(input.value) && (!offline || input.value === "backend" || input.value === "silent");
        setSegmentOptionEnabled(input, enabled, sttInputUnavailableReason(input.value));
      }
      setSelectedSttInput(offline ? firstAvailableSttInput("backend") : firstAvailableSttInput(selectedSttInput()));

      for (const input of ttsOutputInputs) {
        const enabled = ttsOutputAvailable(input.value)
          && (!forceSilentTts || input.value === "silent")
          && (!offline || input.value === "backend" || input.value === "silent");
        setSegmentOptionEnabled(input, enabled, ttsOutputUnavailableReason(input.value));
      }
      setSelectedTtsOutput(
        offline ? (ttsOutputAvailable("backend") ? "backend" : "silent")
          : (forceSilentTts ? "silent" : firstAvailableTtsOutput(selectedTtsOutput()))
      );
      syncAudioDeviceVisibility();
    }

    function selectedSttInput() {
      const checked = sttInputInputs.find((input) => input.checked);
      return checked ? checked.value : "both";
    }

    function setSelectedSttInput(value) {
      const nextValue = ["both", "browser", "backend", "silent"].includes(value) ? value : "both";
      for (const input of sttInputInputs) {
        input.checked = input.value === nextValue;
      }
    }

    function syncSttInputControls() {
      const offline = selectedConnectivityMode() === "offline";
      if (offline) {
        setSelectedSttInput(firstAvailableSttInput("backend"));
      }
      for (const input of sttInputInputs) {
        const enabled = sttInputAvailable(input.value) && (!offline || input.value === "backend" || input.value === "silent");
        setSegmentOptionEnabled(input, enabled, sttInputUnavailableReason(input.value));
      }
      if (!sttInputAvailable(selectedSttInput())) {
        setSelectedSttInput(firstAvailableSttInput(selectedSttInput()));
      }
      syncAudioDeviceVisibility();
    }

	    function syncTtsProviderControls() {
      const connectivityMode = selectedConnectivityMode();
      const offline = connectivityMode === "offline";
	      const provider = cloudTtsProvider.value || "none";
	      const output = selectedTtsOutput();
	      const forceSilent = !offline && provider === "none";
      for (const element of cloudAudioControls) element.classList.toggle("hidden", offline);
      offlineAudioSummary.classList.toggle("hidden", !offline);
	      for (const input of ttsOutputInputs) {
        const enabled = ttsOutputAvailable(input.value)
          && (!forceSilent || input.value === "silent")
          && (!offline || input.value === "backend" || input.value === "silent");
        setSegmentOptionEnabled(input, enabled, ttsOutputUnavailableReason(input.value));
	        input.checked = offline
          ? input.value === (ttsOutputAvailable("backend") ? "backend" : "silent")
          : (forceSilent ? input.value === "silent" : input.value === firstAvailableTtsOutput(output));
	      }
	      elevenlabsVoiceField.classList.toggle("hidden", offline || provider !== "elevenlabs");
	      openaiTtsVoiceField.classList.toggle("hidden", offline || provider !== "openai");
	      ttsSpeedField.classList.toggle("hidden", offline || provider === "none");
	      ttsTestField.classList.toggle("hidden", offline || provider === "none");
	      elevenlabsVoice.disabled = offline || provider !== "elevenlabs" || elevenlabsVoice.options.length === 0 || !elevenlabsVoice.value;
	      openaiTtsVoice.disabled = offline || provider !== "openai" || openaiTtsVoice.options.length === 0 || !openaiTtsVoice.value;
	      openaiTtsSpeed.disabled = offline || provider === "none";
      webTtsVolume.disabled = offline || provider === "none" || selectedTtsOutput() !== "browser";
      backendTtsVolume.disabled = selectedTtsOutput() !== "backend";
      backendAudioOutputPan.disabled = backendAudioOutputPanField.classList.contains("hidden") || !backendAudioCapabilities.output;
      webTtsVolumeField.title = webTtsVolume.disabled ? "WEB_TTS_VOLUME - actif seulement avec TTS Output Browser" : "WEB_TTS_VOLUME";
      backendTtsVolumeField.title = backendTtsVolume.disabled ? "BACKEND_TTS_VOLUME - actif seulement avec TTS Output Backend" : "BACKEND_TTS_VOLUME";
      backendAudioOutputPanField.title = backendAudioOutputPan.disabled ? "BACKEND_AUDIO_OUTPUT_PAN - actif avec TTS Output Backend ou le monitoring backend actif" : "BACKEND_AUDIO_OUTPUT_PAN";
      ttsTest.disabled = offline || provider === "none" || (provider === "openai" && !openaiTtsVoice.value) || (provider === "elevenlabs" && !elevenlabsVoice.value);
      syncAudioDeviceVisibility();
	    }

    function selectedConnectivityMode() {
      const checked = connectivityModeInputs.find((input) => input.checked);
      return checked ? checked.value : "online";
    }

    function setSelectedConnectivityMode(value) {
      const nextValue = value === "offline" ? "offline" : "online";
      for (const input of connectivityModeInputs) {
        input.checked = input.value === nextValue;
      }
    }

    function selectedMcpToolRoutingEnabled() {
      const checked = mcpToolRoutingInputs.find((input) => input.checked);
      return checked ? checked.value === "true" : false;
    }

    function setSelectedMcpToolRoutingEnabled(enabled) {
      const nextValue = enabled ? "true" : "false";
      for (const input of mcpToolRoutingInputs) {
        input.checked = input.value === nextValue;
      }
    }

    function setSelectedInterruptConversationEnabled(enabled) {
      const nextValue = enabled ? "on" : "off";
      for (const input of interruptConversationInputs) {
        input.checked = input.value === nextValue;
      }
    }

    function syncConnectivityLock() {
      for (const input of connectivityModeInputs) {
        input.disabled = connectivityLocked;
      }
    }

    function syncConnectivityControls() {
      if (selectedConnectivityMode() === "offline") {
        if ([...llmProvider.options].some((option) => option.value === "ollama")) {
          llmProvider.value = "ollama";
        }
        if ([...cloudTtsProvider.options].some((option) => option.value === "none")) {
          cloudTtsProvider.value = "none";
        }
        setSelectedTtsOutput(ttsOutputAvailable("backend") ? "backend" : "silent");
        setSelectedSttInput(firstAvailableSttInput("backend"));
      }
      syncConnectivityLock();
      syncSttInputControls();
      syncTtsProviderControls();
    }

    function selectedTtsOutput() {
      const checked = ttsOutputInputs.find((input) => input.checked);
      return checked ? checked.value : "silent";
    }

    function setSelectedTtsOutput(value) {
      const nextValue = value || "silent";
      for (const input of ttsOutputInputs) {
        input.checked = input.value === nextValue;
      }
      syncAudioDeviceVisibility();
    }

    function autoSizeComposer() {
      injectCommand.style.height = "0px";
      injectCommand.style.height = `${Math.min(injectCommand.scrollHeight, 160)}px`;
    }

    function setSettingsOpen(open) {
      settingsOverlay.classList.toggle("open", open);
      settingsOverlay.setAttribute("aria-hidden", open ? "false" : "true");
      if (open) settingsClose.focus();
      else settingsOpen.focus();
    }

    function setLoadingOverlay(loading, title = tr("loading", "Loading"), detail = "Preparing persisted context summary", mode = "loading") {
      sessionLoading.classList.toggle("open", Boolean(loading));
      sessionLoading.classList.toggle("done", mode === "done");
      sessionLoading.classList.toggle("error", mode === "error");
      sessionLoading.setAttribute("aria-hidden", loading ? "false" : "true");
      sessionLoadingTitle.textContent = title;
      sessionLoadingDetail.textContent = detail;
    }

    function setEnvironmentLoading(loading, title = tr("environment_refresh", "rafraichissement de l'environnement")) {
      environmentLoadingActive = Boolean(loading);
      setLoadingOverlay(environmentLoadingActive, title, tr("applying_config", "Application de la nouvelle configuration"));
    }

    function setProfileLoading(loading, title = tr("preparing_voice_profile", "Préparation du profil vocal"), detail = speakerEmbeddingPreparationMessage, mode = "loading") {
      profileLoadingActive = Boolean(loading);
      if (environmentLoadingActive && !loading) return;
      setLoadingOverlay(loading, title, detail, mode);
    }

    function setSessionLoading(loading, title = "Loading session") {
      sessionNew.disabled = Boolean(loading);
      for (const button of sessionList.querySelectorAll(".session-main, .session-menu-button, .session-summary-button, .session-menu-action")) {
        button.disabled = Boolean(loading);
      }
      if ((environmentLoadingActive || profileLoadingActive) && !loading) return;
      setLoadingOverlay(loading, title);
    }

    function closeSessionMenus() {
      openSessionMenuId = "";
      for (const row of sessionList.querySelectorAll(".session-row.menu-open")) {
        row.classList.remove("menu-open");
      }
    }

    async function renameSession(sessionId, currentTitle) {
      const title = window.prompt(tr("rename_session", "Rename session"), currentTitle || tr("untitled_session", "Untitled session"));
      if (title === null) return;
      const cleanedTitle = title.trim();
      if (!cleanedTitle) return;
      setSessionLoading(true, tr("renaming_session", "Renaming session"));
      try {
        const response = await fetch("/api/session-context/rename", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: sessionId, title: cleanedTitle })
        });
        if (!response.ok) throw new Error(await response.text());
        await refresh();
      } catch (error) {
        metaEl.textContent = `session rename failed: ${error}`;
      } finally {
        setSessionLoading(false);
      }
    }

    async function deleteSession(sessionId, currentTitle) {
      const confirmed = window.confirm(`Delete session "${currentTitle || "Untitled session"}"?`);
      if (!confirmed) return;
      setSessionLoading(true, "Deleting session");
      try {
        const response = await fetch("/api/session-context/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: sessionId })
        });
        if (!response.ok) throw new Error(await response.text());
        pendingMessages = [];
        await refresh();
      } catch (error) {
        metaEl.textContent = `session delete failed: ${error}`;
      } finally {
        setSessionLoading(false);
      }
    }

    async function clearSessionConversation(sessionId, currentTitle) {
      const confirmed = window.confirm(
        `Clear visible conversation for "${currentTitle || "Untitled session"}"? The LLM summary will be kept.`
      );
      if (!confirmed) return;
      setSessionLoading(true, "Clearing conversation");
      try {
        const response = await fetch("/api/session-context/clear", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: sessionId })
        });
        if (!response.ok) throw new Error(await response.text());
        pendingMessages = [];
        await refresh();
      } catch (error) {
        metaEl.textContent = `session clear failed: ${error}`;
      } finally {
        setSessionLoading(false);
      }
    }

    async function saveSessionContext(sessionId, currentTitle) {
      setSessionLoading(true, "Saving context");
      try {
        const response = await fetch("/api/session-context/save", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: sessionId })
        });
        if (!response.ok) throw new Error(await response.text());
        await refresh();
        metaEl.textContent = `context saved for ${currentTitle || "Untitled session"}`;
      } catch (error) {
        metaEl.textContent = `context save failed: ${error}`;
      } finally {
        setSessionLoading(false);
      }
    }

    function activateTab(tabId) {
      for (const tab of tabs) {
        const active = tab.id === tabId;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
      }
      for (const panel of panels) {
        panel.classList.toggle("active", panel.getAttribute("aria-labelledby") === tabId);
      }
    }

	    async function loadLlmOptions(provider, preferredModel, connectivityOverride = "") {
      if (llmOptionsLoading) return false;
      const shouldMarkClean = !connectivityOverride;
      llmOptionsLoading = true;
	      llmProvider.disabled = true;
	      llmModel.disabled = true;
      for (const input of connectivityModeInputs) input.disabled = true;
	        sessionContextSize.disabled = true;
      for (const input of mcpToolRoutingInputs) input.disabled = true;
      for (const input of interruptConversationInputs) input.disabled = true;
      wakeWord.disabled = true;
      sttPromptEl.disabled = true;
      sttLanguage.disabled = true;
      assistantSystemPromptEl.disabled = true;
      cloudTtsProvider.disabled = true;
      for (const input of ttsOutputInputs) input.disabled = true;
      for (const input of sttInputInputs) input.disabled = true;
      elevenlabsVoice.disabled = true;
      openaiTtsVoice.disabled = true;
      openaiTtsSpeed.disabled = true;
      webTtsVolume.disabled = true;
      backendTtsVolume.disabled = true;
      backendAudioMonitorVolume.disabled = true;
      for (const input of backendAudioMonitorModeInputs) input.disabled = true;
      backendAudioOutputPan.disabled = true;
      commandAckSound.disabled = true;
      ttsTest.disabled = true;
      for (const control of vadControls) control.disabled = true;
      for (const button of vadPresetButtons) button.disabled = true;
      setSpeakerControlsDisabled(true, "Loading speaker recognition options...");
      backendAudioInput.disabled = true;
      browserAudioTest.disabled = true;
      backendAudioTest.disabled = true;
      backendAudioOutput.disabled = true;
      thinkingSound.disabled = true;
      llmSave.disabled = true;
      llmMessage.textContent = tr("loading_llm_options", "Loading LLM options...");
      try {
        const suffix = provider ? `?provider=${encodeURIComponent(provider)}` : "";
        const response = await fetch(`/api/llm-options${suffix}`, { cache: "no-store" });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();

	        const selectedProvider = data.provider || provider || "";
        setSelectedConnectivityMode(connectivityOverride || data.selected_connectivity_mode || "online");
	        llmProvider.replaceChildren();
        for (const item of data.providers || []) {
          const label = item.available === false && item.reason
            ? `${item.label || item.id} (${item.reason})`
            : (item.label || item.id);
          llmProvider.appendChild(option(label, item.id, item.available === false, item.id === selectedProvider));
        }
        if (selectedProvider && llmProvider.value !== selectedProvider) {
          llmProvider.value = selectedProvider;
        }
        setSessionContextSize(data.selected_session_context_size || 0);
        setMcpAgentMaxSteps(data.selected_mcp_agent_max_steps || 20);
        setSelectedMcpToolRoutingEnabled(Boolean(data.selected_mcp_tool_routing_enabled));
        setSelectedInterruptConversationEnabled(Boolean(data.selected_interrupt_conversation_enabled));
        interruptConversationEnabled = selectedInterruptConversationEnabled();
        wakeWord.value = data.selected_wake_word || "";
        sttPromptEl.value = data.selected_stt_prompt || "";
        assistantSystemPromptEl.value = data.selected_system_prompt || "";

        cloudTtsProvider.replaceChildren();
        const selectedCloudTtsProvider = data.selected_cloud_tts_provider || "";
        for (const item of data.cloud_tts_providers || []) {
          cloudTtsProvider.appendChild(option(item.label || item.id, item.id, false, item.id === selectedCloudTtsProvider));
        }
        if (selectedCloudTtsProvider && cloudTtsProvider.value !== selectedCloudTtsProvider) {
          cloudTtsProvider.value = selectedCloudTtsProvider;
        }
        setSelectedTtsOutput(data.selected_tts_output || "silent");
        setSelectedSttInput(data.selected_stt_input || "both");
        sttLanguage.replaceChildren();
        const selectedSttLanguage = data.selected_stt_language || i18nPayload.locale || "fr";
        const locales = data.available_locales || i18nPayload.available_locales || [];
        for (const locale of locales) {
          sttLanguage.appendChild(option(locale.label || locale.id, locale.id, false, locale.id === selectedSttLanguage));
        }
        if (selectedSttLanguage && sttLanguage.value !== selectedSttLanguage) {
          sttLanguage.value = selectedSttLanguage;
        }

        llmModel.replaceChildren();
        const selectedModel = preferredModel || data.selected_model || "";
        const models = data.models || [];
        if (models.length === 0) {
          llmModel.appendChild(option(tr("no_model_available", "No model available"), "", true, true));
        } else {
          for (const model of models) {
            llmModel.appendChild(option(model.label || model.id, model.id, false, model.id === selectedModel));
          }
          if (selectedModel && !models.some((model) => model.id === selectedModel)) {
            llmModel.appendChild(option(`${selectedModel} (${tr("current", "current")})`, selectedModel, false, true));
          }
        }

        elevenlabsVoice.replaceChildren();
        const selectedVoiceId = data.selected_voice_id || "";
        const voices = data.voices || [];
        if (voices.length === 0) {
          elevenlabsVoice.appendChild(option(tr("no_voice_available", "No voice available"), "", true, true));
        } else {
          for (const voice of voices) {
            elevenlabsVoice.appendChild(option(voice.label || voice.id, voice.id, false, voice.id === selectedVoiceId));
          }
          if (selectedVoiceId && !voices.some((voice) => voice.id === selectedVoiceId)) {
            elevenlabsVoice.appendChild(option(`${selectedVoiceId} (${tr("current", "current")})`, selectedVoiceId, false, true));
          }
        }

        openaiTtsVoice.replaceChildren();
        const selectedOpenAiTtsVoice = data.selected_openai_tts_voice || "";
        const openAiVoices = data.openai_tts_voices || [];
        if (openAiVoices.length === 0) {
          openaiTtsVoice.appendChild(option(tr("no_voice_available", "No voice available"), "", true, true));
        } else {
          for (const voice of openAiVoices) {
            openaiTtsVoice.appendChild(option(voice.label || voice.id, voice.id, false, voice.id === selectedOpenAiTtsVoice));
          }
          if (selectedOpenAiTtsVoice && !openAiVoices.some((voice) => voice.id === selectedOpenAiTtsVoice)) {
            openaiTtsVoice.appendChild(option(`${selectedOpenAiTtsVoice} (${tr("current", "current")})`, selectedOpenAiTtsVoice, false, true));
          }
        }
	        openaiTtsSpeed.value = String(data.selected_openai_tts_speed || 1.0);
	        webTtsVolume.value = String(data.selected_web_tts_volume ?? 1.0);
	        backendTtsVolume.value = String(data.selected_backend_tts_volume ?? 1.0);
	        setSelectedBackendAudioMonitorMode(data.selected_backend_audio_monitor_mode || "off");
	        backendAudioMonitorVolume.value = String(data.selected_backend_audio_monitor_volume ?? 1.0);
	        backendAudioOutputPan.value = String(data.selected_backend_audio_output_pan ?? 0.0);
	        setCommandAckSoundEnabled(Boolean(data.selected_command_ack_sound_enabled));
	        syncOpenAiSpeedLabel();
        syncTtsVolumeLabels();
        syncBackendAudioMonitorVolumeLabel();
        syncBackendAudioOutputPanLabel();
        setVadControls(data);
        setSpeakerControls(data);

        backendAudioInput.replaceChildren();
        backendAudioCapabilities.input = Array.isArray(data.backend_audio_inputs) && data.backend_audio_inputs.length > 0;
        const selectedBackendAudioInput = data.selected_backend_audio_input_device || "";
        backendAudioInput.appendChild(option(backendAudioCapabilities.input ? "Default input" : "No backend input", "", !backendAudioCapabilities.input, !selectedBackendAudioInput));
        for (const device of data.backend_audio_inputs || []) {
          const label = device.default ? `${device.label || device.id} (default)` : (device.label || device.id);
          backendAudioInput.appendChild(option(label, device.id, false, device.id === selectedBackendAudioInput));
        }
        if (selectedBackendAudioInput && ![...backendAudioInput.options].some((item) => item.value === selectedBackendAudioInput)) {
          backendAudioInput.options[0].textContent = `Default input (current unavailable: ${selectedBackendAudioInput})`;
          backendAudioInput.value = "";
        }

        backendAudioOutput.replaceChildren();
        backendAudioCapabilities.output = Array.isArray(data.backend_audio_outputs) && data.backend_audio_outputs.length > 0;
        const selectedBackendAudioOutput = data.selected_backend_audio_output_device || "";
        backendAudioOutput.appendChild(option(backendAudioCapabilities.output ? "Default output" : "No backend output", "", !backendAudioCapabilities.output, !selectedBackendAudioOutput));
        for (const device of data.backend_audio_outputs || []) {
          const label = device.default ? `${device.label || device.id} (default)` : (device.label || device.id);
          backendAudioOutput.appendChild(option(label, device.id, false, device.id === selectedBackendAudioOutput));
        }
        if (selectedBackendAudioOutput && ![...backendAudioOutput.options].some((item) => item.value === selectedBackendAudioOutput)) {
          backendAudioOutput.options[0].textContent = `Default output (current unavailable: ${selectedBackendAudioOutput})`;
          backendAudioOutput.value = "";
        }
        syncConnectivityControls();

        thinkingSound.replaceChildren();
        const selectedThinkingSound = data.selected_thinking_sound_file || "";
        const sounds = data.thinking_sounds || [];
        if (sounds.length === 0) {
          thinkingSound.appendChild(option("No WAV available", "", true, true));
        } else {
          for (const sound of sounds) {
            thinkingSound.appendChild(option(sound.label || sound.id, sound.id, false, sound.id === selectedThinkingSound));
          }
          if (selectedThinkingSound && !sounds.some((sound) => sound.id === selectedThinkingSound)) {
            thinkingSound.appendChild(option(`${selectedThinkingSound} (${tr("current", "current")})`, selectedThinkingSound, false, true));
          }
        }

        llmMessage.textContent = data.message || "";
        if (shouldMarkClean) {
          markConfigClean();
        }
      } catch (error) {
        llmMessage.textContent = `LLM options unavailable: ${error}`;
      } finally {
	        llmProvider.disabled = false;
	        llmModel.disabled = llmModel.options.length === 0 || !llmModel.value;
        for (const input of connectivityModeInputs) input.disabled = connectivityLocked;
	        sessionContextSize.disabled = false;
        for (const input of mcpToolRoutingInputs) input.disabled = false;
        for (const input of interruptConversationInputs) input.disabled = false;
        wakeWord.disabled = false;
        sttPromptEl.disabled = false;
        sttLanguage.disabled = sttLanguage.options.length === 0 || !sttLanguage.value;
        assistantSystemPromptEl.disabled = false;
        cloudTtsProvider.disabled = cloudTtsProvider.options.length === 0 || !cloudTtsProvider.value;
        for (const input of ttsOutputInputs) input.disabled = false;
        for (const input of sttInputInputs) input.disabled = false;
        for (const control of vadControls) control.disabled = false;
        for (const button of vadPresetButtons) button.disabled = false;
        setSpeakerControlsDisabled(Boolean(speakerRecognitionUnavailableReason), speakerRecognitionUnavailableReason);
	        syncConnectivityControls();
        backendAudioInput.disabled = !backendAudioCapabilities.input || backendAudioInput.options.length === 0;
        backendAudioOutput.disabled = !backendAudioCapabilities.output || backendAudioOutput.options.length === 0;
        syncBackendAudioMonitorControls();
        syncCommandAckSoundControls();
        backendAudioOutputPan.disabled = !backendAudioCapabilities.output || backendAudioOutputPanField.classList.contains("hidden");
        browserAudioTest.disabled = !browserAudioCapabilities.input || browserAudioInputField.classList.contains("hidden");
        backendAudioTest.disabled = !backendAudioCapabilities.input || backendAudioInputField.classList.contains("hidden");
        thinkingSound.disabled = thinkingSound.options.length === 0 || !thinkingSound.value;
        llmSave.disabled = !llmProvider.value;
        llmOptionsLoading = false;
      }
      return true;
    }

    async function syncLlmControls(data) {
      if (llmControlsInitialized) return;
      const env = (data.config && data.config.env) || {};
      const provider = String(env.LLM_PROVIDER || "openai").toLowerCase();
      const model = String(env.OPENAI_MODEL || "");
      const loaded = await loadLlmOptions(provider, model);
      if (loaded !== false) {
        llmControlsInitialized = true;
      }
    }

    async function refresh() {
      try {
        const response = await fetch("/api/snapshot", { cache: "no-store" });
        const data = await response.json();
        const previousBusy = composerLocked;
        const snapshotEnvFile = data.env_file || "";
        const snapshotEnvChanged = Boolean(currentSnapshotEnvFile) && snapshotEnvFile && snapshotEnvFile !== currentSnapshotEnvFile;
        if (snapshotEnvFile) {
          currentSnapshotEnvFile = snapshotEnvFile;
        }
        const services = data.services || {};
        const rows = [
          tile("Internet", data.internet, data.mode === "auto" ? "auto profile detection" : "fixed profile"),
          tile("Profile", data.mode, data.env_file || ""),
          ...Object.entries(services).map(([name, service]) => tile(name, service.status, service.detail))
        ];
        stateEl.innerHTML = rows.join("");
        configEl.value = data.config_text || "";
        renderMcpServers(data.mcp_servers || []);
        syncMcpRoutingEditors();
        const remoteScreen = data.remote_screen || {};
        if (!vncUrlDirty && snapshotEnvChanged && currentVncFrameUrl) {
          disconnectVnc("reconnexion VNC...");
        }
        if (!vncUrlDirty && remoteScreen.vnc_url) {
          const remoteScreenUrlChanged = vncUrl.value !== remoteScreen.vnc_url;
          if (remoteScreenUrlChanged) {
            vncUrl.value = remoteScreen.vnc_url;
          }
          if (typeof remoteScreen.view_only === "boolean" && vncViewOnly.checked !== remoteScreen.view_only) {
            vncViewOnly.checked = remoteScreen.view_only;
          }
          let remoteScreenFrameUrl = "";
          try {
            remoteScreenFrameUrl = noVncUrlFromInput(remoteScreen.vnc_url);
          } catch (error) {
            remoteScreenFrameUrl = "";
          }
          if (remoteScreenFrameUrl && (remoteScreenUrlChanged || snapshotEnvChanged || !currentVncFrameUrl)) {
            await connectVnc({ force: true });
          }
        }
        const environmentLoading = data.environment_loading || {};
        setEnvironmentLoading(
          Boolean(environmentLoading.active),
          environmentLoading.title || tr("environment_refresh", "rafraichissement de l'environnement")
        );
        const envProfileChanged = await loadEnvProfiles();
        if (envProfileChanged) {
          llmControlsInitialized = false;
          configBaseline = "";
          cloudApiLoaded = false;
        }
        await syncLlmControls(data);
        promptEl.value = data.prompt || "";
        renderSessions(data.session_context || {});
        if (!settingsOverlay.classList.contains("open")) {
          setSessionContextSize(data.session_context_size || 0);
        }
        const shouldStick = logsEl.scrollTop + logsEl.clientHeight >= logsEl.scrollHeight - 8;
        logsEl.value = data.logs || "";
        if (shouldStick) logsEl.scrollTop = logsEl.scrollHeight;
        webAudio = data.web_audio || { enabled: false, stt_enabled: false, tts_enabled: false, tts_output: "silent" };
        interruptConversationEnabled = Boolean(webAudio.interrupt_conversation_enabled);
        thinkingAudioUrl = data.thinking_sound_url || "";
        commandAckSoundUrl = data.command_ack_sound_url || "/assets/ring.wav";
        if (!webAudio.stt_enabled && conversationEnabled) {
          setConversationEnabled(false);
        }
        const runtimeSpeaker = (data.runtime || {}).speaker_recognition || {};
        if (Object.keys(runtimeSpeaker).length > 0) {
          speakerRecognitionRuntimeEnabled = Boolean(runtimeSpeaker.enabled);
          speakerRecognitionUnavailableReason = runtimeSpeaker.requested && !runtimeSpeaker.enabled && runtimeSpeaker.unavailable_reason
            ? `Speaker recognition unavailable: ${runtimeSpeaker.unavailable_reason}`
            : "";
          syncComposerSpeakerControl();
        }
        updateConversationButton();
        lastServerMessages = data.messages || [];
        const serverBusy = Boolean(data.assistant_busy);
        const showThinking = serverBusy || pendingMessages.length > 0;
        const latestAssistantMessage = [...lastServerMessages].reverse().find((message) => message.role === "assistant");
        const latestAssistantMessageId = latestAssistantMessage ? latestAssistantMessage.id : null;
        const isNewAssistantMessage = Boolean(
          messagesHydrated &&
          latestAssistantMessageId !== null &&
          latestAssistantMessageId !== undefined &&
          !seenAssistantMessageIds.has(latestAssistantMessageId)
        );
        const latestAssistantMessageAgeMs = latestAssistantMessage && latestAssistantMessage.created_at
          ? Date.now() - Number(latestAssistantMessage.created_at) * 1000
          : Infinity;
        const shouldSpeakLatestAssistant = latestAssistantMessage && isNewAssistantMessage && (
          previousBusy || (latestAssistantMessage.speak === true && latestAssistantMessageAgeMs < 30000)
        );
        const willSpeakLatestAssistant = Boolean(
          shouldSpeakLatestAssistant &&
          webAudio.tts_enabled &&
          latestAssistantMessage &&
          latestAssistantMessage.id !== lastSpokenAssistantMessageId
        );
        if (webAudio.tts_enabled && webAudio.tts_output === "browser" && (showThinking || willSpeakLatestAssistant)) startThinkingAudio();
        else stopThinkingAudio();
        setComposerLocked(showThinking);
        renderMessages(lastServerMessages, showThinking);
        for (const message of lastServerMessages) {
          if (message.role === "assistant" && message.id !== null && message.id !== undefined) {
            seenAssistantMessageIds.add(message.id);
          }
        }
        messagesHydrated = true;
        if (
          willSpeakLatestAssistant
        ) {
          if (!webTtsUnlocked) {
            deferWebTtsUntilUserGesture(latestAssistantMessage);
          } else {
            lastSpokenAssistantMessageId = latestAssistantMessage.id;
            if (!(latestAssistantMessage.speak === true && showThinking)) {
              playBrowserCommandAckSound();
            }
            playWebTts(latestAssistantMessage.text || "");
          }
        } else if (previousBusy && !showThinking && conversationEnabled) {
          const delay = interruptConversationEnabled
            ? 250
            : webAudio.tts_blocked_by_backend && latestAssistantMessage
            ? Math.min(10000, 1200 + String(latestAssistantMessage.text || "").length * 55)
            : 250;
          scheduleConversationRestart(delay);
        }
        const updated = data.updated_at ? new Date(data.updated_at * 1000).toLocaleTimeString() : "unknown";
        if (Date.now() >= metaErrorUntil) {
          setMeta(`updated ${updated} · uptime ${data.uptime_seconds || 0}s`);
        }
      } catch (error) {
        setMeta(`disconnected: ${error}`, "error", 5000);
      }
    }

    refresh();
    setInterval(refresh, 1500);

    toastOk.addEventListener("click", hideToast);
    toastOverlay.addEventListener("click", (event) => {
      if (event.target === toastOverlay) hideToast();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && toastOverlay.classList.contains("open")) {
        hideToast();
      }
    });

    vncUrl.addEventListener("input", () => {
      vncUrlDirty = true;
    });
    vncViewOnly.addEventListener("change", () => {
      if (currentVncFrameUrl) {
        disconnectVnc("reconnexion VNC...");
        connectVnc({ force: true, save: true });
      } else {
        saveRemoteScreenUrl().catch((error) => {
          console.warn("Could not save VNC view-only option", error);
        });
      }
    });
    vncConnect.addEventListener("click", () => connectVnc({ save: true }));
    vncUrl.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        connectVnc({ save: true });
      }
    });
    vncFrame.addEventListener("load", () => {
      if (currentVncFrameUrl) {
        setVncStatus("connexion...");
      }
    });
    vncFrame.addEventListener("error", () => {
      window.clearTimeout(vncConnectTimer);
      setVncStatus("hors ligne");
    });
    window.addEventListener("message", (event) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data || {};
      if (data.type !== "lsa-vnc-status") return;
      window.clearTimeout(vncConnectTimer);
      setVncStatus(String(data.text || (data.connected ? "connecté" : "hors ligne")));
    });

    injectCommand.addEventListener("input", autoSizeComposer);
    composerSpeaker.addEventListener("change", () => {
      window.localStorage.setItem("lsaComposerSpeaker", composerSpeaker.value || "auto");
    });
    composerAttach.addEventListener("click", () => {
      composerFile.click();
    });
    composerFile.addEventListener("change", async () => {
      const file = composerFile.files && composerFile.files[0];
      composerFile.value = "";
      try {
        await handleComposerFile(file);
      } catch (error) {
        setMeta(`file upload failed: ${error.message || error}`, "error", 7000);
      }
    });
    injectCommand.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        submitComposerCommand();
      }
    });

    injectCommand.addEventListener("beforeinput", (event) => {
      if (event.inputType === "insertLineBreak" && !event.shiftKey) {
        event.preventDefault();
        submitComposerCommand();
      }
    });

    injectForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      await submitComposerCommand();
    });

    injectStop.addEventListener("click", async () => {
      await unlockWebTtsAudio();
      await cancelCommand();
    });

    webMic.addEventListener("click", async () => {
      await unlockWebTtsAudio();
      if (isRecording) {
        stopWebRecording();
      } else {
        await startWebRecording();
      }
    });

    webConversation.addEventListener("click", async () => {
      await unlockWebTtsAudio();
      setConversationEnabled(!conversationEnabled);
    });

    document.addEventListener("mousemove", (event) => {
      lastPointer = { x: event.clientX, y: event.clientY };
    });

    sessionNew.addEventListener("click", async () => {
      setSessionLoading(true, "Creating session");
      try {
        const response = await fetch("/api/session-context/new", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({})
        });
        if (!response.ok) throw new Error(await response.text());
        pendingMessages = [];
        await refresh();
      } catch (error) {
        metaEl.textContent = `new session failed: ${error}`;
      } finally {
        setSessionLoading(false);
      }
    });

    sessionList.addEventListener("mouseover", (event) => {
      if (!canHoverSessionSummary()) return;
      lastPointer = { x: event.clientX, y: event.clientY };
      const row = event.target.closest(".session-row.has-summary");
      if (!row || row.contains(event.relatedTarget)) return;
      const sessionId = row.dataset.sessionId || "";
      if (!sessionId) return;
      scheduleSessionSummary(sessionId);
    });

    sessionList.addEventListener("mouseout", (event) => {
      if (!canHoverSessionSummary()) return;
      lastPointer = { x: event.clientX, y: event.clientY };
      const row = event.target.closest(".session-row.has-summary");
      if (!row || row.contains(event.relatedTarget)) return;
      if (openSessionSummaryId === row.dataset.sessionId || sessionSummaryHoverId === row.dataset.sessionId) {
        closeSessionSummaryAfterPointerCheck(row.dataset.sessionId);
      }
    });

    sessionList.addEventListener("click", async (event) => {
      const actionButton = event.target.closest(".session-menu-action");
      const menuButton = event.target.closest(".session-menu-button");
      const summaryButton = event.target.closest(".session-summary-button");
      const mainButton = event.target.closest(".session-main");
      const row = event.target.closest(".session-row");
      if (!row || composerLocked) return;
      const sessionId = row.dataset.sessionId;
      if (!sessionId) return;

      if (actionButton) {
        const action = actionButton.dataset.sessionAction;
        const title = row.dataset.sessionTitle || "Untitled session";
        closeSessionMenus();
        closeSessionSummary();
        if (action === "rename") {
          await renameSession(sessionId, title);
        } else if (action === "clear") {
          await clearSessionConversation(sessionId, title);
        } else if (action === "save-context") {
          await saveSessionContext(sessionId, title);
        } else if (action === "delete") {
          await deleteSession(sessionId, title);
        }
        return;
      }

      if (menuButton) {
        const wasOpen = row.classList.contains("menu-open");
        closeSessionMenus();
        closeSessionSummary();
        if (!wasOpen) {
          openSessionMenuId = sessionId;
          row.classList.add("menu-open");
        }
        return;
      }

      if (summaryButton) {
        const wasOpen = openSessionSummaryId === sessionId && sessionSummaryPinned;
        closeSessionSummary();
        if (!wasOpen) openSessionSummary(sessionId, row, { pinned: true });
        return;
      }

      if (!mainButton) return;
      closeSessionMenus();
      closeSessionSummary();
      setSessionLoading(true, "Loading session");
      try {
        const response = await fetch("/api/session-context/select", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: sessionId })
        });
        if (!response.ok) throw new Error(await response.text());
        pendingMessages = [];
        await refresh();
      } catch (error) {
        metaEl.textContent = `session switch failed: ${error}`;
      } finally {
        setSessionLoading(false);
      }
    });

    document.addEventListener("click", (event) => {
      if (!event.target.closest(".session-row") && !event.target.closest("#session-summary-popover")) {
        closeSessionMenus();
        closeSessionSummary();
      }
    });
    document.addEventListener("pointerdown", () => {
      unlockWebTtsAudioFromUserGesture();
    }, { capture: true });
    document.addEventListener("keydown", () => {
      unlockWebTtsAudioFromUserGesture();
    }, { capture: true });
    sessionList.addEventListener("scroll", () => closeSessionSummary());
    window.addEventListener("resize", () => {
      if (openSessionSummaryId && sessionSummaryAnchor) {
        placeSessionSummaryPopover(sessionSummaryAnchor);
      }
    });

    settingsOpen.addEventListener("click", () => setSettingsOpen(true));
    settingsClose.addEventListener("click", () => setSettingsOpen(false));
    settingsOverlay.addEventListener("click", (event) => {
      if (event.target === settingsOverlay) setSettingsOpen(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && settingsOverlay.classList.contains("open")) {
        setSettingsOpen(false);
      }
    });
    for (const tab of tabs) {
      tab.addEventListener("click", () => activateTab(tab.id));
    }

	    llmProvider.addEventListener("change", () => {
	      loadLlmOptions(llmProvider.value, "", selectedConnectivityMode());
	    });

    for (const input of connectivityModeInputs) {
      input.addEventListener("change", () => {
        const mode = selectedConnectivityMode();
        loadLlmOptions(mode === "offline" ? "ollama" : "openai", "", mode);
      });
    }

    envProfile.addEventListener("change", () => {
      cloudApiLoaded = false;
      switchEnvProfile(envProfile.value);
    });

	    cloudTtsProvider.addEventListener("change", syncTtsProviderControls);
    elevenlabsVoice.addEventListener("change", syncTtsProviderControls);
    openaiTtsVoice.addEventListener("change", syncTtsProviderControls);
    for (const input of ttsOutputInputs) {
      input.addEventListener("change", syncTtsProviderControls);
    }
    for (const input of sttInputInputs) {
      input.addEventListener("change", syncSttInputControls);
    }
    browserAudioInput.addEventListener("change", () => {
      stopBrowserAudioTest();
      selectedBrowserAudioInput = browserAudioInput.value;
      window.localStorage.setItem("browser-audio-input", selectedBrowserAudioInput);
      if (conversationEnabled) {
        stopConversationRecording(true, true);
        scheduleConversationRestart(0);
      }
    });
    browserAudioTest.addEventListener("click", toggleBrowserAudioTest);
    backendAudioInput.addEventListener("change", stopBackendAudioTest);
    backendAudioTest.addEventListener("click", toggleBackendAudioTest);
    browserAudioOutput.addEventListener("change", async () => {
      selectedBrowserAudioOutput = browserAudioOutput.value;
      window.localStorage.setItem("browser-audio-output", selectedBrowserAudioOutput);
      try {
        await applyBrowserAudioOutput(thinkingAudio);
        await applyBrowserAudioOutput(currentWebTtsAudio);
      } catch (error) {
        metaEl.textContent = `browser audio output unavailable: ${error}`;
      }
    });
    browserAudioRefresh.addEventListener("click", () => loadBrowserAudioDevices(true));
    sessionContextSize.addEventListener("input", () => {
      syncSessionContextSizeLabel();
      renderMessages(lastServerMessages, composerLocked || pendingMessages.length > 0);
    });
    mcpAgentMaxSteps.addEventListener("input", syncMcpAgentMaxStepsLabel);
    for (const input of mcpToolRoutingInputs) {
      input.addEventListener("change", syncMcpRoutingEditors);
    }
    setSelectedMcpAdminRoute(window.localStorage.getItem("mcp-admin-route") || "proxy");
    for (const input of mcpAdminRouteInputs) {
      input.addEventListener("change", () => {
        const route = selectedMcpAdminRoute();
        window.localStorage.setItem("mcp-admin-route", route);
        mcpServersSignature = "";
        renderMcpServers(lastMcpServers);
      });
    }
    for (const input of interruptConversationInputs) {
      input.addEventListener("change", () => {
        interruptConversationEnabled = selectedInterruptConversationEnabled();
      });
    }
    speakerThreshold.addEventListener("input", syncSpeakerLabels);
    speakerMargin.addEventListener("input", syncSpeakerLabels);
    cloudApiDetails.addEventListener("toggle", () => {
      if (cloudApiDetails.open) loadCloudApiStatus();
    });
    cloudApiRefresh.addEventListener("click", () => loadCloudApiStatus(true));

    llmSave.addEventListener("click", async () => {
      const provider = llmProvider.value;
      const model = llmModel.value;
      const sessionContextSizeValue = Number(sessionContextSize.value || 0);
      const mcpAgentMaxStepsValue = Number(mcpAgentMaxSteps.value || 20);
      const mcpToolRoutingEnabled = selectedMcpToolRoutingEnabled();
      const interruptConversation = selectedInterruptConversationEnabled();
      const wakeWordValue = wakeWord.value.trim();
	      const sttPromptValue = sttPromptEl.value.trim();
	      const systemPromptValue = assistantSystemPromptEl.value.trim();
      const connectivityModeValue = selectedConnectivityMode();
      const cloudTtsProviderValue = cloudTtsProvider.value;
      const ttsOutputValue = selectedTtsOutput();
      const sttInputValue = selectedSttInput();
      const sttLanguageValue = sttLanguage.value || i18nPayload.locale || "fr";
      const backendAudioInputDevice = backendAudioInput.value;
      const backendAudioOutputDevice = backendAudioOutput.value;
      const backendAudioOutputPanValue = Number(backendAudioOutputPan.value || 0);
      const backendAudioMonitorModeValue = selectedBackendAudioMonitorMode();
      const backendAudioMonitorVolumeValue = Number(backendAudioMonitorVolume.value || 1);
      const voiceId = elevenlabsVoice.value;
      const thinkingSoundFile = thinkingSound.value;
      const commandAckSoundEnabledValue = commandAckSoundEnabled();
      const openAiTtsVoiceValue = openaiTtsVoice.value;
      const openAiTtsSpeedValue = Number(openaiTtsSpeed.value || 1);
      const webTtsVolumeValue = Number(webTtsVolume.value || 1);
      const backendTtsVolumeValue = Number(backendTtsVolume.value || 1);
      const vadSpeechThresholdValue = Number(vadSpeechThreshold.value || 0.5);
      const vadNegativeThresholdValue = Number(vadNegativeThreshold.value || 0.35);
      const vadMinSpeechMsValue = Number(vadMinSpeechMs.value || 120);
      const vadMinSilenceMsValue = Number(vadMinSilenceMs.value || 650);
      const vadSpeechPadMsValue = Number(vadSpeechPadMs.value || 100);
      const vadMaxSpeechSecondsValue = Number(vadMaxSpeechSeconds.value || 8);
      const speakerRecognitionEnabledValue = selectedSpeakerRecognitionEnabled();
      const speakerBackendValue = speakerBackend.value || "resemblyzer";
      const speakerThresholdValue = Number(speakerThreshold.value || 0.75);
      const speakerMarginValue = Number(speakerMargin.value || 0.10);
      const speakerProfilesValue = collectSpeakerProfiles();
      if (!provider) return;

      llmSave.disabled = true;
      llmMessage.textContent = tr("saving", "Saving...");
      try {
        const response = await fetch("/api/llm-config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider,
            model,
            session_context_size: sessionContextSizeValue,
            mcp_agent_max_steps: mcpAgentMaxStepsValue,
            mcp_tool_routing_enabled: mcpToolRoutingEnabled,
            interrupt_conversation_enabled: interruptConversation,
            connectivity_mode: connectivityModeValue,
            cloud_tts_provider: cloudTtsProviderValue,
            tts_output: ttsOutputValue,
            stt_input: sttInputValue,
            stt_language: sttLanguageValue,
            backend_audio_input_device: backendAudioInputDevice,
            backend_audio_output_device: backendAudioOutputDevice,
            backend_audio_output_pan: backendAudioOutputPanValue,
            backend_audio_monitor_mode: backendAudioMonitorModeValue,
            backend_audio_monitor_volume: backendAudioMonitorVolumeValue,
            wake_word: wakeWordValue,
            stt_prompt: sttPromptValue,
            system_prompt: systemPromptValue,
            voice_id: voiceId,
            thinking_sound_file: thinkingSoundFile,
            command_ack_sound_enabled: commandAckSoundEnabledValue,
            openai_tts_voice: openAiTtsVoiceValue,
            openai_tts_speed: openAiTtsSpeedValue,
            web_tts_volume: webTtsVolumeValue,
            backend_tts_volume: backendTtsVolumeValue,
            vad_speech_threshold: vadSpeechThresholdValue,
            vad_negative_threshold: vadNegativeThresholdValue,
            vad_min_speech_ms: vadMinSpeechMsValue,
            vad_min_silence_ms: vadMinSilenceMsValue,
            vad_speech_pad_ms: vadSpeechPadMsValue,
            vad_max_speech_seconds: vadMaxSpeechSecondsValue,
            speaker_recognition_enabled: speakerRecognitionEnabledValue,
            speaker_backend: speakerBackendValue,
            speaker_threshold: speakerThresholdValue,
            speaker_margin: speakerMarginValue,
            speaker_profiles: speakerProfilesValue
          })
        });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        llmMessage.textContent = data.message || tr("saved", "Saved.");
        cloudApiLoaded = false;
        setEnvironmentLoading(true);
        markConfigClean();
        if ((data.stt_language || sttLanguageValue) !== i18nPayload.locale) {
          window.setTimeout(() => window.location.reload(), 250);
          return;
        }
        await refresh();
      } catch (error) {
        setEnvironmentLoading(false);
        llmMessage.textContent = trf("save_failed", "Save failed: {error}", { error });
      } finally {
        llmSave.disabled = !llmProvider.value;
      }
    });

    openaiTtsSpeed.addEventListener("input", syncOpenAiSpeedLabel);
    webTtsVolume.addEventListener("input", syncTtsVolumeLabels);
    backendTtsVolume.addEventListener("input", syncTtsVolumeLabels);
    backendAudioMonitorVolume.addEventListener("input", syncBackendAudioMonitorVolumeLabel);
    for (const input of backendAudioMonitorModeInputs) {
      input.addEventListener("change", () => {
        syncBackendAudioMonitorControls();
        syncAudioDeviceVisibility();
      });
    }
    backendAudioOutputPan.addEventListener("input", syncBackendAudioOutputPanLabel);
    commandAckSound.addEventListener("click", () => setCommandAckSoundEnabled(!commandAckSoundEnabled()));
    wakeWord.addEventListener("input", () => {
      syncBackendAudioMonitorControls();
      syncAudioDeviceVisibility();
    });
    for (const control of vadControls) {
      control.addEventListener("input", syncVadLabels);
    }
    for (const button of vadPresetButtons) {
      button.addEventListener("click", () => applyVadPreset(button.dataset.vadPreset || ""));
    }
    ttsTest.addEventListener("click", testSelectedTtsVoice);
    loadBrowserAudioDevices(false);
