(function () {
  "use strict";
  var script = document.currentScript;
  if (!script || script.dataset.webChatLoaded === "true") return;
  script.dataset.webChatLoaded = "true";
  var key = script.dataset.installationKey;
  if (!key) return;
  var source = new URL(script.src);
  var app = (script.dataset.appUrl || source.origin).replace(/\/$/, "");
  var api = (script.dataset.apiUrl || app + "/api/v1").replace(/\/$/, "");
  var locale = /^(ru|uz|en)$/.test(script.dataset.locale || "")
    ? script.dataset.locale
    : "ru";
  var button = document.createElement("button");
  button.type = "button";
  button.setAttribute("aria-label", "Open Web Chat");
  button.textContent = "Chat";
  button.style.cssText =
    "position:fixed;z-index:2147483646;right:20px;bottom:20px;border:0;border-radius:999px;background:#08764d;color:#fff;padding:14px 20px;font:700 15px system-ui;box-shadow:0 12px 36px rgba(0,0,0,.2);cursor:pointer";
  var frame = document.createElement("iframe");
  frame.title = "Web Chat";
  frame.setAttribute("allow", "clipboard-write");
  frame.src = app + "/" + locale + "/widget/" + encodeURIComponent(key);
  frame.style.cssText =
    "display:none;position:fixed;z-index:2147483647;right:20px;bottom:84px;width:min(390px,calc(100vw - 24px));height:min(680px,calc(100vh - 108px));border:0;border-radius:22px;box-shadow:0 22px 70px rgba(0,0,0,.24);background:white";
  var config = null;
  fetch(
    api +
      "/public/web-chat/installations/" +
      encodeURIComponent(key) +
      "/config/",
    { credentials: "omit", headers: { Accept: "application/json" } },
  )
    .then(function (response) {
      if (!response.ok) throw new Error("unavailable");
      return response.json();
    })
    .then(function (value) {
      config = value;
      if (config.theme && config.theme.position === "left") {
        button.style.left = "20px";
        button.style.right = "auto";
        frame.style.left = "20px";
        frame.style.right = "auto";
      }
      if (frame.contentWindow)
        frame.contentWindow.postMessage(
          { type: "althair:webchat:init", config: config },
          app,
        );
    })
    .catch(function () {
      button.disabled = true;
      button.textContent = "Chat unavailable";
    });
  frame.addEventListener("load", function () {
    if (config && frame.contentWindow)
      frame.contentWindow.postMessage(
        { type: "althair:webchat:init", config: config },
        app,
      );
  });
  button.addEventListener("click", function () {
    var open = frame.style.display !== "none";
    frame.style.display = open ? "none" : "block";
    button.setAttribute("aria-expanded", String(!open));
  });
  document.body.appendChild(frame);
  document.body.appendChild(button);
})();
