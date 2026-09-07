(() => {
  "use strict";

  function reportBootError(src, error) {
    const detail = String(error?.message || error?.reason?.message || error?.reason || error || "").trim();
    console.error(`LSA web module failed: ${src}`, error || "");
    const target = document.querySelector("#meta");
    if (target) {
      const moduleName = String(src || "frontend").split("/").pop().split("?")[0];
      target.textContent = detail ? `Web error (${moduleName}): ${detail}` : `Web module unavailable: ${moduleName}`;
      target.classList.add("error");
    }
  }

  window.addEventListener("error", (event) => {
    const source = event.filename || "frontend";
    reportBootError(source, event.error || event.message || event);
  });
  window.addEventListener("unhandledrejection", (event) => {
    reportBootError("promise", event.reason || event);
  });

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
  loadClassicScript("/assets/web/config-bootstrap.js?v=cfg9-20260907g", () => {
    loadClassicScript("/assets/web/app-main.js?v=cfg9-20260907g", () => {
      loadClassicScript("/assets/web/mcp-realtime.js?v=cfg9-20260907g", () => {
        loadClassicScript("/assets/web/config-unified.js?v=cfg9-20260907g");
      });
    });
  });
})();
