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

// 401 kommt nie von der App, sondern von Home Assistant: Die Ingress-Sitzung ist abgelaufen, meist weil die Seite
// lange im Hintergrund lag. Eine neue Sitzung legt nur die HA-Oberfläche an, deshalb wird sie neu geladen.
// Höchstens einmal je Minute, damit eine dauerhaft verweigerte Anmeldung keine Schleife auslöst.
const NEU_GELADEN_KEY = "kamin_abbrand_neu_geladen";
function sitzungErneuern() {
  if (document.hidden) return false;    // Beim Zurückkehren wird sofort neu abgefragt, dann greift es.
  let zuletzt = 0;
  try { zuletzt = Number(sessionStorage.getItem(NEU_GELADEN_KEY)) || 0; } catch (_) { /* Speicher gesperrt */ }
  if (Date.now() - zuletzt < 60000) return false;
  try { sessionStorage.setItem(NEU_GELADEN_KEY, String(Date.now())); } catch (_) { return false; }
  try { window.top.location.reload(); } catch (_) { location.reload(); }
  return true;
}
async function api(pfad, inhaltDaten) {
  const antwort = await fetch(pfad, inhaltDaten === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(inhaltDaten) });
  if (antwort.status === 401) {
    throw new Error(sitzungErneuern() ? "Die Anmeldung bei Home Assistant ist abgelaufen, die Seite wird neu geladen …"
      : "Die Anmeldung bei Home Assistant ist abgelaufen. Bitte die Seite neu laden.");
  }
  const daten = await antwort.json().catch(() => ({}));
  if (!antwort.ok) throw new Error(daten.fehler || `Fehler ${antwort.status}`);
  return daten;
}
let toastUhr;
function toast(text) { const t = document.getElementById("toast"); t.textContent = text; t.classList.add("zeigen"); clearTimeout(toastUhr); toastUhr = setTimeout(() => t.classList.remove("zeigen"), 3200); }
const melden = (f) => toast(f.message || String(f));

// ---- Lauschen und Betrieb ----------------------------------------------
// Die Oberfläche zeigt nur an. Ob der Betrieb erlaubt ist, entscheidet der Server.
let modus = null;
let betriebWahl = null;
const bestaetigung = { co_melder: false, hersteller: false, verantwortung: false, ohne_watchdog: false };
const BESTAETIGUNG_TEXTE = [
  ["co_melder", "Im Aufstellraum hängt ein funktionierender CO-Melder."],
  ["hersteller", "Der Betrieb ist mit dem Ofenhersteller oder dem Schornsteinfeger geklärt."],
  ["verantwortung", "Ich nutze die Steuerung auf eigene Verantwortung. Eine falsche Klappenstellung kann das Feuer ersticken, dabei entsteht Kohlenmonoxid."]];
function modusAnzeigen(neu) {
  modus = neu;
  const b = document.getElementById("banner");
  b.hidden = false;
  b.classList.toggle("betrieb", neu === "betrieb");
  b.replaceChildren(...(neu === "betrieb"
    ? [h("b", {}, "Betrieb."), " Die App steuert die Klappe. Bei jeder Störung fährt sie auf 100 % und geht zurück auf Lauschen."]
    : [h("b", {}, "Lauschen."), " Die App rechnet mit und zeigt an. Sie schaltet nichts."]));
}
const imBetrieb = () => modus === "betrieb";
function haken(name, text) {
  return h("label", { klasse: "schalter" }, h("input", { type: "checkbox", checked: bestaetigung[name], onchange: (e) => { bestaetigung[name] = e.target.checked; } }), h("span", {}, text));
}
async function betriebSenden(ziel) {
  try {
    const antwort = await api("api/betrieb", ziel === "betrieb" ? { modus: ziel, bestaetigung } : { modus: ziel });
    betriebWahl = null;
    toast(ziel === "betrieb" ? "Betrieb: Die App steuert jetzt die Klappe." : antwort.klappe_offen ? "Lauschen: Die Klappe steht auf 100 %." : "Lauschen: Die Klappe konnte nicht geöffnet werden, bitte prüfen!");
  } catch (f) { melden(f); }
  aktualisieren();
}
function betriebKarte(s) {
  const b = s.betrieb, aktiv = s.modus === "betrieb";
  if (betriebWahl === s.modus) betriebWahl = null;
  const knopf = (wert, text) => h("button", { type: "button", "aria-pressed": String(s.modus === wert), onclick: () => { betriebWahl = s.modus === wert ? null : wert; aktualisieren(); } }, text);
  const frageBetrieb = betriebWahl === "betrieb" && h("div", { klasse: "betrieb-frage" },
    b.offene_punkte.length > 0 && [h("b", {}, "Das fehlt noch, damit die App die Klappe steuern darf:"), h("ul", { klasse: "betrieb-liste" }, b.offene_punkte.map((p) => h("li", {}, p.text)))],
    !b.bestaetigt && [h("p", { stil: "margin-top:10px" }, h("b", {}, "Einmalig bestätigen")), ...BESTAETIGUNG_TEXTE.map(([n, t]) => haken(n, t))],
    !b.watchdog_zugewiesen && !b.ohne_watchdog_bestaetigt && haken("ohne_watchdog", "Es ist kein Watchdog zugewiesen. Ich bestätige, dass die Klappe bei einem Ausfall nicht schließen kann."),
    h("p", { stil: "margin-top:10px" }, h("button", { klasse: "haupt", onclick: () => betriebSenden("betrieb") }, "Betrieb einschalten"), " ", h("button", { klasse: "textknopf", onclick: () => { betriebWahl = null; aktualisieren(); } }, "Abbrechen")));
  const frageLauschen = betriebWahl === "lauschen" && h("div", { klasse: "betrieb-frage" },
    h("p", {}, "Die Klappe fährt auf 100 %, danach steuert die App nichts mehr. Hat die App die konkurrierende Automation ausgeschaltet, schaltet sie sie wieder ein."),
    h("p", { stil: "margin-top:10px" }, h("button", { klasse: "haupt", onclick: () => betriebSenden("lauschen") }, "Auf Lauschen schalten"), " ", h("button", { klasse: "textknopf", onclick: () => { betriebWahl = null; aktualisieren(); } }, "Abbrechen")));
  return h("div", { klasse: "karte", stil: (b.stoerung || (!aktiv && b.rueckfall)) ? "--farbe:var(--fehler)" : null },
    h("div", { klasse: "betrieb-kopf" },
      h("div", {}, h("h2", {}, "Klappensteuerung"), h("div", { klasse: "leise" }, aktiv ? "Die App stellt die Klappe." : "Die App rechnet nur mit.")),
      h("div", { klasse: "umschalter", role: "group", "aria-label": "Modus der App" }, knopf("lauschen", "Lauschen"), knopf("betrieb", "Betrieb"))),
    aktiv && b.anlaufschutz && h("p", { klasse: "leise", stil: "margin-top:8px" }, "Anlaufschutz: Die Klappe bleibt offen, bis der Ofen eingeordnet oder kalt ist."),
    b.stoerung && h("p", { klasse: "warnung", stil: "margin-top:8px" }, b.stoerung),
    !aktiv && b.rueckfall && h("p", { klasse: "warnung", stil: "margin-top:8px" }, `Zurückgefallen auf Lauschen (${uhr(b.rueckfall.zeit)}): ${b.rueckfall.grund}${b.rueckfall.klappe_offen ? " Die Klappe steht auf 100 %." : " Die Klappe konnte nicht geöffnet werden!"}`),
    frageBetrieb, frageLauschen);
}


// ---- Handbetrieb (Test und Wartung) ----------------------------------
let handEntwurf = null;   // Schieberwert, solange er noch nicht gesendet ist
let handDauer = null;     // gewählte Dauer vor dem Einschalten
async function handSenden(daten, text) {
  try { await api("api/hand", daten); if (text) toast(text); } catch (f) { melden(f); }
  handEntwurf = null; handDauer = null; aktualisieren();
}
function handKarte(s) {
  const d = s.hand;
  if (!d || !d.motorisiert) return null;
  const klappe = handEntwurf ?? d.klappe;
  const gesperrt = !d.moeglich;
  const gewaehlt = handDauer ?? d.dauer_min;
  const dauerKnopf = (m) => h("button", { type: "button", "aria-pressed": String(gewaehlt === m), disabled: gesperrt,
    onclick: () => { handDauer = m; if (d.aktiv) handSenden({ an: true, dauer_min: m }, `Handbetrieb läuft jetzt ${m} min`); else aktualisieren(); } }, `${m} min`);
  const schnell = (w) => h("button", { type: "button", klasse: "neben", disabled: gesperrt,
    onclick: () => handSenden({ an: d.aktiv, klappe: w }, `Klappe im Handbetrieb ${w} %`) }, `${w} %`);
  const anzeige = h("b", {}, `${zahl(klappe)} %`);
  return h("div", { klasse: "karte hand", stil: d.aktiv ? "--farbe:var(--wartung)" : null },
    h("div", { klasse: "betrieb-kopf" },
      h("div", {}, h("h2", {}, "Handbetrieb"),
        h("div", { klasse: "leise" }, d.aktiv ? `Klappe steht von Hand auf ${zahl(d.klappe)} %, noch ${dauer(d.rest_s)}.` : "Für Klappentest und Wartung. Endet von selbst.")),
      h("div", { klasse: "umschalter", role: "group", "aria-label": "Handbetrieb" },
        h("button", { type: "button", "aria-pressed": String(!d.aktiv), onclick: () => d.aktiv && handSenden({ an: false }, "Handbetrieb aus, die Automatik übernimmt.") }, "Automatik"),
        h("button", { type: "button", "aria-pressed": String(d.aktiv), disabled: gesperrt, onclick: () => !d.aktiv && handSenden({ an: true, dauer_min: gewaehlt, klappe }, `Handbetrieb an für ${gewaehlt} min`) }, "Hand"))),
    gesperrt && h("p", { klasse: "leise", stil: "margin-top:8px" }, d.grund),
    d.ha_an && h("p", { klasse: "leise", stil: "margin-top:8px" }, "Der Handbetrieb in Home Assistant ist eingeschaltet."),
    h("div", { klasse: "hand-zeile" },
      h("span", { klasse: "etikett" }, "Klappe"),
      h("input", { type: "range", min: 0, max: 100, step: 5, value: klappe, disabled: gesperrt, "aria-label": "Klappe im Handbetrieb in Prozent",
        oninput: (e) => { handEntwurf = +e.target.value; anzeige.textContent = `${handEntwurf} %`; },
        onchange: (e) => handSenden({ an: d.aktiv, klappe: +e.target.value }, `Klappe ${e.target.value} %`) }),
      anzeige),
    h("div", { klasse: "hand-zeile" }, [0, 50, 100].map(schnell)),
    h("div", { klasse: "hand-zeile" },
      h("span", { klasse: "etikett" }, "Dauer"),
      h("div", { klasse: "umschalter", role: "group", "aria-label": "Dauer des Handbetriebs" }, d.dauern_min.map(dauerKnopf)),
      d.aktiv && h("button", { type: "button", klasse: "neben", onclick: () => handSenden({ an: true }, `Handbetrieb läuft wieder ${d.dauer_min} min`) }, "Verlängern")));
}

