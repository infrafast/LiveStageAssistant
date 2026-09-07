(() => {
  "use strict";

  const LLM_OPTIONS_URL = "/api/llm-options";

  function normalizeList(value) {
    return String(value || "")
      .split(/[,;|]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function installWakeSelect() {
    const current = document.querySelector("#wake-word");
    if (!current || current.tagName === "SELECT") return current;

    const select = document.createElement("select");
    select.id = "wake-word";
    select.className = current.className;
    select.name = current.name;
    select.disabled = current.disabled;
    select.title = current.title;
    select.setAttribute("aria-label", current.getAttribute("aria-label") || "Wake word");

    const disabled = document.createElement("option");
    disabled.value = "";
    disabled.textContent = "Disabled";
    select.appendChild(disabled);

    current.replaceWith(select);
    return select;
  }

  function appendWakeOption(select, value, label = "") {
    const normalized = String(value || "").trim();
    if (!normalized) return;
    if ([...select.options].some((option) => option.value === normalized)) return;
    const option = document.createElement("option");
    option.value = normalized;
    option.textContent = String(label || normalized).trim() || normalized;
    select.appendChild(option);
  }

  async function loadWakeOptions(select) {
    try {
      const response = await fetch(LLM_OPTIONS_URL, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const selected = String(data.selected_wake_word || "").trim();

      // WAKE_WORD stores the spoken activation word. Prefer configured model
      // names as selector values; model paths are detector implementation detail.
      for (const name of normalizeList(data.selected_backend_wake_word_model_names)) {
        appendWakeOption(select, name, name);
      }
      appendWakeOption(select, selected, selected);
      select.value = selected;

      window.dispatchEvent(new CustomEvent("lsa:wake-options-ready", {
        detail: { value: selected }
      }));
    } catch (error) {
      console.warn("Could not load wake-word options", error);
    }
  }

  const wakeSelect = installWakeSelect();
  if (wakeSelect) {
    window.LSA_WAKE_OPTIONS_READY = loadWakeOptions(wakeSelect);
  }
})();
