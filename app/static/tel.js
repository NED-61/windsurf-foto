/* Bezoekersteller van het Windsurf Wedstrijdarchief (windsurf-foto, app/teller.py): geen cookies, niets bewaard op je apparaat,
   geen IP-adressen bewaard. Voor een andere pagina op ned-61.com, in de <head>:
   <script defer src="https://homelab257.tailc2d973.ts.net:10000/static/tel.js"></script>
   De archiefsite zelf heeft dezelfde code in assets/site.js. */
(function () {
  if (location.protocol !== "https:") return;
  var s = document.currentScript;
  var base = s && s.src ? s.src.replace(/\/static\/tel\.js.*$/, "") : "";
  if (!base) return;
  var bericht = JSON.stringify({ p: location.pathname, r: document.referrer || "", w: (window.screen && screen.width) || 0 });
  try {
    if (navigator.sendBeacon && navigator.sendBeacon(base + "/api/tel", bericht)) return;
    fetch(base + "/api/tel", { method: "POST", body: bericht, keepalive: true, mode: "no-cors" });
  } catch (e) { /* tellen mag de pagina nooit hinderen */ }
})();