// ---- Start -------------------------------------------------------------
let infoOffen = false;
function wertKarte(titel, gross, einheit, zusatz, balken) {
  return h("div", { klasse: "karte wert" }, h("div", { klasse: "etikett" }, titel),
    h("div", { klasse: "gross" }, gross, h("small", {}, einheit)),
    zusatz && h("div", { klasse: "leise" }, zusatz),
    balken !== undefined && h("div", { klasse: "balken" }, h("i", { stil: `width:${Math.min(Math.max(balken || 0, 0), 100)}%` })));
}
async function quittieren() {
  try {
    const d = await api("api/quittieren", {});
    toast(d.fehler_offen ? `Noch nicht quittiert: ${d.phase}. Ursache prüfen.` : `Quittiert, weiter mit ${d.phase}.`);
  } catch (f) { melden(f); }
  aktualisieren();
}
// Ansicht der Startseite: Einfach zeigt das Wichtigste, Experte auch die Werte des Reglers.
const ANSICHT_KEY = "kamin_ansicht";
function ansichtLesen() {
  try { return localStorage.getItem(ANSICHT_KEY) === "experte" ? "experte" : "einfach"; } catch (_) { return "einfach"; }
}
let ansicht = ansichtLesen();
function ansichtWaehlen(neu) {
  ansicht = neu;
  try { localStorage.setItem(ANSICHT_KEY, neu); } catch (_) { /* Speicher gesperrt */ }
  aktualisieren();
}
function ansichtUmschalter() {
  return h("div", { klasse: "ansicht-wahl" }, h("div", { klasse: "modus", role: "group", "aria-label": "Ansicht" },
    [["einfach", "Einfach"], ["experte", "Experte"]].map(([w, text]) => h("button", { type: "button", "aria-pressed": String(ansicht === w), onclick: () => ansicht !== w && ansichtWaehlen(w) }, text))));
}
function pumpeKarte(s) {
  if (s.pumpe === null && s.pumpe_stellsignal === null) return null;
  const lauf = s.pumpe === null ? null : s.pumpe ? "läuft" : "steht";
  if (s.pumpe_stellsignal === null) return wertKarte("Pumpe", lauf, "");
  return wertKarte("Pumpe", zahl(s.pumpe_stellsignal, 1), "%", lauf, s.pumpe_stellsignal);
}
async function leistungWaehlen(modus) {
  try { const d = await api("api/leistung", { modus }); toast(`Leistung ${modus}: Ziel ${zahl(d.ziel)} °C`); aktualisieren(); } catch (f) { melden(f); }
}
function startZeichnen(s) {
  const experte = ansicht === "experte";
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
    wertKarte("Klappe", zahl(s.klappe_soll), "%", (s.hand && s.hand.aktiv ? "Handbetrieb · " : "") + (s.klappe_ist !== null ? `Iststellung ${zahl(s.klappe_ist)} %` : "Sollstellung"), s.klappe_soll),
    s.zug !== null && wertKarte("Kaminzug", zahl(s.zug, 1), "Pa"),
    s.twt !== null && wertKarte("Wärmetauscher", zahl(s.twt, 1), "°C"),
    s.puffer_unten !== null && wertKarte("Puffer unten", zahl(s.puffer_unten, 1), "°C"),
    pumpeKarte(s),
    s.reinigung && wertKarte("Reinigung Wärmetauscher", s.reinigung.faellig ? "fällig" : zahl(s.reinigung.rest_h, 0), s.reinigung.faellig ? "" : "h",
      `${zahl(s.reinigung.gefahren_h, 0)} von ${zahl(s.reinigung.intervall_h, 0)} Brennstunden`, s.reinigung.gefahren_h / Math.max(s.reinigung.intervall_h, 1) * 100),
    experte && s.gradient !== null && s.gradient !== undefined && wertKarte("Gradient", zahl(s.gradient, 1), "K/min"),
    experte && s.i_anteil !== null && wertKarte("I-Anteil", zahl(s.i_anteil, 1), "%", "Integralanteil des Reglers"),
    experte && s.feuer && s.t_maximum !== null && wertKarte("T-Maximum Phase", zahl(s.t_maximum), "°C", s.phase_seit !== null ? `Phase seit ${new Date(s.phase_seit * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr` : null));

  const ad = s.abbrand_dauer;
  const kennzahl = (titel, wert) => h("div", {}, h("div", { klasse: "etikett" }, titel), h("b", {}, wert));
  const minuten = (m) => m === null || m === undefined ? "–" : dauer(m * 60);
  const dauerKarte = ad && h("div", { klasse: "karte" }, h("h2", {}, "Abbrand-Dauer"),
    h("div", { klasse: "vorschlag" },
      kennzahl("Aktueller Abbrand", ad.aktuell_min === null ? "läuft nicht" : minuten(ad.aktuell_min)),
      kennzahl("Letzter Abbrand", minuten(ad.letzte_min)),
      kennzahl("Ø heute", minuten(ad.schnitt_heute_min)),
      kennzahl("Abbrände heute", zahl(ad.anzahl_heute)),
      kennzahl("Ø gesamt", minuten(ad.schnitt_gesamt_min)),
      kennzahl("Abbrände gesamt", zahl(ad.anzahl_gesamt))),
    h("p", { klasse: "leise" }, `Ein Abbrand endet unter ${zahl(ad.t_glut)} °C oder beim nächsten Nachlegen. Fehlstarts zählen nicht.`));
  const fehlerKarte = (s.stoerung || s.klappe_folgt_nicht || (s.betrieb && s.betrieb.stoerung)) && h("div", { klasse: "karte", stil: "--farbe:var(--fehler)" },
    h("h2", {}, "Fehler quittieren"),
    s.modus === "betrieb"
      ? [h("p", {}, "Ursache beheben, dann quittieren. Ist der Ofen kalt, geht die Steuerung zurück in Standby, sonst in die passende Phase. Besteht der Fehler weiter, meldet er sich wieder."),
         h("p", { stil: "margin-top:10px" }, h("button", { klasse: "haupt", onclick: quittieren }, "Fehler quittieren"))]
      : h("p", { klasse: "leise" }, "Im Lauschen führt die Automation in Home Assistant die Phase. Eine Störung endet dort, sobald der Fühler wieder Werte liefert, ein Anheizfehler nach 90 min."));
  const klappeWarnung = s.klappe_folgt_nicht && h("div", { klasse: "karte", stil: "--farbe:var(--fehler)" }, h("h2", {}, "Klappe folgt nicht"),
    h("p", {}, "Die Rückmeldung passt nicht zur Sollstellung. Bitte Antrieb, Verdrahtung und Rückmeldung prüfen."));

  const band = h("div", { klasse: "karte", stil: `--farbe:${f}` }, h("div", { klasse: "etikett" }, "Ablauf"),
    h("div", { klasse: "band" }, s.phasenband.map((p) => h("span", { klasse: p === s.phase ? "aktiv" : "" }, p))));

  const app = s.app.neu;
  const leistungKarte = h("div", { klasse: "karte" }, h("div", { klasse: "zeile" },
    h("div", {}, h("div", { klasse: "etikett" }, "Leistung"),
      h("div", { klasse: "modus", role: "group", "aria-label": "Leistung" }, ["Eco", "Normal", "Power"].map((m) => h("button", { type: "button", "aria-pressed": String(m === s.leistung), onclick: () => leistungWaehlen(m) }, m))),
      h("div", { klasse: "leise", stil: "margin-top:4px" }, `Ziel ${zahl(s.ziel)} °C`, s.leistung_eigen && s.leistung_live && s.leistung_live !== s.leistung ? ` · Automation läuft auf ${s.leistung_live}` : "", s.modus !== "betrieb" ? " · wirkt im Lauschen nicht auf den Ofen" : "")),
    h("div", {}, h("div", { klasse: "etikett" }, s.modus === "betrieb" ? "Die App steuert" : "Die App würde jetzt"), app ? h("div", {}, `${app.phase}, Klappe ${app.klappe} %`) : h("div", { klasse: "leise" }, "noch keine Berechnung")),
    s.tuerkontakt_vorhanden && s.tuer_offen !== null && h("div", {}, h("div", { klasse: "etikett" }, "Tür"), h("div", {}, s.tuer_offen ? "offen" : "geschlossen")),
    s.lueftung_frei !== null && h("div", {}, h("div", { klasse: "etikett" }, "Lüftung"), h("div", {}, s.lueftung_frei ? "frei" : "gesperrt"))));

  const r = s.reinigung;
  const reinigung = r && (r.faellig || r.bald) && h("div", { klasse: "karte", stil: `--farbe:var(${r.faellig ? "--fehler" : "--nachlegen"})` },
    h("h2", {}, r.faellig ? "Wärmetauscher reinigen" : "Reinigung bald fällig"),
    h("p", {}, r.faellig ? `Seit der letzten Reinigung ${zahl(r.gefahren_h, 0)} Brennstunden, eingestellt sind ${zahl(r.intervall_h, 0)}.`
      : `Noch etwa ${zahl(r.rest_h, 0)} von ${zahl(r.intervall_h, 0)} Brennstunden.`),
    h("p", { klasse: "leise" }, "Wenn du gereinigt hast, bestätige es. Dann beginnt die Zählung neu."),
    h("p", { stil: "margin-top:10px" }, h("button", { klasse: "haupt", onclick: async () => { try { await api("api/reinigung/bestaetigen", {}); toast("Reinigung gespeichert."); aktualisieren(); } catch (f) { melden(f); } } }, "Gereinigt")));
  const wa = s.wasser;
  const WASSER_FARBE = { ok: "--ok", hinweis: "--nachlegen", warnung: "--warn", alarm: "--fehler" };
  const WASSER_TITEL = { ok: "Wasserseite in Ordnung", hinweis: "Wasserseite: Hinweis", warnung: "Wasserseite: Warnung", alarm: "Wasserseite: Alarm" };
  const wasser = wa && (wa.stufe !== "ok" || wa.leistung_kw !== null || wa.energie_heute_kwh !== null || wa.leistung_problem || wa.energie_problem) &&
    h("div", { klasse: "karte", stil: `--farbe:var(${WASSER_FARBE[wa.stufe]})` },
      h("h2", {}, WASSER_TITEL[wa.stufe]),
      wa.befunde.map((b) => h("div", { klasse: "meldung" }, h("span", { klasse: "chip" }, b.stufe === "alarm" ? "Alarm" : b.stufe === "warnung" ? "Warnung" : "Hinweis"), " ", b.text,
        b.wert !== null && h("span", { klasse: "leise" }, ` (jetzt ${zahl(b.wert, 1)} °C)`))),
      (wa.leistung_kw !== null || wa.energie_heute_kwh !== null) && h("div", { klasse: "raster", stil: "margin-top:8px" },
        wa.leistung_kw !== null && wertKarte("Wärmeleistung", zahl(wa.leistung_kw, 1), "kW"),
        wa.energie_heute_kwh !== null && wertKarte("Wärme heute", zahl(wa.energie_heute_kwh, 1), "kWh")),
      wa.leistung_problem && h("div", { klasse: "warnung" }, `Leistung: ${wa.leistung_problem}`),
      wa.energie_problem && h("div", { klasse: "warnung" }, `Energie: ${wa.energie_problem}`),
      h("p", { klasse: "leise", stil: "margin-top:8px" }, "Die App beobachtet nur und schaltet auf der Wasserseite nichts. Sie ersetzt keine Sicherheitseinrichtung."));
  const fehlt = s.fehlende_pflicht.length > 0 && h("div", { klasse: "karte" }, h("h2", {}, "Noch einzurichten"),
    h("p", {}, `Bitte unter Ein-/Ausgänge zuweisen: ${s.fehlende_pflicht.join(", ")}.`),
    h("p", { stil: "margin-top:10px" }, h("button", { klasse: "haupt", onclick: () => gehe("eingaenge") }, "Jetzt zuweisen")));
  const einrichtung = s.einrichtung.length > 0 && h("div", { klasse: "karte" }, h("h2", {}, "Einrichtung noch nicht fertig"),
    h("p", { klasse: "leise" }, `${s.einrichtung.length === 1 ? "Eine Angabe fehlt" : `${s.einrichtung.length} Angaben fehlen`} noch. Ofenart, Puffer und Wärmeerzeuger stehen unter Anlage, die Sensoren unter Ein-/Ausgänge.`),
    h("p", { stil: "margin-top:10px" }, h("button", { klasse: "textknopf", onclick: () => gehe("anlage") }, "Zur Anlage"), " ", h("button", { klasse: "textknopf", onclick: () => gehe("eingaenge") }, "Zu den Ein-/Ausgängen")));
  zeige(...[ansichtUmschalter(), einmessenStartKarte(s), fehlt,einrichtung, fehlerKarte, klappeWarnung, wasser, reinigung, leistungKarte, betriebKarte(s), karte, werte, dauerKarte, band, handKarte(s)].filter(Boolean));
}

