(() => {
  const basePath = String(window.LSA_BASE_PATH || "").replace(/\/+$/, "");
  const apiUrl = (path) => `${basePath}${String(path || "").startsWith("/") ? path : `/${path}`}`;
  const seen = new Map();
  let activeChannel = null;
  let realtimeBusy = false;
  let lastUserTranscript = "";
  let lastAssistantTranscript = "";

  function cleanText(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function dedupe(role, text, key = "") {
    const cleaned = cleanText(text);
    if (!cleaned) return true;
    const now = Date.now();
    for (const [seenKey, time] of seen.entries()) {
      if (now - time > 120000) seen.delete(seenKey);
    }
    const finalKey = key || `${role}:${cleaned}`;
    if (seen.has(finalKey)) return true;
    seen.set(finalKey, now);
    return false;
  }

  async function postJson(path, body) {
    const response = await fetch(apiUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json().catch(() => ({}));
  }

  async function refreshSnapshot() {
    window.dispatchEvent(new CustomEvent("lsa-realtime-chat-updated"));
  }

  async function setRealtimeBusy(busy) {
    const nextBusy = Boolean(busy);
    if (realtimeBusy === nextBusy) return;
    realtimeBusy = nextBusy;
    try {
      await postJson("/api/realtime-chat-state", { assistant_busy: realtimeBusy });
    } catch (error) {
      console.warn("Could not update realtime chat busy state", error);
    }
    refreshSnapshot();
  }

  async function appendRealtimeMessage(role, text, options = {}) {
    const cleaned = cleanText(text);
    if (!cleaned) return;
    if (dedupe(role, cleaned, options.key)) return;
    try {
      await postJson("/api/realtime-chat-message", {
        role,
        text: cleaned,
        speak: Boolean(options.speak)
      });
    } catch (error) {
      console.warn("Could not append realtime chat message", error);
    }
    refreshSnapshot();
  }

  function sendRealtimeEvent(event) {
    if (!activeChannel || activeChannel.readyState !== "open") {
      throw new Error("Realtime data channel is not ready");
    }
    activeChannel.send(JSON.stringify(event));
  }

  function transcriptFromItem(item) {
    const parts = [];
    for (const content of item?.content || []) {
      const text = cleanText(content?.transcript || content?.text || content?.input_text || content?.output_text);
      if (text) parts.push(text);
    }
    return cleanText(parts.join(" "));
  }

  function completedTranscript(event) {
    return cleanText(
      event.transcript ||
      event.text ||
      event.delta ||
      transcriptFromItem(event.item) ||
      transcriptFromItem(event.output_item)
    );
  }

  function isResponseBusyEvent(type) {
    return type === "response.created"
      || type === "response.output_item.added"
      || type === "response.content_part.added"
      || type === "response.audio.delta"
      || type === "response.audio_transcript.delta"
      || type === "response.text.delta"
      || type === "response.output_text.delta";
  }

  function isResponseDoneEvent(type) {
    return type === "response.done"
      || type === "response.cancelled"
      || type === "response.failed";
  }

  async function handleRealtimeEvent(event) {
    const type = String(event?.type || "");
    if (!type) return;

    if (isResponseBusyEvent(type)) {
      setRealtimeBusy(true);
    }

    if (type === "conversation.item.input_audio_transcription.completed") {
      const text = completedTranscript(event);
      if (text && text !== lastUserTranscript) {
        lastUserTranscript = text;
        appendRealtimeMessage("user", text, { key: event.item_id ? `user:${event.item_id}` : "" });
      }
      return;
    }

    if (
      type === "response.audio_transcript.done" ||
      type === "response.output_text.done" ||
      type === "response.text.done" ||
      type === "response.output_item.done"
    ) {
      const text = completedTranscript(event);
      if (text && text !== lastAssistantTranscript) {
        lastAssistantTranscript = text;
        appendRealtimeMessage("assistant", text, {
          speak: false,
          key: event.response_id || event.item_id ? `assistant:${event.response_id || event.item_id}` : ""
        });
      }
    }

    if (isResponseDoneEvent(type)) {
      setRealtimeBusy(false);
    }
  }

  function attachRealtimeChannel(channel) {
    if (!channel || channel.label !== "oai-events") return;
    activeChannel = channel;
    channel.addEventListener("open", () => {
      activeChannel = channel;
      console.info("LiveStageAssistant Realtime chat channel connected");
    });
    channel.addEventListener("message", (event) => {
      try {
        handleRealtimeEvent(JSON.parse(event.data));
      } catch (error) {
        console.warn("Could not process realtime event", error);
      }
    });
    const clear = () => {
      if (activeChannel === channel) activeChannel = null;
      setRealtimeBusy(false);
    };
    channel.addEventListener("close", clear);
    channel.addEventListener("error", clear);
  }

  const originalCreateDataChannel = RTCPeerConnection.prototype.createDataChannel;
  RTCPeerConnection.prototype.createDataChannel = function createDataChannel(label, options) {
    const channel = originalCreateDataChannel.call(this, label, options);
    attachRealtimeChannel(channel);
    return channel;
  };

  const originalClose = RTCPeerConnection.prototype.close;
  RTCPeerConnection.prototype.close = function close() {
    if (activeChannel && activeChannel.readyState !== "closed") {
      try { activeChannel.close(); } catch (_) {}
    }
    activeChannel = null;
    setRealtimeBusy(false);
    return originalClose.call(this);
  };

  function isStopCommand(value) {
    const normalized = cleanText(value)
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^\w'-]+/g, " ");
    return normalized.split(/\s+/).some((word) => ["stop", "stoppe", "stope", "arrete", "arreter", "annule", "annuler", "cancel"].includes(word));
  }

  async function submitRealtimeText(textarea, text) {
    if (isStopCommand(text)) {
      sendRealtimeEvent({ type: "response.cancel" });
      await setRealtimeBusy(false);
      textarea.value = "";
      textarea.style.height = "0px";
      return;
    }
    await appendRealtimeMessage("user", text, { key: `local-user:${Date.now()}:${text}` });
    textarea.value = "";
    textarea.style.height = "0px";
    await setRealtimeBusy(true);
    sendRealtimeEvent({
      type: "conversation.item.create",
      item: {
        type: "message",
        role: "user",
        content: [{ type: "input_text", text }]
      }
    });
    sendRealtimeEvent({ type: "response.create" });
  }

  function installComposerHook() {
    const form = document.querySelector("#inject-form");
    const textarea = document.querySelector("#inject-command");
    if (!form || !textarea) return;
    form.addEventListener("submit", (event) => {
      if (!activeChannel || activeChannel.readyState !== "open") return;
      const text = cleanText(textarea.value);
      if (!text) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      submitRealtimeText(textarea, text).catch((error) => {
        console.warn("Realtime text submit failed", error);
      });
    }, true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", installComposerHook, { once: true });
  } else {
    installComposerHook();
  }
})();
