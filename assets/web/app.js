(() => {
  "use strict";

  function reportBootError(src, error) {
    console.error(`LSA web module failed to load: ${src}`, error || "");
    const target = document.querySelector("#meta");
    if (target) {
      target.textContent = `Web module unavailable: ${src.split("/").pop().split("?")[0]}`;
      target.classList.add("error");
    }
  }

  function loadClassicScript(src, oncomplete) {
    const script = document.createElement("script");
    script.src = src;
    script.async = false;
    let finished = false;
    const finish = (ok, error = null) => {
      if (finished) return;
      finished = true;
      if (!ok) reportBootError(src, error);
      if (oncomplete) oncomplete(ok);
    };
    script.addEventListener("load", () => finish(true), { once: true });
    script.addEventListener("error", (event) => finish(false, event), { once: true });
    document.head.appendChild(script);
  }

  // The wake bootstrap must run before app-main so app-main captures the single
  // canonical wake control. A failed optional module must never prevent the core
  // monitor UI from loading; every stage therefore advances on load *or* error.
  loadClassicScript("/assets/web/config-bootstrap.js?v=cfg9-20260907f", () => {
    loadClassicScript("/assets/web/app-main.js?v=cfg9-20260907f", () => {
      loadClassicScript("/assets/web/mcp-realtime.js?v=cfg9-20260907f", () => {
        loadClassicScript("/assets/web/config-unified.js?v=cfg9-20260907f");
      });
    });
  });
})();
