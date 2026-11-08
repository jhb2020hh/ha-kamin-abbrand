"use strict";
// Alles relativ, damit es hinter dem Ingress-Pfad von Home Assistant funktioniert.
const inhalt = document.getElementById("inhalt");
const STIL_FARBE = { standby: "--standby", tuer: "--tuer", anheizen: "--anheizen", abbrand: "--abbrand", ausbrand: "--ausbrand",
  nachlegen: "--nachlegen", glut: "--glut", abkuehlen: "--abkuehlen", fehler: "--fehler", wartung: "--wartung" };
const PHASE_FARBE = { "Standby": "--standby", "Tür offen": "--tuer", "Anheizen": "--anheizen", "Abbrand": "--abbrand", "Ausbrand": "--ausbrand",
  "Glut": "--glut", "Abkühlen": "--abkuehlen", "Anheizfehler": "--fehler", "Störung": "--fehler", "Wartung": "--wartung" };
const farbe = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function h(tag, eigenschaften, ...kinder) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(eigenschaften || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "klasse") el.className = v;
    else if (k === "stil") el.setAttribute("style", v);
    else if (k in el && k !== "list") el[k] = v;
    else el.setAttribute(k, v);
  }
  for (const kind of kinder.flat()) if (kind !== null && kind !== undefined && kind !== false) el.append(kind);
  return el;
}
const zeige = (...kinder) => inhalt.replaceChildren(...kinder.flat().filter(Boolean));
const zahl = (w, stellen = 0) => (w === null || w === undefined) ? "–" : Number(w).toLocaleString("de-DE", { maximumFractionDigits: stellen, minimumFractionDigits: stellen });
function dauer(s) { if (s === null || s === undefined) return "–"; s = Math.round(s); if (s < 90) return `${s} s`; const m = Math.round(s / 60); return m < 90 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`; }
const uhr = (z) => new Date(z * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

async function api(pfad, inhaltDaten) {
  const antwort = await fetch(pfad, inhaltDaten === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(inhaltDaten) });
  const daten = await antwort.json().catch(() => ({}));
  if (!antwort.ok) throw new Error(daten.fehler || `Fehler ${antwort.status}`);
  return daten;
}
let toastUhr;
function toast(text) { const t = document.getElementById("toast"); t.textContent = text; t.classList.add("zeigen"); clearTimeout(toastUhr); toastUhr = setTimeout(() => t.classList.remove("zeigen"), 3200); }
const melden = (f) => toast(f.message || String(f));

// ---- Start -------------------------------------------------------------
let infoOffen = false;
function wertKarte(titel, gross, einheit, zusatz, balken) {
  return h("div", { klasse: "karte wert" }, h("div", { klasse: "etikett" }, titel),
    h("div", { klasse: "gross" }, gross, h("small", {}, einheit)),
    zusatz && h("div", { klasse: "leise" }, zusatz),
    balken !== undefined && h("div", { klasse: "balken" }, h("i", { stil: `width:${Math.min(Math.max(balken || 0, 0), 100)}%` })));
}
function startZeichnen(s) {
  const f = `var(${STIL_FARBE[s.stil] || "--standby"})`;
  const karte = h("div", { klasse: "karte", stil: `--farbe:${f}` },
    h("div", { klasse: "status" },
      h("div", { klasse: "punkt" + (s.blinkt ? " blinkt" : "") }),
      h("div", {}, h("h1", {}, s.stil === "nachlegen" ? "Nachlegen" : (s.phase || "Keine Daten")),
        h("div", { klasse: "satz" }, s.satz),
        s.seit_s !== null && h("div", { klasse: "leise" }, `seit ${dauer(s.seit_s)}`)),
      s.info && h("button", { klasse: "infoknopf", title: "Was bedeutet das?", "aria-expanded": String(infoOffen), onclick: () => { infoOffen = !infoOffen; aktualisieren(); } }, "i")),
    infoOffen && s.info && h("div", { klasse: "infotext" }, s.info),
    s.fortschritt !== null && [h("div", { klasse: "fortschritt" }, h("i", { stil: `width:${s.fortschritt}%` })), h("div", { klasse: "leise" }, `Anheizen: ${s.fortschritt} % bis zum Abbrand`)]);

  const werte = h("div", { klasse: "raster" },
    wertKarte("Abgastemperatur", zahl(s.abgas), "°C", `Ziel ${zahl(s.ziel)} °C`, s.abgas !== null ? s.abgas / Math.max(s.ziel * 1.4, 1) * 100 : 0),
    wertKarte("Klappe", zahl(s.klappe_soll), "%", s.klappe_ist !== null ? `Iststellung ${zahl(s.klappe_ist)} %` : "Sollstellung", s.klappe_soll),
    s.zug !== null && wertKarte("Kaminzug", zahl(s.zug, 1), "Pa"),
    s.twt !== null && wertKarte("Wärmetauscher", zahl(s.twt, 1), "°C"),
    s.puffer_unten !== null && wertKarte("Puffer unten", zahl(s.puffer_unten, 1), "°C"));

  const band = h("div", { klasse: "karte", stil: `--farbe:${f}` }, h("div", { klasse: "etikett" }, "Ablauf"),
    h("div", { klasse: "band" }, s.phasenband.map((p) => h("span", { klasse: p === s.phase ? "aktiv" : "" }, p))));

  const app = s.app.neu;
  const unten = h("div", { klasse: "karte" }, h("div", { klasse: "zeile" },
    h("div", {}, h("div", { klasse: "etikett" }, "Leistung"),
      s.leistung ? h("div", { klasse: "modus" }, ["Eco", "Normal", "Power"].map((m) => h("span", { klasse: m === s.leistung ? "aktiv" : "" }, m))) : h("div", { klasse: "leise" }, "Kein Leistungsmodus vorhanden")),
    h("div", {}, h("div", { klasse: "etikett" }, "Die App würde jetzt"), app ? h("div", {}, `${app.phase}, Klappe ${app.klappe} %`) : h("div", { klasse: "leise" }, "noch keine Berechnung")),
    s.tuerkontakt_vorhanden && s.tuer_offen !== null && h("div", {}, h("div", { klasse: "etikett" }, "Tür"), h("div", {}, s.tuer_offen ? "offen" : "geschlossen"))));

  const fehlt = s.fehlende_pflicht.length > 0 && h("div", { klasse: "karte" }, h("h2", {}, "Noch einzurichten"),
    h("p", {}, `Bitte unter Ein-/Ausgänge zuweisen: ${s.fehlende_pflicht.join(", ")}.`),
    h("p", { stil: "margin-top:10px" }, h("button", { klasse: "haupt", onclick: () => gehe("eingaenge") }, "Jetzt zuweisen")));
  zeige(...[fehlt, karte, werte, band, unten].filter(Boolean));
}

// ---- Verlauf -----------------------------------------------------------
let stunden = 3;
function grafik(d) {
  const B = 860, H = 340, L = 44, R = 40, O = 12, U = 44;
  const breit = B - L - R, hoch = H - O - U;
  const n = d.zeit.length;
  if (n < 2) return h("p", { klasse: "leise" }, "Für diesen Zeitraum liegen noch keine Daten vor.");
  const z0 = d.zeit[0], z1 = d.zeit[n - 1], x = (z) => L + (z - z0) / Math.max(z1 - z0, 1) * breit;
  const tMax = Math.max(100, ...d.t.filter((v) => v !== null)) * 1.08, yT = (v) => O + hoch - v / tMax * hoch, yK = (v) => O + hoch - v / 100 * hoch;
  const zugWerte = d.zug.filter((v) => v !== null), zugMax = Math.max(10, ...zugWerte), yZ = (v) => O + hoch - v / zugMax * hoch;
  const NS = "http://www.w3.org/2000/svg";
  const s = (tag, a, text) => { const e = document.createElementNS(NS, tag); for (const k in a) e.setAttribute(k, a[k]); if (text !== undefined) e.textContent = text; return e; };
  const svg = s("svg", { viewBox: `0 0 ${B} ${H}`, role: "img", "aria-label": "Verlauf von Temperatur, Klappe und Kaminzug" });
  for (let i = 0; i <= 4; i++) {
    const y = O + hoch * i / 4;
    svg.append(s("line", { x1: L, x2: B - R, y1: y, y2: y, stroke: farbe("--grid") }));
    svg.append(s("text", { x: L - 6, y: y + 4, "text-anchor": "end" }, Math.round(tMax * (1 - i / 4))));
    svg.append(s("text", { x: B - R + 6, y: y + 4 }, `${100 - 25 * i}%`));
  }
  for (let i = 0; i <= 5; i++) { const z = z0 + (z1 - z0) * i / 5; svg.append(s("text", { x: x(z), y: H - 6, "text-anchor": i === 0 ? "start" : i === 5 ? "end" : "middle" }, new Date(z * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }))); }
  // Phasenband unter der Grafik
  let start = 0;
  for (let i = 1; i <= n; i++) if (i === n || d.phase[i] !== d.phase[start]) {
    svg.append(s("rect", { x: x(d.zeit[start]), y: O + hoch + 6, width: Math.max(x(d.zeit[Math.min(i, n - 1)]) - x(d.zeit[start]), 1), height: 10, fill: farbe(PHASE_FARBE[d.phase[start]] || "--standby") }));
    start = i;
  }
  const linie = (reihe, y, f, breite) => { let p = "", neu = true; reihe.forEach((v, i) => { if (v === null || v === undefined) { neu = true; return; } p += `${neu ? "M" : "L"}${x(d.zeit[i]).toFixed(1)} ${y(v).toFixed(1)}`; neu = false; }); svg.append(s("path", { d: p, fill: "none", stroke: f, "stroke-width": breite, "stroke-linejoin": "round" })); };
  if (zugWerte.length) linie(d.zug, yZ, farbe("--ok"), 1.2);
  linie(d.klappe, yK, farbe("--blau"), 1.6);
  linie(d.t, yT, farbe("--akzent"), 2.2);
  const marke = s("line", { y1: O, y2: O + hoch, stroke: farbe("--muted"), visibility: "hidden" }); svg.append(marke);
  const tipp = h("div", { klasse: "tipp" }), rahmen = h("div", { klasse: "grafik" }, svg, tipp);
  svg.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect(), mx = (e.clientX - r.left) / r.width * B, z = z0 + (mx - L) / breit * (z1 - z0);
    let i = d.zeit.findIndex((w) => w >= z); if (i < 0) i = n - 1;
    marke.setAttribute("x1", x(d.zeit[i])); marke.setAttribute("x2", x(d.zeit[i])); marke.setAttribute("visibility", "visible");
    tipp.textContent = `${uhr(d.zeit[i])} · ${d.phase[i] || "–"} · ${zahl(d.t[i])} °C · Klappe ${zahl(d.klappe[i])} %` + (d.zug[i] !== null ? ` · ${zahl(d.zug[i], 1)} Pa` : "");
    tipp.style.display = "block"; tipp.style.top = "6px"; tipp.style.left = `${Math.min(Math.max(e.clientX - r.left - tipp.offsetWidth / 2, 0), r.width - tipp.offsetWidth)}px`;
  });
  svg.addEventListener("pointerleave", () => { tipp.style.display = "none"; marke.setAttribute("visibility", "hidden"); });
  return rahmen;
}
async function verlaufZeichnen() {
  const anzeige = h("b", {}, `${stunden} h`), flaeche = h("div", {}, h("p", { klasse: "leise" }, "Wird geladen …"));
  const laden = async () => { try { flaeche.replaceChildren(grafik(await api(`api/verlauf?stunden=${stunden}`))); } catch (f) { flaeche.replaceChildren(h("p", {}, f.message)); } };
  zeige(h("div", { klasse: "kopf" }, h("h1", {}, "Verlauf")),
    h("div", { klasse: "karte" }, h("div", { klasse: "zeile" }, h("span", {}, "Zeitraum"), anzeige),
      h("input", { type: "range", min: 1, max: 12, step: 1, value: stunden, "aria-label": "Zeitraum in Stunden", oninput: (e) => { stunden = +e.target.value; anzeige.textContent = `${stunden} h`; }, onchange: laden })),
    h("div", { klasse: "karte" }, flaeche, h("div", { klasse: "legende" },
      [["--akzent", "Abgas (°C, links)"], ["--blau", "Klappe (%, rechts)"], ["--ok", "Kaminzug (skaliert)"]].map(([c, t]) => h("span", { stil: `--c:var(${c})` }, h("i"), t)), h("span", {}, "Farbband: Phase"))));
  laden();
}

// ---- Ein-/Ausgänge -----------------------------------------------------
function auswahlFeld(rolle, stand) {
  const feld = h("input", { type: "text", value: stand[rolle.schluessel] || "", placeholder: "Suchen oder leer lassen", autocomplete: "off", role: "combobox", "aria-label": rolle.titel, "aria-expanded": "false" });
  const liste = h("ul", { klasse: "liste", role: "listbox", hidden: true });
  let treffer = [], markiert = -1, uhrSuche;
  const zu = () => { liste.hidden = true; feld.setAttribute("aria-expanded", "false"); markiert = -1; };
  const waehlen = (id) => { feld.value = id; stand[rolle.schluessel] = id; zu(); };
  const zeigen = () => {
    liste.replaceChildren(...treffer.map((t, i) => h("li", { role: "option", klasse: i === markiert ? "markiert" : "", onmousedown: (e) => { e.preventDefault(); waehlen(t.id); } }, t.name, h("small", {}, `${t.id} · ${t.wert}${t.einheit ? " " + t.einheit : ""}`))));
    liste.hidden = treffer.length === 0; feld.setAttribute("aria-expanded", String(!liste.hidden));
  };
  const suchen = async () => { try { treffer = await api(`api/katalog?domains=${rolle.domains.join(",")}&q=${encodeURIComponent(feld.value)}`); markiert = -1; if (document.activeElement === feld) zeigen(); } catch (f) { melden(f); } };
  feld.addEventListener("focus", suchen);
  feld.addEventListener("input", () => { stand[rolle.schluessel] = feld.value.trim(); clearTimeout(uhrSuche); uhrSuche = setTimeout(suchen, 150); });
  feld.addEventListener("blur", zu);
  feld.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); markiert = (markiert + (e.key === "ArrowDown" ? 1 : -1) + treffer.length) % Math.max(treffer.length, 1); zeigen(); }
    else if (e.key === "Enter" && markiert >= 0) { e.preventDefault(); waehlen(treffer[markiert].id); }
    else if (e.key === "Escape") zu();
  });
  return h("div", { klasse: "auswahl" }, feld, liste);
}
const STUFE_TEXT = { pflicht: "Pflicht", empfohlen: "Empfohlen", optional: "Optional", erweitert: "Erweitert" };
function rolleZeile(r, stand) {
  return h("div", { klasse: "rolle" },
    h("div", {}, h("span", { klasse: "titel" }, r.titel), " ", h("span", { klasse: `chip ${r.stufe}` }, STUFE_TEXT[r.stufe]), h("div", { klasse: "leise" }, r.hilfe)),
    h("div", {}, auswahlFeld(r, stand),
      r.entitaet && r.wert !== null && h("div", { klasse: "aktuell" }, `${r.name || r.entitaet}: ${r.wert}${r.wert_einheit ? " " + r.wert_einheit : ""}${r.alter_s !== null ? ` · vor ${dauer(r.alter_s)}` : ""}`),
      r.warnung && h("div", { klasse: "warnung" }, r.warnung)));
}
async function eingaengeZeichnen() {
  const d = await api("api/rollen"), stand = {};
  d.rollen.forEach((r) => { if (r.entitaet) stand[r.schluessel] = r.entitaet; });
  const tuer = h("input", { type: "checkbox", checked: d.tuerkontakt_vorhanden });
  const nach = (f) => d.rollen.filter(f).map((r) => rolleZeile(r, stand));
  zeige(h("div", { klasse: "kopf" }, h("h1", {}, "Ein-/Ausgänge"), h("p", { klasse: "leise" }, "Hier legst du fest, welche Sensoren und Schalter aus Home Assistant die App verwendet.")),
    !d.verbunden && h("div", { klasse: "karte" }, "Keine Verbindung zu Home Assistant, die aktuellen Werte fehlen."),
    h("div", { klasse: "karte" }, h("label", { klasse: "schalter" }, tuer, h("span", {}, h("b", {}, "Es gibt einen Türkontakt"), h("div", { klasse: "leise" }, "Ohne Türkontakt erkennt die App das Nachlegen nur am Temperaturanstieg. Gilt nur für die App."))) ),
    h("div", { klasse: "karte" }, h("h2", {}, "Eingänge"), nach((r) => r.art === "eingang" && r.stufe !== "erweitert")),
    h("div", { klasse: "karte" }, h("h2", {}, "Ausgänge"), h("p", { klasse: "leise" }, "Werden geprüft und angezeigt. Geschaltet werden sie erst nach der Umschaltung."), nach((r) => r.art === "ausgang")),
    h("details", { klasse: "karte" }, h("summary", {}, "Erweitert"), nach((r) => r.stufe === "erweitert")),
    h("div", { klasse: "leiste" }, h("button", { klasse: "haupt", onclick: async () => {
      try { await api("api/rollen", { rollen: stand, tuerkontakt_vorhanden: tuer.checked }); toast("Gespeichert. Die Zuweisung gilt sofort."); eingaengeZeichnen(); } catch (f) { melden(f); }
    } }, "Speichern")));
}

// ---- Einstellungen -----------------------------------------------------
async function setzen(name, wert) { await api("api/parameter", { name, wert }); }
function feldZeile(f, neu) {
  const eingabe = h("input", { type: "number", min: f.minimum, max: f.maximum, step: f.schritt, value: f.wert, "aria-label": f.titel, onchange: async (e) => { try { await setzen(f.name, e.target.value === "" ? null : Number(e.target.value)); toast(`${f.titel} gespeichert`); neu(); } catch (x) { melden(x); neu(); } } });
  return h("div", { klasse: "feld" },
    h("div", {}, h("b", {}, f.titel), h("div", { klasse: "leise" }, f.hilfe)),
    h("div", { klasse: "eingabe" }, eingabe, h("span", { klasse: "leise" }, f.einheit)),
    h("div", { klasse: "unten" }, h("span", { klasse: "chip" + (f.eigen ? " eigen" : "") }, f.quelle),
      f.eigen && h("span", { klasse: "leise" }, `Automation: ${zahl(f.live, 2)} ${f.einheit}`),
      f.eigen && h("button", { klasse: "textknopf", onclick: async () => { await setzen(f.name, null); toast("Zurückgesetzt"); neu(); } }, "Zurücksetzen")));
}
async function einstellungenZeichnen() {
  const d = await api("api/parameter");
  zeige(h("div", { klasse: "kopf" }, h("h1", {}, "Einstellungen"), h("p", { klasse: "leise" }, "Änderungen wirken bis zur Umschaltung nur auf die Berechnung der App, nicht auf den Ofen.")),
    d.themen.map((t, i) => h("details", { klasse: "karte", open: i === 0 }, h("summary", {}, t.titel), t.felder.map((f) => feldZeile(f, einstellungenZeichnen)))));
}

// ---- Regler ------------------------------------------------------------
function schieber(f) {
  const anzeige = h("b", {}, `${zahl(f.wert, 2)} ${f.einheit}`);
  return h("div", { klasse: "schieber" }, h("div", { klasse: "zeile" }, h("span", {}, f.titel, " ", h("span", { klasse: "chip" + (f.eigen ? " eigen" : "") }, f.quelle)), anzeige),
    h("div", { klasse: "leise" }, f.hilfe),
    h("input", { type: "range", min: f.minimum, max: f.maximum, step: f.schritt, value: f.wert, "aria-label": f.titel,
      oninput: (e) => { anzeige.textContent = `${zahl(e.target.value, 2)} ${f.einheit}`; },
      onchange: async (e) => { try { await setzen(f.name, Number(e.target.value)); toast(`${f.titel} gilt ab sofort für die App`); } catch (x) { melden(x); reglerZeichnen(); } } }),
    f.eigen && h("button", { klasse: "textknopf", onclick: async () => { await setzen(f.name, null); reglerZeichnen(); } }, `Zurück auf Automation (${zahl(f.live, 2)})`));
}
function vorschlagZeigen(v) {
  if (!v) return null;
  if (!v.ok) return h("p", { klasse: "warnung" }, v.grund);
  const w = (t, x) => h("div", {}, h("div", { klasse: "etikett" }, t), h("b", {}, x));
  return h("div", {}, h("p", {}, v.grund), h("div", { klasse: "vorschlag" },
    w("Kp", zahl(v.kp, 2)), w("Tn", `${zahl(v.tn, 1)} min`), w("Totzeit Tu", `${zahl(v.tu_min, 2)} ± ${zahl(v.streuung_tu, 2)} min`), v.ks_tg !== null && w("Ks/Tg", `${zahl(v.ks_tg, 3)} ± ${zahl(v.streuung_ks, 3)}`)),
    h("button", { klasse: "haupt", onclick: async () => { try { await api("api/autotune/uebernehmen", {}); toast("Vorschlag für die App übernommen"); reglerZeichnen(); } catch (f) { melden(f); } } }, "Vorschlag übernehmen"));
}
async function reglerZeichnen() {
  const d = await api("api/parameter");
  const ergebnis = h("div", { stil: "margin-top:12px" }, vorschlagZeigen(d.vorschlag));
  const knopf = h("button", { klasse: "neben", onclick: async () => { knopf.disabled = true; knopf.textContent = "Wird berechnet …"; try { ergebnis.replaceChildren(vorschlagZeigen(await api("api/autotune/verlauf", {}))); } catch (f) { melden(f); } knopf.disabled = false; knopf.textContent = "Aus dem Verlauf berechnen"; } }, "Aus dem Verlauf berechnen");
  zeige(h("div", { klasse: "kopf" }, h("h1", {}, "Regler"), h("p", { klasse: "leise" }, "Bis zur Umschaltung wirken diese Werte nur auf die gerechnete Klappe. Wie der Ofen wirklich reagiert, zeigt sich erst danach.")),
    h("div", { klasse: "karte" }, d.regler.map(schieber)),
    h("div", { klasse: "karte" }, h("h2", {}, "Auto-Tune aus dem Verlauf"), h("p", { klasse: "leise" }, "Wertet aus, wie die Temperatur in den letzten sieben Tagen auf Klappenbewegungen reagiert hat. Das Ergebnis ist ein Startwert, den du bestätigst."), h("p", { stil: "margin-top:10px" }, knopf), ergebnis),
    h("div", { klasse: "karte" }, h("h2", {}, "Auto-Tune mit Testlauf"), h("p", { klasse: "leise" }, "Wie beim 3D-Drucker: Die Klappe pendelt im Abbrand um ihre Arbeitsstellung und die App misst die Antwort."), h("p", { stil: "margin-top:10px" }, h("button", { klasse: "neben", disabled: true }, "Testlauf starten")), h("p", { klasse: "warnung" }, d.autotune_aktiv_gesperrt)));
}

// ---- Service -----------------------------------------------------------
async function serviceZeichnen() {
  const d = await api("api/service");
  const zeile = (a, b) => h("tr", {}, h("td", {}, a), h("td", {}, b));
  const tok = h("code", {}, "••••••••");
  const i = d.integration || {};
  zeige(h("div", { klasse: "kopf" }, h("h1", {}, "Service")),
    h("div", { klasse: "karte" }, h("table", { klasse: "kv" }, h("tbody", {},
      zeile("Version", d.version), zeile("Verbindung zu HA", d.verbunden ? "verbunden" : "getrennt"), zeile("App läuft seit", dauer(d.laeuft_s)),
      zeile("Betriebsstunden", `${zahl(d.betrieb_h, 1)} h`), zeile("Verlaufsdaten", `${zahl(d.verlauf_mb, 1)} MB`),
      d.ohne_helfer && zeile("Betriebsart", "Ohne Live-Automation, Start im Standby"),
      Object.keys(d.fehler).length > 0 && zeile("Fehler", JSON.stringify(d.fehler))))),
    h("div", { klasse: "karte" }, h("h2", {}, "Integration in Home Assistant"), h("p", {}, i.text || "Noch kein Stand."),
      i.neustart_noetig && h("p", { klasse: "warnung" }, "Bitte Home Assistant einmal neu starten, dann erscheint die Integration."),
      h("table", { klasse: "kv", stil: "margin-top:8px" }, h("tbody", {}, zeile("Automatisch angeboten", i.discovery ? "ja" : "nein, bitte von Hand hinzufügen"), zeile("Adresse", `${d.host}:${d.port}`),
        zeile("Token", h("span", {}, tok, " ", h("button", { klasse: "textknopf", onclick: () => { tok.textContent = d.token; } }, "anzeigen")))))),
    h("div", { klasse: "karte" }, h("h2", {}, "Letzte Meldungen"), d.meldungen.length ? d.meldungen.map((m) => h("div", { klasse: "meldung" }, h("time", {}, uhr(m.zeit)), m.text)) : h("p", { klasse: "leise" }, "Noch keine Meldungen seit dem Start.")),
    h("div", { klasse: "karte" }, h("h2", {}, "Letzte Änderungen"), d.aenderungen.length ? d.aenderungen.map((m) => h("div", { klasse: "meldung" }, h("time", {}, uhr(m.zeit)), `${m.was}: ${typeof m.neu === "object" && m.neu !== null ? "geändert" : `${m.alt ?? "Automation"} → ${m.neu ?? "Automation"}`}`)) : h("p", { klasse: "leise" }, "Noch keine Änderungen.")));
}

// ---- Rahmen ------------------------------------------------------------
const SEITEN = { start: null, verlauf: verlaufZeichnen, eingaenge: eingaengeZeichnen, einstellungen: einstellungenZeichnen, regler: reglerZeichnen, service: serviceZeichnen };
let seite = "start";
async function aktualisieren() {
  if (seite !== "start") return;
  try { startZeichnen(await api("api/status")); } catch (f) { zeige(h("div", { klasse: "karte" }, `Keine Daten: ${f.message}`)); }
}
async function gehe(name) {
  seite = SEITEN.hasOwnProperty(name) ? name : "start";
  location.hash = seite;
  document.querySelectorAll(".navi button").forEach((b) => { if (b.dataset.seite === seite) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  if (seite === "start") return aktualisieren();
  try { await SEITEN[seite](); } catch (f) { zeige(h("div", { klasse: "karte" }, f.message)); }
}
document.querySelectorAll(".navi button").forEach((b) => b.addEventListener("click", () => gehe(b.dataset.seite)));
gehe(location.hash.slice(1) || "start");
setInterval(aktualisieren, 5000);
