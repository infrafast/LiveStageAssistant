(() => {
  "use strict";

  for (const src of [
    "/assets/web/app-main.js",
    "/assets/web/mcp-realtime.js",
    "/assets/web/config-unified.js"
  ]) {
    const script = document.createElement("script");
    script.src = src;
    script.async = false;
    document.head.appendChild(script);
  }
})();
