(() => {
  "use strict";

  function loadClassicScript(src, onload) {
    const script = document.createElement("script");
    script.src = src;
    script.async = false;
    if (onload) script.addEventListener("load", onload, { once: true });
    document.head.appendChild(script);
  }

  // Common config controls must exist before app-main captures DOM references.
  loadClassicScript("/assets/web/config-bootstrap.js?v=cfg9-20260907e", () => {
    loadClassicScript("/assets/web/app-main.js?v=cfg9-20260907e", () => {
      loadClassicScript("/assets/web/mcp-realtime.js?v=cfg9-20260907e", () => {
        loadClassicScript("/assets/web/config-unified.js?v=cfg9-20260907e");
      });
    });
  });
})();
