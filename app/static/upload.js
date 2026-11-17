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
    a.className = "ft"; a.href = "/foto/" + f.id; a.title = f.caption || "";
    a.innerHTML = '<img src="/i/' + esc(f.key) + '/t.jpg" width="' + f.tw + '" height="' + f.th + '" alt="">' + (f.status !== "zichtbaar" ? '<span class="badge badge-' + esc(f.status) + '">' + (f.status === "wacht" ? "wachtrij" : esc(f.status)) + "</span>" : "");
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
      } else if (x.status === 409 && j.vraag) {
        vraag(item, j);
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
  /* ---------- lijkt op een foto die er al staat: eerst de vraag, pas na het antwoord wordt hij opgeslagen ---------- */
  var balkKnop = document.createElement("button");
  balkKnop.type = "button"; balkKnop.className = "lijkt-balk"; balkKnop.hidden = true;
  document.body.appendChild(balkKnop);
  balkKnop.addEventListener("click", function () {
    var v = openVragen()[0]; if (!v) return;
    v.scrollIntoView({ behavior: "smooth", block: "center" });
    var k = v.querySelector(".lijkt-knoppen .btn"); if (k) k.focus({ preventScroll: true });
  });
  function openVragen() { return lijst.querySelectorAll("li.vraag"); }
  function telVragen() {
    var n = openVragen().length;
    balkKnop.hidden = !n;
    document.body.classList.toggle("met-lijkt-balk", n > 0);
    balkKnop.textContent = n === 1 ? "1 foto wacht op je antwoord: staat hij er al? Bekijk ↓" : n + " foto's wachten op je antwoord: staan ze er al? Bekijk ↓";
  }
  function statusTekst(f) { return f.status === "zichtbaar" ? "" : f.status === "wacht" ? "nog in de wachtrij" : f.status; }
  function vraag(item, j) {
    var li = item.li, st = li.querySelector(".st"), n = j.lijkt_op.length;
    var alleEigen = j.lijkt_op.every(function (f) { return f.eigen; });
    li.querySelector(".balk i").style.width = "100%";
    li.classList.add("vraag");
    st.textContent = "wacht op je antwoord"; st.className = "st waarsch";
    var p = document.createElement("div");
    p.className = "lijkt"; p.setAttribute("role", "group"); p.setAttribute("aria-label", "Staat deze foto er al?");
    p.innerHTML = '<p class="lijkt-kop"><strong>Staat deze foto er al?</strong> Hij lijkt op ' + (n === 1 ? "een foto" : n + " foto's") +
      (alleEigen ? " die je zelf al hebt geüpload" : " die al op de site " + (n === 1 ? "staat" : "staan")) + ". Vergelijk ze en kies: pas daarna wordt hij geüpload.</p>" +
      '<div class="lijkt-fotos"><figure class="jij"><img src="' + esc(j.voorbeeld) + '" alt="Jouw foto"><figcaption><strong>Jouw foto</strong><br><small class="muted">' + esc(item.file.name) + "</small></figcaption></figure>" +
      j.lijkt_op.map(function (f) {
        var s = statusTekst(f);
        return '<figure><a href="/foto/' + Number(f.id) + '" target="_blank" rel="noopener"><img src="/i/' + esc(f.key) + '/t.jpg" alt="De foto die er al staat"></a>' +
          "<figcaption><strong>Staat er al" + (f.eigen ? " (van jou)" : "") + '</strong><br><small class="muted">' + esc(f.wedstrijd_naam) + (s ? " · " + esc(s) : "") + "</small></figcaption></figure>";
      }).join("") + "</div>" +
      '<div class="lijkt-knoppen"><button type="button" class="btn" data-antwoord="zelfde">Ja, dezelfde foto: niet uploaden</button>' +
      '<button type="button" class="btn btn-stil" data-antwoord="anders">Nee, een andere foto: toch uploaden</button></div>' +
      '<p class="small muted">Kies je "een andere foto", dan kijkt de beheerder nog even mee voordat hij op de site staat.</p>';
    li.appendChild(p);
    var eigen = p.querySelector(".jij img"), geprobeerd = false;
    eigen.addEventListener("error", function () {   // geen voorbeeld van de server: dan het bestand zelf (lukt niet met HEIC in de meeste browsers)
      if (!geprobeerd && window.URL) { geprobeerd = true; eigen.src = URL.createObjectURL(item.file); } else { eigen.alt = "(geen voorbeeld)"; }
    });
    p.querySelector(".lijkt-knoppen").addEventListener("click", function (e) {
      var b = e.target.closest("button[data-antwoord]"); if (b && !b.disabled) antwoord(item, j, b.dataset.antwoord, p);
    });
    telVragen();
  }
  function antwoord(item, j, a, p) {
    var li = item.li, st = li.querySelector(".st"), knoppen = p.querySelectorAll(".lijkt-knoppen button");
    function knoppenAan(aan) { Array.prototype.forEach.call(knoppen, function (k) { k.disabled = !aan; }); }
    function klaarMet() { li.classList.remove("vraag"); p.remove(); }
    knoppenAan(false);
    st.textContent = a === "zelfde" ? "bezig" : "bezig met uploaden"; st.className = "st";
    var fd = new FormData();
    fd.append("csrf", csrf); fd.append("token", j.token); fd.append("antwoord", a);
    fetch("/api/upload/antwoord", { method: "POST", body: fd, credentials: "same-origin" }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) { return { s: r.status, b: b }; });
    }).then(function (res) {
      var b = res.b;
      if (res.s === 200 && b.overgeslagen) {
        klaarMet(); li.classList.add("overgeslagen");
        st.textContent = "niet geüpload: deze foto staat er al"; st.className = "st";
      } else if (res.s === 200 && b.ok) {
        klaarMet();
        st.textContent = b.foto.status === "zichtbaar" ? "staat online" : "in de wachtrij";
        if (b.waarschuwing) { st.textContent += " · " + b.waarschuwing; st.className = "st waarsch"; } else { st.className = "st"; }
        toonKlaar(b.foto);
      } else if ([409, 410, 413, 415, 422].indexOf(res.s) >= 0) {   // definitief: staat er intussen al, verlopen, of geweigerd
        klaarMet();
        st.textContent = b.fout || ("niet geüpload (" + res.s + ")"); st.className = res.s === 409 ? "st waarsch" : "st fout";
      } else {                                                       // even niet gelukt: de vraag blijft staan
        knoppenAan(true);
        st.textContent = (b.fout || ("mislukt (" + res.s + ")")) + " Kies nog eens."; st.className = "st fout";
      }
      telVragen();
    }).catch(function () {
      knoppenAan(true);
      st.textContent = "geen verbinding: kies nog eens"; st.className = "st fout";
    });
  }

  input.addEventListener("change", function () { voegToe(input.files); input.value = ""; });
  drop.addEventListener("click", function (e) { if (e.target.tagName !== "LABEL") input.click(); });
  drop.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  ["dragenter", "dragover"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); }); });
  ["dragleave", "drop"].forEach(function (ev) { drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); }); });
  drop.addEventListener("drop", function (e) { if (e.dataTransfer && e.dataTransfer.files.length) voegToe(e.dataTransfer.files); });
  window.addEventListener("beforeunload", function (e) { if (bezig || wachtrij.length || openVragen().length) { e.preventDefault(); e.returnValue = ""; } });
})();
