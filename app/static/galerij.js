/* Lightbox voor elk .fotos-grid (klik op een foto: groot bekijken, vorige/volgende, details), en formulieren met data-fetch (opslaan zonder herladen).
   Dezelfde lightbox staat in site.js van de archief-repo; wijzig je hier iets, kijk dan ook daar. */
(function () {
  "use strict";
  var csrf = (document.querySelector('meta[name="csrf"]') || {}).content || "";

  /* ---------- lightbox ---------- */
  var lb = null, lijst = [], idx = -1;
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function bouw() {
    lb = document.createElement("div");
    lb.className = "lb"; lb.setAttribute("role", "dialog"); lb.setAttribute("aria-label", "Foto groot");
    lb.innerHTML = '<img alt=""><div class="lb-bar"><div class="lb-txt"></div><a class="lb-link" href="#">Details</a><span class="lb-n"></span></div>' +
      '<button class="lb-prev" aria-label="Vorige">&#8249;</button><button class="lb-next" aria-label="Volgende">&#8250;</button><button class="lb-sluit" aria-label="Sluiten">&times;</button>';
    lb.querySelector(".lb-prev").addEventListener("click", function (e) { e.stopPropagation(); toon(idx - 1); });
    lb.querySelector(".lb-next").addEventListener("click", function (e) { e.stopPropagation(); toon(idx + 1); });
    lb.querySelector(".lb-sluit").addEventListener("click", sluit);
    lb.addEventListener("click", function (e) { if (e.target === lb || e.target.tagName === "IMG") toon(idx + 1); });
    document.addEventListener("keydown", function (e) {
      if (!lb || lb.hidden) return;
      if (e.key === "Escape") sluit();
      else if (e.key === "ArrowRight") toon(idx + 1);
      else if (e.key === "ArrowLeft") toon(idx - 1);
    });
    document.body.appendChild(lb);
  }
  function toon(i) {
    if (!lijst.length) return;
    idx = (i + lijst.length) % lijst.length;
    var a = lijst[idx];
    lb.querySelector("img").src = a.dataset.m;
    lb.querySelector(".lb-txt").innerHTML = (a.dataset.caption ? esc(a.dataset.caption) + " " : "") + (a.dataset.credit ? '<span class="muted">Foto: ' + esc(a.dataset.credit) + "</span>" : "");
    lb.querySelector(".lb-link").href = a.dataset.url || a.href;
    lb.querySelector(".lb-n").textContent = (idx + 1) + " / " + lijst.length;
    lb.hidden = false; document.body.classList.add("lb-open");
    var vlg = lijst[(idx + 1) % lijst.length]; if (vlg) { var p = new Image(); p.src = vlg.dataset.m; }
  }
  function sluit() { if (lb) { lb.hidden = true; document.body.classList.remove("lb-open"); } }
  document.addEventListener("click", function (e) {
    var a = e.target.closest(".fotos-grid a.ft[data-m]");
    if (!a || e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    if (!lb) bouw();
    lijst = Array.prototype.slice.call(a.closest(".fotos-grid").querySelectorAll("a.ft[data-m]"));
    toon(lijst.indexOf(a));
  });

  /* ---------- formulieren die zonder herladen opslaan (bewerken, verwijderen) ---------- */
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f.matches("form[data-fetch]")) return;
    e.preventDefault();
    if (f.dataset.bevestig && !confirm(f.dataset.bevestig)) return;
    var uit = f.querySelector(".form-uit"), fd = new FormData(f);
    if (!fd.get("csrf")) fd.set("csrf", csrf);
    if (uit) uit.textContent = "Opslaan…";
    fetch(f.action, { method: "POST", body: fd, credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        if (!x.ok) { if (uit) uit.textContent = x.j.fout || "Mislukt."; else alert(x.j.fout || "Mislukt."); return; }
        if (f.dataset.daarna) { location.href = f.dataset.daarna + (x.j.bericht ? (f.dataset.daarna.indexOf("?") >= 0 ? "&" : "?") + "bericht=" + encodeURIComponent(x.j.bericht) : ""); return; }
        if (uit) uit.textContent = "Opgeslagen.";
      })
      .catch(function () { if (uit) uit.textContent = "Geen verbinding."; });
  });
})();
