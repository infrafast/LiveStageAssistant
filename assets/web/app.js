(() => {
  "use strict";

  function loadClassicScript(src, onload) {
    const script = document.createElement("script");
    script.src = src;
    script.async = false;
    if (onload) script.addEventListener("load", onload, { once: true });
    document.head.appendChild(script);
  }

  loadClassicScript("/assets/web/app-main.js?v=rv2d-20260907i", () => {
    loadClassicScript("/assets/web/mcp-realtime.js?v=rv2d-20260907i", () => {
      loadClassicScript("/assets/web/config-unified.js?v=rv2d-20260907i");
    });
  });
})();
