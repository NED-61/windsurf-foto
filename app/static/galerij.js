/* Formulieren met data-fetch (opslaan zonder herladen: bewerken, verwijderen, taggen). Klikken op een foto in een raster is een gewone link
   naar de fotopagina: daar staat de foto groot, met wie erop staat en vorige/volgende (geen lightbox; keuze van Max, 22 oktober 2026). */
(function () {
  "use strict";
  var csrf = (document.querySelector('meta[name="csrf"]') || {}).content || "";
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
