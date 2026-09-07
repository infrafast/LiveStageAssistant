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
  loadClassicScript("/assets/web/config-bootstrap.js?v=rv2d-20260907d", () => {
    loadClassicScript("/assets/web/app-main.js?v=rv2d-20260907d", () => {
      loadClassicScript("/assets/web/mcp-realtime.js?v=rv2d-20260907d", () => {
        loadClassicScript("/assets/web/config-unified.js?v=rv2d-20260907d");
      });
    });
  });
})();