// ---- Verlauf -----------------------------------------------------------
let stunden = 3;
const REIHEN = [
  { k: "t", name: "Abgas", einheit: "°C", farbe: "--akzent", achse: "t", breite: 2.2 },
  { k: "ziel", name: "Ziel", einheit: "°C", farbe: "--akzent", achse: "t", breite: 1.2, strich: "6 4" },
  { k: "twt", name: "Wärmetauscher", einheit: "°C", farbe: "--ausbrand", achse: "t", breite: 1.4, stellen: 1 },
  { k: "klappe", name: "Klappe Soll", einheit: "%", farbe: "--blau", achse: "k", breite: 1.6 },
  { k: "klappe_ist", name: "Klappe Ist", einheit: "%", farbe: "--blau", achse: "k", breite: 1.2, strich: "3 3" },
  { k: "zug", name: "Kaminzug", einheit: "Pa", farbe: "--ok", achse: "zug", breite: 1.2, stellen: 1 },
  { k: "leistung", name: "Wärmeleistung", einheit: "kW", farbe: "--glut", achse: "kw", breite: 1.8, stellen: 1 },
  { k: "pumpe", name: "Pumpe", einheit: "%", farbe: "--anheizen", achse: "k", breite: 1.2, stellen: 1 },
];
const VERLAUF_AUS_KEY = "kamin.verlauf.aus";
function verlaufAusLesen() {
  try { const w = JSON.parse(localStorage.getItem(VERLAUF_AUS_KEY)); if (Array.isArray(w)) return new Set(w); } catch (_) { /* Speicher gesperrt */ }
  return new Set(["twt"]);
}
function verlaufAusMerken() { try { localStorage.setItem(VERLAUF_AUS_KEY, JSON.stringify([...verlaufAus])); } catch (_) { /* Speicher gesperrt */ } }
const verlaufAus = verlaufAusLesen();
const reihenWerte = (d, k) => (d[k] || []).filter((v) => v !== null && v !== undefined);
const reiheAn = (d, r) => !verlaufAus.has(r.k) && reihenWerte(d, r.k).length > 0;
const zugGrenze = (d) => Math.max(10, ...reihenWerte(d, "zug"));
const kwGrenze = (d) => Math.max(5, ...reihenWerte(d, "leistung"));
// Optionen o (alle frei): reihen statt der Auswahl im Verlauf, breite/hoehe der viewBox, tuer (schmales Band aus d.tuer),
// marker [{zeit, text, titel}] als dünne senkrechte Striche, zusatz(g) zeichnet eigene Ebenen, klasse für den Rahmen.
function grafik(d, o = {}) {
  const B = o.breite || 860, H = o.hoehe || 340, L = 44, R = 40, O = 12, U = 44 + (o.tuer ? 8 : 0);
  const breit = B - L - R, hoch = H - O - U;
  const n = d.zeit.length;
  if (n < 2) return h("p", { klasse: "leise" }, "Für diesen Zeitraum liegen noch keine Daten vor.");
  const z0 = d.zeit[0], z1 = d.zeit[n - 1], x = (z) => L + (z - z0) / Math.max(z1 - z0, 1) * breit;
  const an = o.reihen || REIHEN.filter((r) => reiheAn(d, r));
  const tMax = Math.max(100, ...an.filter((r) => r.achse === "t").flatMap((r) => reihenWerte(d, r.k))) * 1.08;
  const skala = (grenze) => (v) => O + hoch - v / grenze * hoch;
  const achsen = { t: skala(tMax), k: skala(100), zug: skala(zugGrenze(d)), kw: skala(kwGrenze(d)) };
  const NS = "http://www.w3.org/2000/svg";
  const s = (tag, a, text) => { const e = document.createElementNS(NS, tag); for (const k in a) e.setAttribute(k, a[k]); if (text !== undefined) e.textContent = text; return e; };
  const svg = s("svg", { viewBox: `0 0 ${B} ${H}`, role: "img", "aria-label": `Verlauf: ${an.map((r) => r.name).join(", ") || "keine Linie ausgewählt"}` });
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
  // Tür offen als schmales Band unter dem Phasenband
  if (o.tuer && d.tuer) for (let i = 0; i < n; i++) if (d.tuer[i] === 1 && (i === 0 || d.tuer[i - 1] !== 1)) {
    let j = i; while (j + 1 < n && d.tuer[j + 1] === 1) j++;
    svg.append(s("rect", { x: x(d.zeit[i]), y: O + hoch + 19, width: Math.max(x(d.zeit[Math.min(j + 1, n - 1)]) - x(d.zeit[i]), 1), height: 5, fill: farbe("--tuer") }));
  }
  for (const m of o.marker || []) {
    if (m.zeit < z0 || m.zeit > z1) continue;
    const xm = x(m.zeit), y0 = O + hoch - 4, g = s("g", {});
    g.append(s("line", { x1: xm, x2: xm, y1: O, y2: O + hoch, stroke: farbe("--muted"), "stroke-width": 1, "stroke-dasharray": "2 3", opacity: 0.8 }));
    if (m.text) g.append(s("text", { x: xm, y: y0 + 10, transform: `rotate(-90 ${xm.toFixed(1)} ${y0})`, class: "marke-text" }, m.text));
    if (m.titel) g.append(s("title", {}, m.titel));
    svg.append(g);
  }
  const linie = (r) => {
    const y = achsen[r.achse];
    let p = "", neu = true;
    d[r.k].forEach((v, i) => { if (v === null || v === undefined) { neu = true; return; } p += `${neu ? "M" : "L"}${x(d.zeit[i]).toFixed(1)} ${y(v).toFixed(1)}`; neu = false; });
    const a = { d: p, fill: "none", stroke: farbe(r.farbe), "stroke-width": r.breite, "stroke-linejoin": "round" };
    if (r.strich) a["stroke-dasharray"] = r.strich;
    svg.append(s("path", a));
  };
  [...an].reverse().forEach(linie);
  // Zeit unter dem Zeiger, auf den Zeitraum begrenzt
  const zeitAus = (clientX) => { const r = svg.getBoundingClientRect(), z = z0 + ((clientX - r.left) / r.width * B - L) / breit * (z1 - z0); return Math.min(Math.max(z, z0), z1); };
  if (o.zusatz) o.zusatz({ svg, s, x, achsen, O, hoch, L, B, z0, z1, zeitAus });
  const marke = s("line", { y1: O, y2: O + hoch, stroke: farbe("--muted"), visibility: "hidden", "pointer-events": "none" }); svg.append(marke);
  const tipp = h("div", { klasse: "tipp" }), rahmen = h("div", { klasse: "grafik" + (o.klasse ? " " + o.klasse : "") }, svg, tipp);
  svg.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect(), z = zeitAus(e.clientX);
    let i = d.zeit.findIndex((w) => w >= z); if (i < 0) i = n - 1;
    marke.setAttribute("x1", x(d.zeit[i])); marke.setAttribute("x2", x(d.zeit[i])); marke.setAttribute("visibility", "visible");
    const teile = an.filter((q) => d[q.k][i] !== null && d[q.k][i] !== undefined).map((q) => `${q.name} ${zahl(d[q.k][i], q.stellen || 0)} ${q.einheit}`);
    tipp.textContent = [uhr(d.zeit[i]), d.phase[i] || "–", ...teile].join(" · ");
    tipp.style.display = "block"; tipp.style.top = "6px"; tipp.style.left = `${Math.min(Math.max(e.clientX - r.left - tipp.offsetWidth / 2, 0), r.width - tipp.offsetWidth)}px`;
  });
  svg.addEventListener("pointerleave", () => { tipp.style.display = "none"; marke.setAttribute("visibility", "hidden"); });
  return rahmen;
}
function reihenTitel(d, r) {
  if (r.achse === "zug") return `${r.name} (${r.einheit}, bis ${zahl(zugGrenze(d))})`;
  if (r.achse === "kw") return `${r.name} (${r.einheit}, bis ${zahl(kwGrenze(d))})`;
  return `${r.name} (${r.einheit})`;
}
// Lädt den offenen Verlauf neu, damit das Zeitfenster mitläuft; gesetzt von verlaufZeichnen.
let verlaufNachfuehren = null;
const VERLAUF_TAKT_MS = 10000;
async function verlaufZeichnen() {
  const anzeige = h("b", {}, `${stunden} h`), flaeche = h("div", {}, h("p", { klasse: "leise" }, "Wird geladen …")), legende = h("div", { klasse: "legende", role: "group", "aria-label": "Linien ein- und ausschalten" });
  let daten = null;
  const zeichnen = () => {
    flaeche.replaceChildren(grafik(daten));
    legende.replaceChildren(...REIHEN.map((r) => {
      const leer = reihenWerte(daten, r.k).length === 0;
      return h("button", { type: "button", stil: `--c:var(${r.farbe})`, disabled: leer, "aria-pressed": String(!leer && !verlaufAus.has(r.k)), title: leer ? "In diesem Zeitraum keine Daten" : "Ein- oder ausschalten",
        onclick: () => { if (verlaufAus.has(r.k)) verlaufAus.delete(r.k); else verlaufAus.add(r.k); verlaufAusMerken(); zeichnen(); } }, h("i", {}), reihenTitel(daten, r));
    }), h("span", {}, "Farbband: Phase"));
  };
  // still = Nachführen im Hintergrund: nicht unter dem Zeiger wegzeichnen, bei einem Fehler die alte Grafik stehen lassen.
  let anfrage = 0;
  const laden = async (still = false) => {
    const nr = ++anfrage;
    try {
      const neu = await api(`api/verlauf?stunden=${stunden}`);
      if (nr !== anfrage || !flaeche.isConnected) return;
      const tipp = flaeche.querySelector(".tipp");
      if (still && tipp && tipp.style.display === "block") return;
      daten = neu; zeichnen();
    } catch (f) { if (!still && nr === anfrage) flaeche.replaceChildren(h("p", {}, f.message)); }
  };
  verlaufNachfuehren = () => laden(true);
  zeige(h("div", { klasse: "kopf" }, h("h1", {}, "Verlauf")),
    h("div", { klasse: "karte" }, h("div", { klasse: "zeile" }, h("span", {}, "Zeitraum"), anzeige),
      h("input", { type: "range", min: 1, max: 12, step: 1, value: stunden, "aria-label": "Zeitraum in Stunden", oninput: (e) => { stunden = +e.target.value; anzeige.textContent = `${stunden} h`; }, onchange: () => laden() })),
    h("div", { klasse: "karte" }, flaeche, legende));
  laden();
}

