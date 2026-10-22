/* Uploadpagina: wedstrijd kiezen, foto's slepen, twee tegelijk uploaden met voortgang, daarna bijschrift per foto. */
(function () {
  "use strict";
  var root = document.getElementById("upload");
  if (!root) return;
  var csrf = (document.querySelector('meta[name="csrf"]') || {}).content || "";
  var slug = root.dataset.slug || "";
  var evQ = document.getElementById("ev-q"), evHits = document.getElementById("ev-hits"), evGek = document.getElementById("ev-gekozen"), evKies = document.getElementById("ev-kiezen");
  var opties = document.getElementById("opties"), drop = document.getElementById("drop"), input = document.getElementById("bestanden"), lijst = document.getElementById("uploads");
  var klaarBlok = document.getElementById("klaar-blok"), klaar = document.getElementById("klaar"), naarWedstrijd = document.getElementById("naar-wedstrijd");
  var wachtrij = [], bezig = 0, MAX_TEGELIJK = 2;

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function datum(s) { if (!s) return ""; var d = new Date(s); return isNaN(d) ? s : d.toLocaleDateString("nl-NL", { day: "numeric", month: "short", year: "numeric" }); }

  /* ---------- wedstrijd kiezen ---------- */
  var t;
  function kies(e) {
    slug = e.slug;
    document.getElementById("ev-naam").textContent = e.name;
    document.getElementById("ev-meta").textContent = [e.date ? datum(e.date) + (e.date_end ? " tot " + datum(e.date_end) : "") : "", e.location || ""].filter(Boolean).join(", ");
    evGek.hidden = false; evKies.hidden = true; evHits.innerHTML = "";
    naarWedstrijd.href = "/wedstrijd/" + slug;
    history.replaceState(null, "", "/upload?wedstrijd=" + encodeURIComponent(slug));
    laadEigen();
  }
  function zoek() {
    fetch("/api/wedstrijden?q=" + encodeURIComponent(evQ.value)).then(function (r) { return r.json(); }).then(function (lst) {
      evHits.innerHTML = lst.length ? lst.map(function (e) {
        return '<button type="button" class="hit" data-slug="' + esc(e.slug) + '"><span>' + esc(e.name) + '</span><small>' + esc([e.date ? datum(e.date) : e.year, e.location].filter(Boolean).join(", ")) + '</small></button>';
      }).join("") : '<p class="none">Geen wedstrijd gevonden.</p>';
      evHits._lst = lst;
    });
  }
  if (evQ) {
    evQ.addEventListener("input", function () { clearTimeout(t); t = setTimeout(zoek, 200); });
    evQ.addEventListener("focus", function () { if (!evHits.innerHTML) zoek(); });
    evHits.addEventListener("click", function (e) {
      var b = e.target.closest(".hit"); if (!b) return;
      var ev = (evHits._lst || []).filter(function (x) { return x.slug === b.dataset.slug; })[0];
      if (ev) kies(ev);
    });
    document.getElementById("ev-anders").addEventListener("click", function () { evGek.hidden = true; evKies.hidden = false; evQ.focus(); if (!evHits.innerHTML) zoek(); });
  }
  if (slug) { naarWedstrijd.href = "/wedstrijd/" + slug; laadEigen(); }

  /* ---------- herkomst: eigen foto, met toestemming, of fotograaf onbekend ---------- */
  var TEKST = {
    eigen: "Ik heb deze foto's zelf gemaakt en mag ze hier plaatsen.",
    toestemming: "Ik heb toestemming van de fotograaf om deze foto's hier te plaatsen.",
    onbekend: "Ik weet niet wie deze foto's heeft gemaakt. Ik plaats ze te goeder trouw en ga akkoord dat ze worden weggehaald als de fotograaf dat vraagt."
  };
  function herkomst() { var r = opties.querySelector("input[name=herkomst]:checked"); return r ? r.value : "eigen"; }
  function toonHerkomst() {
    var h = herkomst(), fv = document.getElementById("fotograaf-veld"), lv = document.getElementById("licentie-veld"), fi = opties.fotograaf;
    fv.hidden = h === "onbekend"; lv.hidden = h === "onbekend"; document.getElementById("onbekend-uitleg").hidden = h !== "onbekend";
    document.getElementById("verklaring").textContent = TEKST[h];
    if (h === "eigen" && !fi.value) fi.value = fi.dataset.eigen || "";
    if (h === "toestemming" && fi.value === fi.dataset.eigen) fi.value = "";
    fi.placeholder = h === "toestemming" ? "naam van de fotograaf (verplicht)" : "";
    opties.toestemming.checked = false;
  }
  Array.prototype.forEach.call(opties.querySelectorAll("input[name=herkomst]"), function (r) { r.addEventListener("change", toonHerkomst); });
  toonHerkomst();

  /* ---------- eerder geüploade foto's van deze wedstrijd ---------- */
  function laadEigen() {
    if (!slug) return;
    fetch("/api/mijn?wedstrijd=" + encodeURIComponent(slug), { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : { fotos: [] }; }).then(function (j) {
      klaar.innerHTML = "";
      j.fotos.forEach(toonKlaar);
      klaarBlok.hidden = !j.fotos.length;
    });
  }
  function toonKlaar(f) {
    var a = document.createElement("a");
    a.className = "ft"; a.href = "/foto/" + f.id; a.dataset.m = f.m; a.dataset.url = "/foto/" + f.id; a.dataset.caption = f.caption || ""; a.dataset.credit = f.credit || "";
    a.innerHTML = '<img src="' + esc(f.t) + '" width="' + f.tw + '" height="' + f.th + '" alt="">' + (f.status !== "zichtbaar" ? '<span class="badge badge-' + esc(f.status) + '">' + (f.status === "wacht" ? "wachtrij" : esc(f.status)) + "</span>" : "");
    klaar.appendChild(a);
    klaarBlok.hidden = false;
  }

  /* ---------- bestanden kiezen ---------- */
  function voegToe(files) {
    if (!slug) { alert("Kies eerst de wedstrijd."); evQ && evQ.focus(); return; }
    if (herkomst() === "toestemming" && opties.fotograaf.value.trim().length < 2) { alert("Vul de naam van de fotograaf in."); opties.fotograaf.focus(); return; }
    if (!opties.toestemming.checked) { alert("Vink eerst de verklaring over de foto's aan."); opties.toestemming.focus(); return; }
    Array.prototype.forEach.call(files, function (f) {
      var li = document.createElement("li");
      li.innerHTML = "<span>" + esc(f.name) + ' <small class="muted">' + (f.size / 1e6).toFixed(1) + ' MB</small></span><span class="st">wacht</span><div class="balk"><i></i></div>';
      lijst.appendChild(li);
      wachtrij.push({ file: f, li: li });
    });
    volgende();
  }
  function volgende() {
    while (bezig < MAX_TEGELIJK && wachtrij.length) upload(wachtrij.shift());
  }
  function upload(item) {
    bezig++;
    var st = item.li.querySelector(".st"), balk = item.li.querySelector(".balk i");
    st.textContent = "bezig"; st.className = "st";
    var fd = new FormData();
    fd.append("csrf", csrf); fd.append("wedstrijd", slug); fd.append("licentie", opties.licentie.value); fd.append("fotograaf", opties.fotograaf.value); fd.append("toestemming", "ja"); fd.append("herkomst", herkomst());
    fd.append("bestand", item.file, item.file.name);
    var x = new XMLHttpRequest();
    x.open("POST", "/api/upload");
    x.upload.onprogress = function (e) { if (e.lengthComputable) balk.style.width = Math.round(e.loaded / e.total * 100) + "%"; };
    x.onload = function () {
      bezig--;
      var j = {}; try { j = JSON.parse(x.responseText); } catch (e) { /* leeg */ }
      if (x.status >= 200 && x.status < 300 && j.ok) {
        balk.style.width = "100%";
        st.textContent = j.foto.status === "zichtbaar" ? "staat online" : "in de wachtrij";
        if (j.waarschuwing) { st.textContent += " · " + j.waarschuwing; st.className = "st waarsch"; }
        toonKlaar(j.foto);
      } else if (x.status === 409) {
        st.textContent = j.fout || "staat er al"; st.className = "st waarsch";
      } else {
        st.innerHTML = esc(j.fout || ("mislukt (" + x.status + ")")) + ' <button type="button" class="link">opnieuw</button>'; st.className = "st fout";
        st.querySelector("button").addEventListener("click", function () { balk.style.width = "0"; wachtrij.push(item); volgende(); });
      }
      volgende();
    };
    x.onerror = function () { bezig--; st.innerHTML = 'geen verbinding <button type="button" class="link">opnieuw</button>'; st.className = "st fout"; st.querySelector("button").addEventListener("click", function () { wachtrij.push(item); volgende(); }); volgende(); };
    x.send(fd);
  }
  input.addEventListener("change", function () { voegToe(input.files); input.value = ""; });
  drop.addEventListener("click", function (e) { if (e.target.tagName !== "LABEL") input.click(); });
  drop.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  ["dragenter", "dragover"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); }); });
  ["dragleave", "drop"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); }); });
  drop.addEventListener("drop", function (e) { if (e.dataTransfer && e.dataTransfer.files.length) voegToe(e.dataTransfer.files); });
  window.addEventListener("beforeunload", function (e) { if (bezig || wachtrij.length) { e.preventDefault(); e.returnValue = ""; } });
})();