// ---- Kosten ------------------------------------------------------------
let kostenMenge = 1000;
const euro = (w, waehrung, stellen = 2) => w === null || w === undefined ? "–" : `${zahl(w, stellen)} ${waehrung}`;
const ART_TEXT = { gemessen: "gemessen", schaetzung: "Schätzung", gemischt: "teils gemessen, teils geschätzt", modell: "Schätzung", keine: "noch keine Daten" };
const artChip = (art) => art && h("span", { klasse: "chip" + (art === "gemessen" ? " eigen" : "") }, ART_TEXT[art] || art);
async function kostenSenden(daten, text) {
  try { await api("api/wirtschaft", daten); toast(text); kostenZeichnen(); } catch (f) { melden(f); }
}
function vergleichKarte(d) {
  const v = d.vergleich, w = d.waehrung, holz = v.zeilen.find((z) => z.schluessel === "holz");
  const menge = h("input", { type: "number", min: 1, step: "any", value: kostenMenge, inputmode: "decimal", "aria-label": "Nutzwärme in kWh",
    onchange: (e) => { const x = Number(e.target.value); if (x > 0) { kostenMenge = x; kostenZeichnen(); } else { toast("Bitte eine Menge über 0 eingeben."); e.target.value = kostenMenge; } } });
  const zeile = (z) => h("tr", {}, h("th", { scope: "row" }, z.titel, z.kostenlos && h("span", { klasse: "chip" }, "kostenlos")),
    h("td", {}, zahl(z.je_kwh, 4)), h("td", {}, zahl(z.kosten, 2)),
    h("td", {}, z.schluessel === "holz" || z.differenz === null ? "–" : `${z.differenz > 0 ? "+" : ""}${zahl(z.differenz, 2)}`));
  return [h("div", { klasse: "karte" }, h("h2", {}, "Brennstoffvergleich"),
      h("p", { klasse: "leise" }, "Was kostet dieselbe Nutzwärme mit Holz und mit deinen anderen Wärmeerzeugern? Der günstigste steht oben."),
      h("div", { klasse: "angaben", stil: "margin:8px 0" }, angabe("Nutzwärme", menge, h("span", { klasse: "leise" }, "kWh"))),
      v.zeilen.length > 0 ? h("div", { klasse: "matrix" }, h("table", { klasse: "kosten" },
        h("thead", {}, h("tr", {}, h("th", {}, "Wärmequelle"), h("th", {}, `${w}/kWh`), h("th", {}, `Summe ${w}`), h("th", {}, holz ? `Diff. ${w}` : ""))),
        h("tbody", {}, v.zeilen.map(zeile)))) : h("p", {}, "Noch nichts zu vergleichen."),
      holz && v.zeilen.length > 1 && h("p", { klasse: "leise", stil: "margin-top:8px" }, `Summe: Kosten für ${zahl(v.menge_kwh)} kWh Nutzwärme. Diff.: Unterschied zu Holz, plus heißt teurer, minus günstiger.`)),
    v.fehlt.length > 0 && h("div", { klasse: "karte" }, h("h2", {}, "Nicht im Vergleich"),
      v.fehlt.map((f) => h("div", { klasse: "meldung" }, h("b", {}, `${f.titel}: `), f.grund))),
    d.hinweise.length > 0 && h("div", { klasse: "karte" }, h("h2", {}, "Das fehlt noch"), d.hinweise.map((x) => h("div", { klasse: "meldung" }, x.text)),
      h("p", { stil: "margin-top:10px" }, h("button", { klasse: "neben", onclick: () => gehe("anlage") }, "Zur Anlage")))];
}
const AMORT_TITEL = { amortisiert: "Eingespielt", prognose: "Auf dem Weg", keine_amortisation: "Keine Amortisation", zu_wenig_daten: "Noch zu wenig Daten", unbekannt: "Noch nicht berechenbar" };
function amortisationKarte(d) {
  const a = d.amortisation, w = d.waehrung;
  const wert = (t, x) => h("div", {}, h("div", { klasse: "etikett" }, t), h("b", {}, x));
  const farbe_ = a.status === "keine_amortisation" ? "--fehler" : a.status === "amortisiert" ? "--ok" : "--abbrand";
  const tage = a.tage_gemessen + a.tage_modell;
  return h("div", { klasse: "karte", stil: `--farbe:var(${farbe_})` },
    h("div", { klasse: "zeile" }, h("h2", {}, "Amortisation"), artChip(a.art)),
    h("p", {}, h("b", {}, AMORT_TITEL[a.status] || a.status), a.hinweis && ` – ${a.hinweis}`),
    a.prozent !== null && [h("div", { klasse: "fortschritt" }, h("i", { stil: `width:${a.prozent}%` })), h("div", { klasse: "leise" }, `${zahl(a.prozent, 1)} % der Investition eingespielt`)],
    h("div", { klasse: "vorschlag", stil: "margin-top:10px" },
      wert("Netto bisher", euro(a.netto_eur, w, 0)), a.rest_eur !== null && wert("Noch offen", euro(a.rest_eur, w, 0)),
      a.prognose_jahre !== null && wert("Prognose", `${zahl(a.prognose_jahre, 1)} Jahre`), a.jahresertrag_eur != null && wert("Ertrag je Jahr", euro(a.jahresertrag_eur, w, 0)), wert("Kaminwärme", `${zahl(a.kaminwaerme_kwh, 0)} kWh`)),
    h("p", { klasse: "leise", stil: "margin-top:8px" }, `Netto = Einsparung ${euro(a.einsparung_eur, w, 0)} − Holz ${euro(a.holz_eur, w, 0)} − laufende Kosten ${euro(a.laufend_eur, w, 0)}. ` +
      (tage ? `Kamintage: ${a.tage_gemessen} gemessen, ${a.tage_modell} geschätzt.` : "") +
      (a.tage_unvollstaendig ? ` Bei ${a.tage_unvollstaendig} Tagen fehlt ein Preis, sie zählen ohne Einsparung.` : "")),
    a.art === "schaetzung" && h("p", { klasse: "leise" }, "Geschätzt heißt: aus dem Holz und deinen Nutzungsgraden gerechnet. Mit Energiezähler, Außentemperatur und Referenz wird gemessen."),
    d.amortisation_hinweise.map((x) => h("div", { klasse: "meldung" }, x.text)));
}
function heuteKarte(d) {
  const n = d.heute_auflagen;
  return h("div", { klasse: "karte" }, h("h2", {}, "Auflagen heute"),
    h("div", { klasse: "zeile" },
      h("div", {}, h("b", { stil: "font-size:1.6rem" }, String(n)), h("div", { klasse: "leise" }, d.kg_je_auflage ? `je ${zahl(d.kg_je_auflage, 1)} kg Holz` : "Holz je Auflage fehlt (unter Anlage), die Holzkosten bleiben leer")),
      h("div", { klasse: "umschalter", role: "group", "aria-label": "Auflagen korrigieren" },
        h("button", { type: "button", disabled: n === 0, "aria-label": "Eine Auflage weniger", onclick: () => kostenSenden({ aktion: "auflage", delta: -1 }, "Auflage abgezogen.") }, "−"),
        h("button", { type: "button", "aria-label": "Eine Auflage mehr", onclick: () => kostenSenden({ aktion: "auflage", delta: 1 }, "Auflage nachgetragen.") }, "+"))),
    h("p", { klasse: "leise", stil: "margin-top:8px" }, "Die App zählt, wenn das Feuer vom Anheizen in den Abbrand geht. Hat sie eine verpasst oder doppelt gezählt, korrigierst du hier."));
}
function tageKarte(d) {
  if (d.tage.length === 0) return h("div", { klasse: "karte" }, h("h2", {}, "Tageswerte"), h("p", { klasse: "leise" }, "Noch keine Tage gebucht. Die Buchführung beginnt mit dem nächsten Takt der App."));
  const w = d.waehrung;
  const zeile = (t) => h("tr", {}, h("th", { scope: "row" }, new Date(t.tag).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }), t.vorlaeufig && h("span", { klasse: "chip" }, "heute")),
    h("td", {}, zahl(t.auflagen)), h("td", {}, zahl(t.kaminwaerme_kwh, 0)), h("td", {}, zahl(t.holz_eur, 2)),
    h("td", {}, t.einsparung_eur === null ? "–" : zahl(t.einsparung_eur, 2)), h("td", {}, t.art ? (t.art === "gemessen" ? "gemessen" : "geschätzt") : "–"));
  return h("div", { klasse: "karte" }, h("h2", {}, "Tageswerte"),
    h("div", { klasse: "matrix" }, h("table", { klasse: "kosten tage" },
      h("thead", {}, h("tr", {}, h("th", {}, "Tag"), h("th", {}, "Aufl."), h("th", {}, "kWh"), h("th", {}, `Holz ${w}`), h("th", {}, `Spart ${w}`), h("th", {}, "Art"))),
      h("tbody", {}, d.tage.map(zeile)))),
    h("p", { klasse: "leise", stil: "margin-top:8px" }, "kWh ist die Kaminwärme. Gemessen wird ein Tag erst nach Mitternacht, heute gilt die Schätzung. Das Geld wird zum Preis des Tages gebucht."));
}
function einstellungenKarte(d) {
  const e = d.einstellungen, w = d.waehrung, werte = Object.fromEntries(Object.entries(e).map(([n, x]) => [n, x.wert]));
  const feld = (name, einheit, hilfe) => zahlAngabe(werte, name, e[name].titel + (hilfe ? ` (${hilfe})` : ""), einheit, e[name].vorgabe ?? "leer");
  const saldo = { ...d.saldo };
  const r = d.referenz;
  return [h("details", { klasse: "karte" }, h("summary", {}, "Amortisation einstellen"),
      h("div", { klasse: "angaben", stil: "margin-top:10px" }, feld("investition", w), feld("laufende_kosten_jahr", `${w}/Jahr`, "z. B. Schornsteinfeger, Wartung"), feld("jahreswaerme_kwh", "kWh", "für die Prognose")),
      h("h3", { stil: "margin-top:14px" }, "Referenz der bisherigen Heizung"),
      h("p", { klasse: "leise" }, "Verbrauch an einem Tag ohne Feuer: größter Wert aus a − b × Außentemperatur und Sockel, in kWh Endenergie je Tag. Ohne Referenz wird die Einsparung geschätzt."),
      h("div", { klasse: "angaben" }, feld("referenz_a", "kWh"), feld("referenz_b", "kWh/K"), feld("referenz_sockel", "kWh")),
      h("div", { klasse: "vorschlagbox" },
        h("b", {}, "Referenz lernen"), h("p", { klasse: "leise" }, r.ok ? `Vorschlag aus ${r.tage} Tagen ohne Feuer: a = ${zahl(r.a, 1)}, b = ${zahl(r.b, 2)}, Sockel = ${zahl(r.sockel, 1)}.` : r.grund),
        r.ok && h("button", { klasse: "neben", onclick: () => kostenSenden({ aktion: "referenz_uebernehmen" }, "Referenz übernommen.") }, "Vorschlag übernehmen")),
      h("h3", { stil: "margin-top:14px" }, "Aufteilung der Ofenwärme"),
      h("p", { klasse: "leise" }, "Mit Wärmemengenzähler gilt: Wasserwärme + Wasserwärme × Verhältnis × Anteil. Ohne Zähler rechnet die App aus dem Holz."),
      h("div", { klasse: "angaben" }, feld("raum_wasser_verhaeltnis", null), feld("aufstellraum_anteil", null)),
      h("div", { klasse: "leiste" }, h("button", { klasse: "haupt", onclick: () => kostenSenden({ aktion: "einstellungen", werte }, "Gespeichert.") }, "Speichern"))),
    h("details", { klasse: "karte" }, h("summary", {}, "Eröffnungssaldo"),
      h("p", { klasse: "leise", stil: "margin-top:10px" }, "Summen aus der Zeit vor der App, zum Beispiel aus deinen bisherigen Zählern. Sie gehen in die Amortisation ein."),
      h("div", { klasse: "angaben" }, Object.entries(d.saldo_felder).map(([n, t]) => zahlAngabe(saldo, n, t, n.endsWith("eur") ? w : n.endsWith("kwh") ? "kWh" : n === "holz_kg" ? "kg" : null, "0"))),
      h("div", { klasse: "leiste" }, h("button", { klasse: "haupt", onclick: () => kostenSenden({ aktion: "eroeffnung", werte: saldo }, "Eröffnungssaldo gespeichert.") }, "Speichern")))];
}
async function kostenZeichnen() {
  const d = await api(`api/wirtschaft?kwh=${encodeURIComponent(kostenMenge)}`);
  zeige(h("div", { klasse: "kopf" }, h("h1", {}, "Kosten"), h("p", { klasse: "leise" }, d.rechenhilfe)),
    d.amortisation && amortisationKarte(d), d.amortisation && heuteKarte(d), vergleichKarte(d), d.amortisation && tageKarte(d), d.amortisation && einstellungenKarte(d));
}

// ---- Anlage ------------------------------------------------------------
const EINHEIT_TEXT = { rm: "Raummeter", srm: "Schüttraummeter" };
const NUR_ENERGIE = ["kWh", "MWh"];
function angabe(titel, ...kinder) { return h("label", { klasse: "angabe" }, h("span", { klasse: "leise" }, titel), h("div", { klasse: "eingabe" }, kinder)); }
function klappliste(wert, einheiten, titel, beiWechsel) {
  return h("select", { "aria-label": titel, onchange: (e) => beiWechsel(e.target.value) }, einheiten.map(([w, t]) => h("option", { value: w, selected: w === wert }, t)));
}
function zahlAngabe(objekt, name, titel, einheit, vorgabe) {
  return angabe(titel, h("input", { type: "number", step: "any", value: objekt[name] ?? "", placeholder: vorgabe ?? "", "aria-label": titel, oninput: (e) => { objekt[name] = e.target.value === "" ? null : Number(e.target.value); } }), einheit && h("span", { klasse: "leise" }, einheit));
}
function rueckmeldungAngaben(p, r) {
  // Rückmeldung der Klappe: Messwert bei zu und auf, in der Einheit des Sensors. Leer heißt Vorgabe der Einheit.
  if (!r) return null;
  const einheit = r.einheit || "%";
  const vorgabe = r.vorgabe || [null, null];
  const jetzt = r.wert !== null && (r.prozent !== null
    ? `Jetzt ${zahl(r.wert, 2)} ${einheit} → ${zahl(r.prozent)} %`
    : `Jetzt ${zahl(r.wert, 2)} ${einheit}, ohne Werte für zu und auf nicht umrechenbar`);
  return h("div", { stil: "margin:0 0 12px" },
    h("div", { klasse: "etikett" }, "Rückmeldung der Klappe"),
    h("p", { klasse: "leise" }, `Welcher Messwert von ${r.entitaet} heißt zu und welcher auf? Leer lassen für die Vorgabe der Einheit. Ist zu größer als auf, gilt die Rückmeldung als invertiert.`),
    h("div", { klasse: "angaben" },
      zahlAngabe(p, "rueck_zu", "Rückmeldung bei zu (0 %)", einheit, vorgabe[0] !== null ? String(vorgabe[0]) : "eintragen"),
      zahlAngabe(p, "rueck_auf", "Rückmeldung bei auf (100 %)", einheit, vorgabe[1] !== null ? String(vorgabe[1]) : "eintragen")),
    jetzt && h("p", { klasse: "leise", stil: "margin-top:4px" }, jetzt, " (mit den gespeicherten Werten)"));
}
function jaNein(objekt, name, titel, hilfe, neu) {
  return h("label", { klasse: "schalter" }, h("input", { type: "checkbox", checked: objekt[name], onchange: (e) => { objekt[name] = e.target.checked; neu(); } }), h("span", {}, h("b", {}, titel), h("div", { klasse: "leise" }, hilfe)));
}
function hinweisKarte(hinweise, titel) {
  return hinweise.length > 0 && h("div", { klasse: "karte" }, h("h2", {}, titel), hinweise.map((x) => h("div", { klasse: "meldung" }, h("b", {}, `${x.bereich}: `), x.text)));
}
function preisAngabe(b, art, waehrung, neu) {
  const einheiten = art.preis_einheiten.map((e) => [e, `je ${EINHEIT_TEXT[e] || e}`]);
  return [zahlAngabe(b, "preis", `Preis (${waehrung})`, null, "leer: kein Vergleich"),
    angabe("Preis gilt", klappliste(b.preis_einheit, einheiten, `Einheit des Preises ${art.titel}`, (w) => { b.preis_einheit = w; neu(); }))];
}
function erzeugerZeile(e, p, d, staende, neu) {
  const art = d.katalog.arten.find((a) => a.schluessel === e.art);
  if (!art.kostenlos) e.preis_einheit ??= art.preis_einheiten[0];
  e.zaehler_einheit ??= art.zaehler_einheiten[0];
  const wechseln = (w) => {
    const a = d.katalog.arten.find((x) => x.schluessel === w);
    Object.assign(e, { art: w, preis: null, preis_einheit: a.preis_einheiten[0] ?? null, nutzungsgrad: null, heizwert: null, zustandszahl: null, kg_je_srm: null, zaehler_einheit: a.zaehler_einheiten[0] });
    neu();
  };
  const menge = !art.kostenlos && [e.preis_einheit, e.zaehler_einheit].some((x) => !NUR_ENERGIE.includes(x));
  const gas = art.zustandszahl !== null;
  const stand = staende.get(e);
  const aktuell = stand && stand.zaehler === e.zaehler ? stand : null;
  return h("div", { klasse: "erzeuger" },
    h("div", { klasse: "zeile" }, h("div", { stil: "flex:1;min-width:180px" }, klappliste(e.art, d.katalog.arten.map((a) => [a.schluessel, a.titel]), "Art des Wärmeerzeugers", wechseln)),
      h("button", { klasse: "textknopf", onclick: () => { p.erzeuger.splice(p.erzeuger.indexOf(e), 1); neu(); } }, "Entfernen")),
    h("div", { klasse: "angaben" },
      zahlAngabe(e, "anteil", "Anteil an der ersetzten Wärme", "%", p.erzeuger.length === 1 ? "100" : null),
      art.kostenlos ? h("p", { klasse: "leise" }, "Zählt als kostenlos.") : preisAngabe(e, art, p.waehrung, neu),
      art.nutzungsgrad !== null && zahlAngabe(e, "nutzungsgrad", art.jaz ? "Jahresarbeitszahl" : "Nutzungsgrad (0,90 = 90 %)", null, art.nutzungsgrad),
      menge && art.heizwert !== null && zahlAngabe(e, "heizwert", gas ? "Brennwert" : "Heizwert", `kWh/${art.heizwert_je}`, art.heizwert),
      menge && gas && zahlAngabe(e, "zustandszahl", "Zustandszahl", null, art.zustandszahl),
      e.preis_einheit === "srm" && zahlAngabe(e, "kg_je_srm", "Gewicht", "kg/srm", art.kg_je_srm)),
    h("div", { klasse: "angaben" },
      h("div", { klasse: "angabe" }, h("span", { klasse: "leise" }, "Zähler (optional)"), auswahlFeld({ schluessel: "zaehler", domains: d.katalog.zaehler_domains, titel: `Zähler ${art.titel}` }, e)),
      angabe("Zähler misst in", klappliste(e.zaehler_einheit, art.zaehler_einheiten.map((x) => [x, x]), `Einheit des Zählers ${art.titel}`, (w) => { e.zaehler_einheit = w; neu(); }))),
    aktuell && aktuell.wert !== null && h("div", { klasse: "aktuell" }, `Aktuell: ${aktuell.wert}${aktuell.einheit ? " " + aktuell.einheit : ""}${aktuell.kwh !== null ? ` = ${zahl(aktuell.kwh, 0)} kWh` : ""}`),
    aktuell && aktuell.warnung && h("div", { klasse: "warnung" }, aktuell.warnung));
}
function holzKarte(p, d, neu) {
  const art = d.katalog.holz;
  p.holz.preis_einheit ??= art.preis_einheiten[0];
  const einheit = p.holz.preis_einheit;
  return h("div", { klasse: "karte" }, h("h2", {}, "Holz im Ofen"), h("p", { klasse: "leise" }, "Ohne Preis bleibt der Vergleich leer."),
    h("div", { klasse: "angaben" }, preisAngabe(p.holz, art, p.waehrung, neu),
      einheit !== "kWh" && zahlAngabe(p.holz, "heizwert", "Heizwert", "kWh/kg", art.heizwert),
      einheit === "rm" && zahlAngabe(p.holz, "kg_je_rm", "Gewicht", "kg/rm", art.kg_je_rm),
      einheit === "srm" && zahlAngabe(p.holz, "kg_je_srm", "Gewicht", "kg/srm", art.kg_je_srm),
      zahlAngabe(p.holz, "nutzungsgrad_ofen", "Nutzungsgrad des Ofens (0,80 = 80 %)", null, art.nutzungsgrad),
      zahlAngabe(p.holz, "kg_je_auflage", "Holz je Auflage", "kg", "leer: nicht bekannt")));
}
function ofenUmschalter(p, neu) {
  const knopf = ([wert, text]) => h("button", { type: "button", "aria-pressed": String(p.ofen === wert), onclick: () => { if (p.ofen !== wert) { p.ofen = wert; neu(); } } }, text);
  return h("div", { klasse: "umschalter", role: "group", "aria-label": "Betriebsart des Ofens" },
    [["luftgefuehrt", "Normal (luftgeführt)"], ["wassergefuehrt", "Wassergeführt"]].map(knopf));
}
async function anlageZeichnen() {
  const d = await api("api/profil"), p = d.profil;
  const staende = new Map(p.erzeuger.map((e, i) => [e, { zaehler: e.zaehler, ...d.zaehler[i] }]));
  const neu = () => {
    const wasser = p.ofen === "wassergefuehrt";
    zeige(h("div", { klasse: "kopf" }, h("h1", {}, "Anlage"), h("p", { klasse: "leise" }, "Beschreibe deinen Ofen und deine übrige Heizung. Die App zeigt danach nur, was dazu passt.")),
      h("div", { klasse: "karte" }, h("h2", {}, "Betriebsart"), ofenUmschalter(p, neu),
        h("p", { klasse: "leise", stil: "margin-top:8px" }, wasser ? "Die App liest zusätzlich Wärmetauscher, Puffer und Pumpe." : "Wassergrenzen und Puffer entfallen. Bereits gespeicherte Werte bleiben erhalten.")),
      !d.verbunden && h("div", { klasse: "karte" }, "Keine Verbindung zu Home Assistant, die aktuellen Werte fehlen."),
      hinweisKarte(d.hinweise, "Das fehlt noch"),
      h("div", { klasse: "karte" }, h("h2", {}, "Ofen"),
        h("div", { klasse: "angaben", stil: "margin:0 0 12px" },
          angabe("Währungszeichen", h("input", { type: "text", maxLength: 5, value: p.waehrung, "aria-label": "Währungszeichen", oninput: (e) => { p.waehrung = e.target.value; }, onchange: neu }))),
        wasser && jaNein(p, "puffer", "Es gibt einen Pufferspeicher", "Dann lassen sich die Speicherfühler zuweisen.", neu),
        wasser && jaNein(p, "pumpe_extern", "Eine eigene Regelung schaltet die Pumpe", "Nur zur Info. Die App liest nur mit, ob die Pumpe läuft.", neu),
        wasser && h("div", { klasse: "angaben", stil: "margin:0 0 12px" }, zahlAngabe(p, "pumpe_laeuft_ab", "Pumpe läuft ab (Stellsignal)", "%", "5")),
        jaNein(p, "klappe_motorisiert", "Die Luftklappe hat einen Motor", "Ohne Motor entfallen Klappenstellung, Wartung und Handbetrieb.", neu),
        p.klappe_motorisiert && jaNein(p, "klappe_invertiert", "Klappe invertiert", "Der Antrieb schließt bei hohem Signal. Falsch eingestellt schließt die Klappe, wenn sie öffnen soll. Gibt es den Helfer der Automation, gilt dessen Wert.", neu),
        p.klappe_motorisiert && h("div", { klasse: "angaben", stil: "margin:0 0 12px" }, zahlAngabe(p, "klappe_y0", "Startspannung bei einer Ausgabe als Licht (z. B. 2 bei 2-10-V-Antrieb)", "V", "0")),
        p.klappe_motorisiert && rueckmeldungAngaben(p, d.rueckmeldung),
        jaNein(p, "tuerkontakt", "Es gibt einen Türkontakt", "Ohne Türkontakt erkennt die App das Nachlegen nur am Temperaturanstieg. Gilt nur für die App.", neu)),
      h("div", { klasse: "karte" }, h("h2", {}, "Wärmeerzeuger"), h("p", { klasse: "leise" }, "Womit würdest du ohne den Ofen heizen? Beliebig viele, die Anteile müssen zusammen 100 % ergeben."),
        p.erzeuger.map((e) => erzeugerZeile(e, p, d, staende, neu)),
        h("p", { stil: "margin-top:12px" }, h("button", { klasse: "neben", onclick: () => { p.erzeuger.push({ art: "erdgas", anteil: null, preis: null }); neu(); } }, "Wärmeerzeuger hinzufügen"))),
      holzKarte(p, d, neu),
      imBetrieb() && h("p", { klasse: "warnung" }, "Im Betrieb nicht änderbar. Bitte erst auf Lauschen schalten."),
      h("div", { klasse: "leiste" }, h("button", { klasse: "haupt", disabled: imBetrieb(), onclick: async () => {
        try { await api("api/profil", p); toast("Gespeichert. Das Anlagenprofil gilt sofort."); anlageZeichnen(); } catch (f) { melden(f); }
      } }, "Speichern")));
  };
  neu();
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
      r.entitaet && r.wert !== null && h("div", { klasse: "aktuell" }, `${r.name || r.entitaet}: ${r.wert}${r.wert_einheit ? " " + r.wert_einheit : ""}${r.umgerechnet !== undefined && r.umgerechnet !== null ? ` → ${r.umgerechnet} %` : ""}${r.alter_s !== null ? ` · vor ${dauer(r.alter_s)}` : ""}`),
      r.warnung && h("div", { klasse: "warnung" }, r.warnung)));
}
async function eingaengeZeichnen() {
  const d = await api("api/rollen"), stand = {};
  d.rollen.forEach((r) => { if (r.entitaet) stand[r.schluessel] = r.entitaet; });
  const nach = (f) => d.rollen.filter((r) => r.sichtbar && f(r)).map((r) => rolleZeile(r, stand));
  const ueber = d.rollen.filter((r) => !r.sichtbar && r.entitaet);
  const offen = d.hinweise.filter((x) => x.bereich !== "Kosten");
  zeige(h("div", { klasse: "kopf" }, h("h1", {}, "Ein-/Ausgänge"), h("p", { klasse: "leise" }, "Hier legst du fest, welche Sensoren und Schalter aus Home Assistant die App verwendet. Welche das sind, richtet sich nach der ", h("button", { klasse: "textknopf", onclick: () => gehe("anlage") }, "Beschreibung deiner Anlage"), ".")),
    !d.verbunden && h("div", { klasse: "karte" }, "Keine Verbindung zu Home Assistant, die aktuellen Werte fehlen."),
    hinweisKarte(offen, "Das fehlt noch"),
    h("div", { klasse: "karte" }, h("h2", {}, "Eingänge"), nach((r) => r.art === "eingang" && r.stufe !== "erweitert")),
    h("div", { klasse: "karte" }, h("h2", {}, "Ausgänge"), h("p", { klasse: "leise" }, imBetrieb() ? "Im Betrieb schaltet die App diese Ausgänge." : "Werden geprüft und angezeigt. Geschaltet werden sie erst im Betrieb."), nach((r) => r.art === "ausgang")),
    h("details", { klasse: "karte" }, h("summary", {}, "Erweitert"), nach((r) => r.stufe === "erweitert")),
    ueber.length > 0 && h("details", { klasse: "karte" }, h("summary", {}, "Zugewiesen, passt aber nicht zur Anlage"), h("p", { klasse: "leise" }, "Diese Zuweisungen bleiben gespeichert. Zum Entfernen das Feld leeren und speichern."), ueber.map((r) => rolleZeile(r, stand))),
    imBetrieb() && h("p", { klasse: "warnung" }, "Im Betrieb nicht änderbar. Bitte erst auf Lauschen schalten."),
    h("div", { klasse: "leiste" }, h("button", { klasse: "haupt", disabled: imBetrieb(), onclick: async () => {
      try { await api("api/rollen", { rollen: stand, tuerkontakt_vorhanden: d.tuerkontakt_vorhanden }); toast("Gespeichert. Die Zuweisung gilt sofort."); eingaengeZeichnen(); } catch (f) { melden(f); }
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
      f.eigen && f.live !== null && h("span", { klasse: "leise" }, `Automation: ${zahl(f.live, 2)} ${f.einheit}`),
      f.eigen && h("button", { klasse: "textknopf", onclick: async () => { await setzen(f.name, null); toast("Zurückgesetzt"); neu(); } }, "Zurücksetzen")));
}
async function einstellungenZeichnen() {
  const d = await api("api/parameter");
  zeige(h("div", { klasse: "kopf" }, h("h1", {}, "Einstellungen"), h("p", { klasse: "leise" }, imBetrieb() ? "Änderungen wirken sofort auf die Klappe." : "Änderungen wirken im Lauschen nur auf die Berechnung der App, nicht auf den Ofen.")),
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
    f.eigen && h("button", { klasse: "textknopf", onclick: async () => { await setzen(f.name, null); reglerZeichnen(); } }, `${imBetrieb() ? "Zurück auf Vorgabe" : "Zurück auf Automation"} (${zahl(f.live, 2)})`));
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
  zeige(h("div", { klasse: "kopf" }, h("h1", {}, "Regler"), h("p", { klasse: "leise" }, imBetrieb() ? "Diese Werte wirken im Betrieb direkt auf die Klappe." : "Im Lauschen wirken diese Werte nur auf die gerechnete Klappe. Wie der Ofen wirklich reagiert, zeigt sich erst im Betrieb.")),
    h("div", { klasse: "karte" }, d.regler.map(schieber)),
    h("div", { klasse: "karte" }, h("h2", {}, "Auto-Tune aus dem Verlauf"), h("p", { klasse: "leise" }, "Wertet aus, wie die Temperatur in den letzten sieben Tagen auf Klappenbewegungen reagiert hat. Das Ergebnis ist ein Startwert, den du bestätigst."), h("p", { stil: "margin-top:10px" }, knopf), ergebnis),
    h("div", { klasse: "karte" }, h("h2", {}, "Auto-Tune mit Testlauf"), h("p", { klasse: "leise" }, "Aktive Streckenidentifikation nach dem Relaisverfahren: Die Zuluftklappe wird im Abbrand gezielt um ihre Arbeitsstellung verstellt. Aus der Reaktion der Abgastemperatur ermittelt die App Verstärkung und Totzeit der Regelstrecke und leitet daraus Reglerparameter ab."), h("p", { stil: "margin-top:10px" }, h("button", { klasse: "neben", disabled: true }, "Testlauf starten")), h("p", { klasse: "warnung" }, d.autotune_aktiv_gesperrt)));
}

// ---- Benachrichtigungen ------------------------------------------------
async function benachrichtigungenZeichnen() {
  const d = await api("api/benachrichtigungen");
  const wahl = new Map(d.arten.map((a) => [a.schluessel, new Set(d.zuordnung[a.schluessel] || [])]));
  const test = async (g, knopf) => {
    knopf.disabled = true;
    try { await api("api/benachrichtigungen/test", { dienst: g.dienst }); toast(`Testnachricht an ${g.name} gesendet`); } catch (f) { melden(f); }
    knopf.disabled = false;
  };
  const kopf = (g) => { const knopf = h("button", { klasse: "neben klein", type: "button", onclick: () => test(g, knopf) }, "Testnachricht");
    return h("th", { scope: "col" }, h("div", { klasse: "geraet" }, g.name), h("div", { klasse: "leise" }, g.fehlt ? "nicht gefunden" : g.dienst.replace("notify.", "")), knopf); };
  const zeile = (a) => h("tr", {}, h("th", { scope: "row" }, a.titel, a.dringend && h("span", { klasse: "chip", title: "Wird auch im Fokus-Modus zugestellt" }, "dringend"), h("div", { klasse: "leise" }, a.hilfe)),
    d.geraete.map((g) => h("td", {}, h("input", { type: "checkbox", checked: wahl.get(a.schluessel).has(g.dienst), "aria-label": `${a.titel} an ${g.name}`,
      onchange: (e) => { const m = wahl.get(a.schluessel); if (e.target.checked) m.add(g.dienst); else m.delete(g.dienst); } }))));
  zeige(h("div", { klasse: "kopf" }, h("h1", {}, "Benachrichtigungen"), h("p", { klasse: "leise" }, "Lege fest, welche Nachricht auf welches Gerät geht. Ohne Haken sendet die App nichts, so gibt es keine doppelten Meldungen neben deinen eigenen Automationen.")),
    !d.verbunden && h("div", { klasse: "karte" }, h("b", {}, "Keine Verbindung zu Home Assistant."), h("p", { klasse: "leise" }, "Die Geräteliste fehlt gerade. Deine gespeicherte Auswahl bleibt erhalten und gilt weiter.")),
    d.verbunden && d.geraete.length === 0 && h("div", { klasse: "karte" }, "Home Assistant bietet keine Geräte für Nachrichten an. Ist die Companion-App auf dem Handy eingerichtet?"),
    d.geraete.length > 0 && h("div", { klasse: "karte" }, h("div", { klasse: "matrix" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Nachricht"), d.geraete.map(kopf))), h("tbody", {}, d.arten.map(zeile))))),
    h("div", { klasse: "leiste" }, h("button", { klasse: "haupt", onclick: async () => {
      try { await api("api/benachrichtigungen", { zuordnung: Object.fromEntries([...wahl].map(([k, m]) => [k, [...m]])) }); toast("Gespeichert."); benachrichtigungenZeichnen(); } catch (f) { melden(f); }
    } }, "Speichern")));
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

// ---- Einmessen: reine Hilfsfunktionen (ohne DOM) ----
// Zyklus 1 beginnt mit dem ersten FEUER, nach jedem NACHLEGEN zählt er eins weiter. Vor dem Feuer gilt Zyklus 1.
function zyklusAus(ereignisse, zeit) {
  const art = (e) => String(e.art || "").toUpperCase();
  let feuer = null;
  for (const e of ereignisse || []) if (art(e) === "FEUER" && (feuer === null || e.zeit < feuer)) feuer = e.zeit;
  if (feuer === null) return 1;
  let zyklus = 1;
  for (const e of ereignisse) if (art(e) === "NACHLEGEN" && e.zeit > feuer && e.zeit <= zeit) zyklus++;
  return zyklus;
}
const nachZeit = (a, b) => a.zeit - b.zeit;
// Je Art und Zyklus gibt es höchstens einen Punkt: Setzen ersetzt ihn, Verschieben in einen anderen Zyklus verdrängt den dortigen.
function punktSetzen(punkte, art, zeit, zyklus) {
  const punkt = { art, zeit: Math.round(zeit), zyklus, auto: false };
  return { punkte: [...punkte.filter((p) => !(p.art === art && p.zyklus === zyklus)), punkt].sort(nachZeit), punkt };
}
function punktVerschieben(punkte, alt, zeit, zyklus) {
  const punkt = { ...alt, zeit: Math.round(zeit), zyklus, auto: false };
  return { punkte: [...punkte.filter((p) => p !== alt && !(p.art === alt.art && p.zyklus === zyklus)), punkt].sort(nachZeit), punkt };
}
const punktEntfernen = (punkte, punkt) => punkte.filter((p) => p !== punkt);
const punkteFuerServer = (punkte) => punkte.map(({ art, zeit, zyklus }) => ({ art, zeit, zyklus }));
function naechsterIndex(zeiten, z) {
  if (!zeiten || zeiten.length === 0) return -1;
  let a = 0, b = zeiten.length - 1;
  while (b - a > 1) { const m = (a + b) >> 1; if (zeiten[m] <= z) a = m; else b = m; }
  return Math.abs(zeiten[a] - z) <= Math.abs(zeiten[b] - z) ? a : b;
}
function ereignisKurz(e) {
  const t = String(e.text || e.art || "").trim();
  return t.length > 18 ? t.slice(0, 17) + "…" : t;
}
// Beschriftungen [{von, bis}] (x-Bereich) auf Zeilen verteilen, damit sie sich nicht überdecken. Liefert die Zeile je Eintrag.
function zeilenVerteilen(bereiche, maxZeilen = 6) {
  const enden = [], zeilen = new Array(bereiche.length);
  bereiche.map((b, i) => [b, i]).sort((a, b) => a[0].von - b[0].von).forEach(([b, i]) => {
    let z = enden.findIndex((e) => e + 4 <= b.von);
    if (z < 0) z = enden.length < maxZeilen ? enden.length : enden.indexOf(Math.min(...enden));
    enden[z] = b.bis; zeilen[i] = z;
  });
  return zeilen;
}
// Zeit auf ganze Sekunden innerhalb [von, bis] der Aufzeichnung; die Rundung bleibt in den Grenzen.
function zeitKlemmen(z, von, bis) {
  const unten = Math.ceil(von), oben = Math.max(Math.floor(bis), unten);
  return Math.min(Math.max(Math.round(z), unten), oben);
}
const wertText = (w) => w === null || w === undefined ? "–" : Number(w).toLocaleString("de-DE", { maximumFractionDigits: 2 });
// ---- Einmessen: Seite ----
const PUNKT_FARBE = { anheizen_max: "--nachlegen", abbrand_stabil: "--wartung", bester_abbrand: "--abbrand", glut: "--glut", abkuehlen: "--abkuehlen", zu_heiss: "--fehler" };
const punktFarbe = (art) => `var(${PUNKT_FARBE[art] || "--akzent"})`;
const EINMESS_REIHEN = ["t", "klappe", "twt", "pumpe"];
let einmessenOffen = null;        // id der geöffneten Aufzeichnung, null = Übersicht
let einmessenGeaendert = false;   // ungespeicherte Punkte in der Detailansicht
let einmessenStand = 0;           // wann die Übersicht zuletzt gezeichnet wurde
let einmessenAngezeigt = null;    // { laeuft, modus } der gezeichneten Übersicht
const einmessenEntwurf = { zyklen: 3, push: null, notiz: "" };
const uhrzeit = (z) => new Date(z * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });

const wartetAufFeuer = (e) => !e.nachlauf && e.zyklus === 0;
function einmessenSatz(e) {
  if (wartetAufFeuer(e)) return "Zünde jetzt an. Die Klappe steht auf 100 %.";
  if (e.nachlauf) return "Die Automatik regelt wieder. Die Aufzeichnung läuft bis der Ofen kalt ist.";
  if (e.handregelung) return "Regle jetzt von Hand an den Ofenklappen.";
  return "Zünde jetzt an. Die Klappe steht auf 100 %.";
}
function einmessenStartKarte(s) {
  const e = s.einmessen;
  if (!e) return null;
  return h("div", { klasse: "karte auffaellig", stil: "--farbe:var(--nachlegen)" },
    h("div", { klasse: "status" }, h("div", { klasse: "punkt blinkt" }),
      h("div", { stil: "min-width:0" }, h("h2", {}, e.nachlauf ? "Einmessen läuft – Nachlauf" : wartetAufFeuer(e) ? "Einmessen läuft – wartet auf Feuer" : `Einmessen läuft – Zyklus ${e.zyklus}/${e.n}`),
        h("div", {}, einmessenSatz(e)),
        h("div", { klasse: "leise" }, `${e.nachgelegt} von ${e.n} nachgelegt · seit ${dauer(e.seit_s)}`))),
    h("p", { stil: "margin-top:10px" }, h("button", { klasse: "haupt", onclick: () => gehe("einmessen") }, "Zum Einmessen")));
}
async function einmessenAbbrechen(l) {
  if (!confirm(l.nachlauf ? "Aufzeichnung jetzt beenden?" : "Aufzeichnung abbrechen? Die Automatik übernimmt dann wieder die Klappe.")) return;
  try { const d = await api("api/einmessen/abbrechen", {}); toast("Aufzeichnung abgebrochen."); einmessenZeichnen(d); } catch (f) { melden(f); }
}
function einmessenLaufKarte(l) {
  const zeile = l.nachlauf ? `Nachlauf · ${l.nachgelegt} von ${l.n} nachgelegt · seit ${dauer(l.seit_s)}`
    : wartetAufFeuer(l) ? `Wartet auf Feuer · ${l.n} Nachlegezyklen · seit ${dauer(l.seit_s)}`
    : `Zyklus ${l.zyklus} von ${l.n} · nachgelegt ${l.nachgelegt} · seit ${dauer(l.seit_s)}`;
  return h("div", { klasse: "karte auffaellig", stil: "--farbe:var(--nachlegen)" },
    h("div", { klasse: "status" }, h("div", { klasse: "punkt blinkt" }),
      h("div", { stil: "min-width:0" }, h("h2", {}, "Aufzeichnung läuft"),
        h("div", { klasse: "satz" }, einmessenSatz(l)),
        h("div", { klasse: "leise", stil: "margin-top:4px" }, zeile),
        l.notiz && h("div", { klasse: "leise notiz" }, `Notiz: ${l.notiz}`),
        h("div", { klasse: "leise" }, l.push ? "Push-Nachrichten sind an." : "Ohne Push-Nachrichten."))),
    !l.nachlauf && h("p", { klasse: "warnung", stil: "margin-top:10px" }, "Übertemperatur meldet die App nur, sie drosselt beim Einmessen nicht."),
    h("p", { stil: "margin-top:12px" }, h("button", { klasse: "neben", onclick: () => einmessenAbbrechen(l) }, "Abbrechen")));
}
function einmessenNeuKarte(d) {
  const e = einmessenEntwurf, max = d.max_zyklen || 5;
  e.zyklen = Math.min(Math.max(e.zyklen, 1), max);
  if (e.push === null) e.push = !!d.push_geraet;
  const zahlen = Array.from({ length: max }, (_, i) => i + 1);
  const gruppe = h("div", { klasse: "modus", role: "group", "aria-label": "Anzahl Nachlegezyklen" });
  const markieren = () => gruppe.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.n === e.zyklen)));
  gruppe.append(...zahlen.map((n) => h("button", { type: "button", "data-n": n, "aria-pressed": String(n === e.zyklen), onclick: () => { e.zyklen = n; markieren(); } }, String(n))));
  const knopf = h("button", { klasse: "haupt", disabled: !d.startbar, onclick: async () => {
    if (!confirm(`Einmessen mit ${e.zyklen} Nachlegezyklen starten?\n\nDie Klappe geht auf 100 % und bleibt dort bis zum letzten Nachlegen.`)) return;
    knopf.disabled = true;
    try {
      const neu = await api("api/einmessen/start", { zyklen: e.zyklen, push: !!e.push, notiz: e.notiz.trim() });
      e.notiz = ""; toast("Aufzeichnung läuft. Jetzt anzünden."); einmessenZeichnen(neu);
    } catch (f) { melden(f); knopf.disabled = !d.startbar; }
  } }, "Aufzeichnung starten");
  return h("div", { klasse: "karte" }, h("h2", {}, "Neue Aufzeichnung"),
    h("ol", { klasse: "schritte" },
      h("li", {}, "Starte die Aufzeichnung und zünde dann an. Die Klappe steht auf 100 %."),
      h("li", {}, "Regle von Hand an den Ofenklappen, bis du zum letzten Mal nachgelegt hast."),
      h("li", {}, "Danach übernimmt die Automatik. Die Aufzeichnung läuft still weiter, bis der Ofen kalt ist.")),
    h("p", { klasse: "warnung" }, "Übertemperatur meldet die App beim Einmessen nur, sie drosselt nicht. Behalte den Ofen im Blick."),
    h("div", { stil: "margin-top:14px" }, h("div", { klasse: "etikett" }, "Nachlegezyklen"), gruppe),
    h("div", { stil: "margin-top:14px" }, jaNein(e, "push", "Push-Nachrichten beim Einmessen", "Meldet Start, Feuer, Nachlegen und Ende. Übertemperatur kommt über die eigene Nachricht „Übertemperatur“.", () => {})),
    !d.push_geraet && h("p", { klasse: "leise", stil: "margin-top:6px" }, "Noch kein Gerät für Nachrichten gewählt. ",
      h("button", { klasse: "textknopf", onclick: () => gehe("benachrichtigungen") }, "Zu den Benachrichtigungen")),
    h("div", { klasse: "angaben", stil: "margin-top:14px" },
      angabe("Notiz", h("input", { type: "text", maxLength: 80, value: e.notiz, placeholder: "z. B. Buche, 2 kg", "aria-label": "Notiz zur Aufzeichnung", oninput: (x) => { e.notiz = x.target.value; } }))),
    h("p", { stil: "margin-top:14px" }, knopf),
    !d.startbar && d.startbar_grund && h("p", { klasse: "warnung" }, d.startbar_grund));
}
async function einmessenListeAktion(pfad, text) {
  try { const d = await api(pfad, {}); toast(text); einmessenZeichnen(d); } catch (f) { melden(f); }
}
function einmessenListeKarte(d) {
  const laufId = d.laeuft ? d.laeuft.id : null;
  const zeile = (k) => {
    const laeuft = k.id === laufId;
    const pfad = `api/einmessen/${encodeURIComponent(k.id)}`;
    return h("div", { klasse: "einmess-zeile" },
      h("div", { klasse: "titelzeile" }, h("b", {}, uhr(k.start)),
        laeuft && h("span", { klasse: "chip eigen" }, "läuft"),
        k.massgeblich && h("span", { klasse: "chip massgeblich" }, "maßgeblich"),
        k.punkte_bearbeitet && h("span", { klasse: "chip" }, "Punkte bearbeitet")),
      k.notiz && h("div", { klasse: "notiz" }, k.notiz),
      h("div", { klasse: "leise" }, [`${k.nachgelegt} von ${k.n} nachgelegt`, k.ende_text].filter(Boolean).join(" · ")),
      h("div", { klasse: "aktionen" },
        h("button", { klasse: "neben klein", onclick: () => einmessenOeffnen(k.id) }, "Öffnen"),
        !laeuft && h("button", { klasse: "neben klein wahl", "aria-pressed": String(!!k.massgeblich), disabled: !!k.massgeblich,
          title: k.massgeblich ? "Diese Aufzeichnung ist maßgeblich" : "Diese Aufzeichnung maßgeblich machen",
          onclick: () => einmessenListeAktion(`${pfad}/massgeblich`, "Diese Aufzeichnung ist jetzt maßgeblich.") }, "Maßgeblich"),
        !laeuft && h("button", { klasse: "textknopf", onclick: () => {
          if (confirm(`Aufzeichnung vom ${uhr(k.start)} löschen? Das kann nicht rückgängig gemacht werden.`)) einmessenListeAktion(`${pfad}/loeschen`, "Aufzeichnung gelöscht.");
        } }, "Löschen")));
  };
  return h("div", { klasse: "karte" }, h("h2", {}, "Aufzeichnungen"),
    d.liste.length === 0 ? h("p", { klasse: "leise" }, "Noch keine Aufzeichnung. Die erste startest du oben.")
      : [h("p", { klasse: "leise" }, "Die Vorschläge der maßgeblichen Aufzeichnung gelten als Grundlage. Genau eine ist maßgeblich."), ...d.liste.map(zeile)]);
}
async function einmessenZeichnen(daten) {
  const d = daten || await api("api/einmessen");
  if (seite !== "einmessen" || einmessenOffen !== null) return;
  einmessenStand = Date.now(); einmessenAngezeigt = { laeuft: !!d.laeuft, modus };
  zeige(h("div", { klasse: "kopf" }, h("h1", {}, "Einmessen"),
      h("p", { klasse: "leise" }, "Du regelst einen Abbrand von Hand, die App zeichnet auf. Aus den Punkten im Verlauf schlägt sie Parameter für deinen Ofen vor.")),
    d.laeuft ? einmessenLaufKarte(d.laeuft) : einmessenNeuKarte(d),
    einmessenListeKarte(d));
}
// Im Takt der Statusabfrage: die Übersicht alle ~10 s neu (auch für startbar), sofort bei Start, Ende oder Moduswechsel. Nie mitten in einer Eingabe.
function einmessenAuffrischen(s) {
  if (einmessenOffen !== null || !einmessenAngezeigt) return;
  const wechsel = einmessenAngezeigt.laeuft !== !!s.einmessen || einmessenAngezeigt.modus !== s.modus;
  if (!wechsel && Date.now() - einmessenStand < 9500) return;
  const f = document.activeElement;
  if (f && inhalt.contains(f) && (f.tagName === "INPUT" || f.tagName === "TEXTAREA")) return;
  einmessenZeichnen().catch(() => { /* nächster Versuch im nächsten Takt */ });
}
async function einmessenOeffnen(id) {
  einmessenOffen = id;
  try { const d = await api(`api/einmessen/${encodeURIComponent(id)}`); einmessenDetail(d); }
  catch (f) { einmessenOffen = null; melden(f); }
}
function einmessenDetail(d, behalten = {}) {
  const k = d.kopf, v = d.verlauf, arten = d.punktarten || [], ereignisse = d.ereignisse || [];
  if (seite !== "einmessen" || String(einmessenOffen) !== String(k.id)) return;
  const pfad = `api/einmessen/${encodeURIComponent(k.id)}`;
  const st = { punkte: behalten.punkte || (d.punkte || []).map((p) => ({ ...p })), geaendert: !!behalten.punkte, gewaehlt: null,
    art: arten.some((a) => a.art === behalten.art) ? behalten.art : (arten[0] ? arten[0].art : null), aus: behalten.aus || new Set(), evOffen: !!behalten.evOffen };
  const artName = (a) => (arten.find((x) => x.art === a) || {}).name || a;
  const feldTitel = (f) => ((d.vorschlaege || []).find((x) => x.feld === f) || {}).titel || f;
  const tAm = (z) => { const i = naechsterIndex(v.zeit, z); return i < 0 ? null : v.t[i]; };
  const punktText = (p) => { const t = tAm(p.zeit); return t === null || t === undefined ? artName(p.art) : `${artName(p.art)} ${zahl(t)} °C`; };
  const merken = () => ({ art: st.art, aus: st.aus, evOffen: st.evOffen, ...(st.geaendert ? { punkte: st.punkte } : {}) });
  const laden = async () => einmessenDetail(await api(pfad), merken());
  const zurueck = () => {
    if (st.geaendert && !confirm("Die geänderten Punkte sind noch nicht gespeichert. Trotzdem zurück?")) return;
    einmessenOffen = null; einmessenGeaendert = false; einmessenZeichnen().catch(melden);
  };
  const aendern = (ergebnis) => { st.punkte = ergebnis.punkte; st.gewaehlt = ergebnis.punkt; st.geaendert = true; neu(); };
  const klemmen = (z) => zeitKlemmen(z, k.start, k.ende || v.zeit[v.zeit.length - 1] || k.start);
  const setzen = (zeit) => { if (!st.art) return; const z = klemmen(zeit); aendern(punktSetzen(st.punkte, st.art, z, zyklusAus(ereignisse, z))); };
  const verschieben = (p, zeit) => { const z = klemmen(zeit); aendern(punktVerschieben(st.punkte, p, z, zyklusAus(ereignisse, z))); };
  const laufend = !k.ende;
  const entfernen = () => { st.punkte = punktEntfernen(st.punkte, st.gewaehlt); st.gewaehlt = null; st.geaendert = true; neu(); };
  const speichern = async () => {
    try { const n = await api(`${pfad}/punkte`, { punkte: punkteFuerServer(st.punkte) }); toast("Punkte gespeichert, die Vorschläge sind neu berechnet."); einmessenDetail(n, { art: st.art, aus: st.aus, evOffen: st.evOffen }); }
    catch (f) { melden(f); }
  };
  const zuruecksetzen = async () => {
    if (!confirm("Alle Punkte auf die Vorschläge der App zurücksetzen? Deine Änderungen an den Punkten gehen verloren.")) return;
    try { const n = await api(`${pfad}/punkte_zuruecksetzen`, {}); toast("Vorschläge der App wiederhergestellt."); einmessenDetail(n, { art: st.art, aus: st.aus, evOffen: st.evOffen }); }
    catch (f) { melden(f); }
  };
  const massgeblich = async () => {
    try { await api(`${pfad}/massgeblich`, {}); toast("Diese Aufzeichnung ist jetzt maßgeblich."); await laden(); } catch (f) { melden(f); }
  };
  // Punkte in der Grafik: Antippen wählt, Ziehen verschiebt, Tippen auf freie Fläche setzt die gewählte Art im Zyklus dort.
  const zusatz = (g) => {
    const sichtbar = st.punkte.map((p, i) => [p, i]).filter(([p]) => p.zeit >= g.z0 && p.zeit <= g.z1);
    const lage = sichtbar.map(([p]) => {
      const xp = g.x(p.zeit), breite = punktText(p).length * 6.2 + 5, rechts = xp > g.L + (g.B - g.L) * 0.7;
      return { xp, rechts, von: rechts ? xp - breite : xp, bis: rechts ? xp : xp + breite };
    });
    const zeilen = zeilenVerteilen(lage);
    sichtbar.forEach(([p, i], j) => {
      const { xp, rechts } = lage[j], c = punktFarbe(p.art), aktiv = p === st.gewaehlt;
      const ty = g.O + 11 + zeilen[j] * 13;
      const griff = g.s("g", { class: "punkt-griff", "data-i": i });
      griff.append(g.s("line", { x1: xp, x2: xp, y1: g.O, y2: g.O + g.hoch, stroke: "transparent", "stroke-width": 24, "pointer-events": "stroke" }));
      const strich = { x1: xp, x2: xp, y1: g.O, y2: g.O + g.hoch, style: `stroke:${c}`, "stroke-width": aktiv ? 3.2 : 2 };
      if (p.auto) strich["stroke-dasharray"] = "6 4";
      griff.append(g.s("line", strich));
      griff.append(g.s("circle", { cx: xp, cy: g.O + g.hoch - 9, r: aktiv ? 8 : 6, style: `fill:${c}` }));
      griff.append(g.s("text", { x: rechts ? xp - 5 : xp + 5, y: ty, "text-anchor": rechts ? "end" : "start", class: "punkt-text", style: `fill:${c}` }, punktText(p)));
      g.svg.append(griff);
    });
    let zug = null;
    g.svg.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      const griff = e.target.closest ? e.target.closest(".punkt-griff") : null;
      zug = { p: griff ? st.punkte[+griff.getAttribute("data-i")] : null, griff, sx: e.clientX, sy: e.clientY, zeit: null, bewegt: false };
      if (griff) { e.preventDefault(); try { g.svg.setPointerCapture(e.pointerId); } catch (_) { /* ohne Capture geht es auch */ } }
    });
    g.svg.addEventListener("pointermove", (e) => {
      if (!zug || !zug.p) return;
      if (!zug.bewegt && Math.abs(e.clientX - zug.sx) < 4) return;
      zug.bewegt = true; zug.zeit = g.zeitAus(e.clientX);
      zug.griff.setAttribute("transform", `translate(${(g.x(zug.zeit) - g.x(zug.p.zeit)).toFixed(1)} 0)`);
      const t = zug.griff.querySelector("text"); if (t) t.textContent = punktText({ ...zug.p, zeit: zug.zeit });
    });
    g.svg.addEventListener("pointerup", (e) => {
      const z = zug; zug = null;
      if (!z) return;
      if (z.p) { if (z.bewegt) verschieben(z.p, z.zeit); else { st.gewaehlt = z.p; st.art = z.p.art; neu(); } return; }
      if (Math.hypot(e.clientX - z.sx, e.clientY - z.sy) < 8) setzen(g.zeitAus(e.clientX));
    });
    g.svg.addEventListener("pointercancel", () => { if (zug && zug.griff) zug.griff.removeAttribute("transform"); zug = null; });
  };
  const vorschlagZeile = (x) => h("div", { klasse: "vorschlag-zeile" },
    h("div", {}, h("b", {}, x.titel),
      h("div", { klasse: "wechsel" }, `bisher ${wertText(x.alt)}${x.einheit ? " " + x.einheit : ""} → neu `, h("b", {}, `${wertText(x.wert)}${x.einheit ? " " + x.einheit : ""}`)),
      x.begruendung && h("div", { klasse: /achtung/i.test(x.begruendung) ? "warnung" : "leise" }, x.begruendung),
      x.gleich && h("div", { klasse: "leise" }, "Entspricht schon dem eingestellten Wert.")),
    h("button", { klasse: "neben", disabled: laufend || !!x.gleich || x.wert === null || x.wert === undefined, title: laufend ? "Erst nach dem Ende der Aufzeichnung" : null, onclick: async (e) => {
      const b = e.currentTarget; b.disabled = true;
      try { const r = await api(`${pfad}/uebernehmen`, { feld: x.feld }); toast(`${r.titel || x.titel} auf ${wertText(r.wert)}${x.einheit ? " " + x.einheit : ""} gesetzt`); await laden(); }
      catch (f) { melden(f); b.disabled = false; }
    } }, "Übernehmen"));
  const neu = () => {
    einmessenGeaendert = st.geaendert;
    const schmal = inhalt.clientWidth < 600;
    const reihen = REIHEN.filter((r) => EINMESS_REIHEN.includes(r.k) && !st.aus.has(r.k) && reihenWerte(v, r.k).length > 0);
    const bild = grafik(v, { reihen, tuer: true, zusatz, klasse: "einmess", breite: schmal ? 480 : 860, hoehe: 360,
      marker: ereignisse.map((e) => ({ zeit: e.zeit, text: ereignisKurz(e), titel: `${uhrzeit(e.zeit)} ${e.text || e.art}` })) });
    const pa = arten.find((a) => a.art === st.art);
    const anzahl = (a) => st.punkte.filter((p) => p.art === a).length;
    const tuerDa = (v.tuer || []).some((w) => w !== null && w !== undefined);
    const legende = h("div", { klasse: "legende", role: "group", "aria-label": "Linien ein- und ausschalten" },
      REIHEN.filter((r) => EINMESS_REIHEN.includes(r.k) && reihenWerte(v, r.k).length > 0).map((r) => h("button", { type: "button", stil: `--c:var(${r.farbe})`, "aria-pressed": String(!st.aus.has(r.k)),
        onclick: () => { if (st.aus.has(r.k)) st.aus.delete(r.k); else st.aus.add(r.k); neu(); } }, h("i", {}), r.k === "klappe" ? `Klappe (${r.einheit})` : reihenTitel(v, r))),
      h("span", {}, "Farbband: Phase"), tuerDa && h("span", {}, "schmales Band: Tür offen"), h("span", {}, "gestrichelt: Ereignis oder Punkt der App"));
    const g = st.gewaehlt;
    zeige(h("div", { klasse: "kopf" },
        h("p", {}, h("button", { klasse: "textknopf", onclick: zurueck }, "← Alle Aufzeichnungen")),
        h("h1", { stil: "margin-top:6px" }, `Aufzeichnung vom ${uhr(k.start)}`),
        h("p", { klasse: "leise" }, [k.notiz, `${k.nachgelegt} von ${k.n} nachgelegt`, k.ende_text].filter(Boolean).join(" · "), " ",
          k.massgeblich && h("span", { klasse: "chip massgeblich" }, "maßgeblich"))),
      !k.ende && h("div", { klasse: "karte auffaellig", stil: "--farbe:var(--nachlegen)" }, h("p", {}, "Die Aufzeichnung läuft noch. Neu laden zeigt den aktuellen Stand."),
        h("p", { stil: "margin-top:8px" }, h("button", { klasse: "neben", onclick: () => laden().catch(melden) }, "Neu laden"))),
      h("div", { klasse: "karte" }, h("h2", {}, "Punkte im Verlauf"),
        arten.length > 0 && h("div", { klasse: "punktwahl", role: "group", "aria-label": "Punktart wählen" },
          arten.map((a) => h("button", { type: "button", stil: `--c:${punktFarbe(a.art)}`, "aria-pressed": String(a.art === st.art),
            onclick: () => { st.art = a.art; if (st.gewaehlt && st.gewaehlt.art !== a.art) st.gewaehlt = null; neu(); } }, h("i", {}), a.name, h("span", { klasse: "leise" }, ` ${anzahl(a.art)}`)))),
        pa && h("p", { klasse: "leise", stil: "margin-top:8px" }, pa.hilfe, pa.felder && pa.felder.length ? ` Bestimmt: ${(pa.feld_titel && pa.feld_titel.length ? pa.feld_titel : pa.felder.map(feldTitel)).join(", ")}.` : ""),
        pa && h("p", { klasse: "leise", stil: "margin-top:4px" }, `Tippe in die Grafik, um „${pa.name}“ im Zyklus dort zu setzen. Einen Punkt ziehst du zur Seite, antippen wählt ihn.`),
        h("div", { stil: "margin-top:10px" }, bild), legende,
        g && h("div", { klasse: "punkt-wahl-info" },
          h("span", {}, h("b", {}, artName(g.art)), ` · Zyklus ${g.zyklus} · ${uhrzeit(g.zeit)} Uhr · ${zahl(tAm(g.zeit))} °C`, g.auto ? " · Vorschlag der App" : ""),
          h("button", { klasse: "neben klein", onclick: entfernen }, "Entfernen")),
        st.geaendert && h("p", { klasse: "warnung" }, "Noch nicht gespeichert. Die Vorschläge unten gelten erst nach dem Speichern."),
        h("div", { klasse: "knopfzeile" }, h("button", { klasse: "haupt", disabled: laufend || !st.geaendert, onclick: speichern }, "Punkte speichern"),
          h("button", { klasse: "neben", disabled: laufend, onclick: zuruecksetzen }, "Vorschläge der App wiederherstellen")),
        laufend && h("p", { klasse: "leise", stil: "margin-top:6px" }, "Speichern, Wiederherstellen und Übernehmen erst nach dem Ende der Aufzeichnung.")),
      h("div", { klasse: "karte" }, h("h2", {}, "Vorschläge für die Parameter"),
        !k.massgeblich && h("div", { klasse: "vorschlagbox" }, h("p", { klasse: "leise" }, "Diese Aufzeichnung ist nicht maßgeblich. Du kannst ihre Vorschläge trotzdem übernehmen."),
          h("button", { klasse: "neben klein", onclick: massgeblich }, "Maßgeblich machen")),
        laufend && h("p", { klasse: "warnung" }, "Übernehmen erst nach dem Ende der Aufzeichnung."),
        (d.vorschlaege || []).length > 0 ? d.vorschlaege.map(vorschlagZeile) : h("p", { klasse: "leise" }, "Noch keine Vorschläge. Setze Punkte und speichere sie.")),
      ereignisse.length > 0 && h("details", { klasse: "karte", open: st.evOffen, ontoggle: (e) => { st.evOffen = e.target.open; } }, h("summary", {}, `Ereignisse (${ereignisse.length})`),
        ereignisse.map((e) => h("div", { klasse: "meldung" }, h("time", {}, uhrzeit(e.zeit)), e.zyklus ? `Zyklus ${e.zyklus} · ` : "", e.text || e.art))));
  };
  neu();
}

// ---- Rahmen ------------------------------------------------------------
const SEITEN = { start: null, verlauf: verlaufZeichnen, kosten: kostenZeichnen, anlage: anlageZeichnen, eingaenge: eingaengeZeichnen, einstellungen: einstellungenZeichnen, regler: reglerZeichnen,
  einmessen: () => { einmessenOffen = null; return einmessenZeichnen(); }, benachrichtigungen: benachrichtigungenZeichnen, service: serviceZeichnen };
// Unterreiter von „Einstellungen“ in Anzeigereihenfolge; „einstellungen“ selbst ist der Reiter Parameter.
const UNTERREITER = [["einstellungen", "Parameter"], ["anlage", "Anlage"], ["eingaenge", "Ein-/Ausgänge"], ["regler", "Regler"], ["einmessen", "Einmessen"], ["benachrichtigungen", "Benachrichtigungen"]];
const imUnterreiter = (name) => UNTERREITER.some(([k]) => k === name);
let seite = "start";
async function aktualisieren() {
  try { const s = await api("api/status"); modusAnzeigen(s.modus); if (seite === "start") startZeichnen(s); else if (seite === "einmessen") einmessenAuffrischen(s); }
  catch (f) { if (seite === "start") zeige(h("div", { klasse: "karte" }, `Keine Daten: ${f.message}`)); }
}
async function gehe(name) {
  if (einmessenOffen !== null && einmessenGeaendert) {
    if (!confirm("Die geänderten Punkte sind noch nicht gespeichert. Trotzdem wechseln?")) return;
    einmessenGeaendert = false;
  }
  seite = SEITEN.hasOwnProperty(name) ? name : "start";
  location.hash = seite;
  const haupt = imUnterreiter(seite) ? "einstellungen" : seite;
  document.querySelectorAll(".navi button").forEach((b) => { if (b.dataset.seite === haupt) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  const unter = document.getElementById("unter");
  unter.hidden = !imUnterreiter(seite);
  unter.replaceChildren(...UNTERREITER.map(([k, t]) => h("button", { type: "button", "aria-current": k === seite ? "page" : false, onclick: () => gehe(k) }, t)));
  const aktiv = unter.querySelector('[aria-current]'); if (aktiv && aktiv.scrollIntoView) aktiv.scrollIntoView({ block: "nearest", inline: "center" });
  if (seite === "start") return aktualisieren();
  try { await SEITEN[seite](); } catch (f) { zeige(h("div", { klasse: "karte" }, f.message)); }
}
document.querySelectorAll(".navi button").forEach((b) => b.addEventListener("click", () => gehe(b.dataset.seite)));
aktualisieren().finally(() => gehe(location.hash.slice(1) || "start"));
// Im Hintergrund nicht abfragen; beim Zurückkehren sofort, damit eine abgelaufene Sitzung gleich erneuert wird.
setInterval(() => { if (!document.hidden) aktualisieren(); }, 5000);
const verlaufTakt = () => { if (!document.hidden && seite === "verlauf" && verlaufNachfuehren) verlaufNachfuehren(); };
setInterval(verlaufTakt, VERLAUF_TAKT_MS);
document.addEventListener("visibilitychange", () => { if (!document.hidden) { aktualisieren(); verlaufTakt(); } });
