/* Nutriplan Bridge custom Lovelace card.
 * Bundled with and auto-registered by the custom_components/nutriplan_bridge
 * integration (see __init__.py -> add_extra_js_url). No manual resource
 * registration needed.
 *
 * Zero-config by default: it looks for entities created by this integration
 * (unique object_id suffixes "plan_actual", "comidas_hoy", "proxima_cita",
 * "seguimientos", "dietista"). Has a visual (GUI) editor - click "Edit" on
 * the card - to override entities explicitly if you run more than one
 * account. Equivalent YAML:
 *
 * type: custom:nutriplan-bridge-card
 * plan_entity: sensor.nutriplan_bridge_plan_actual
 * meals_entity: sensor.nutriplan_bridge_comidas_hoy
 * appointment_entity: sensor.nutriplan_bridge_proxima_cita
 * tracking_entity: sensor.nutriplan_bridge_seguimientos
 * dietista_entity: sensor.nutriplan_bridge_dietista
 *
 * The "comidas_hoy" sensor's "comidas" attribute carries, per meal slot:
 * { franja, hora, platos: [{ subingesta_id, plato_id, super_plato_id, nombre,
 *   receta, comensales, duracion_min, rating, calorias, alergenos, imagen,
 *   ingredientes: [{nombre, cantidad, medida_casera}] }] }
 * (a slot can have more than one plato, e.g. desayuno = coffee + sandwich).
 * plan_id/dieta_index live at the top level of that same attribute set.
 *
 * Progressive disclosure, mobile-first: a meal expands into a short list of
 * dish rows (thumbnail + name + kcal); tapping a row expands ITS OWN detail
 * (recipe, ingredients, "Cambiar plato" and a 5-star rating control) inline
 * below it, so a 3-dish meal doesn't dump three full recipes on screen at
 * once. Stars call nutriplan_bridge.valorar_plato. "Cambiar plato" calls
 * opciones_plato (backend finds the right list in the response and shapes
 * every option with the same _plato_detail() used for comidas_hoy, so no
 * guessing happens client-side); each option is its own expandable row with
 * a full preview (recipe/ingredients/allergens) and an explicit "Aceptar"
 * button that calls cambiar_plato. cambiar_plato mutates the real, live
 * plan - see api.py's docstring for what is/isn't fully confirmed about it.
 */

(() => {
const MEAL_LABELS = {
  desayuno: "Desayuno",
  tentempie1: "Tentempié",
  tentempie2: "Tentempié",
  comida: "Comida",
  merienda1: "Merienda",
  merienda2: "Merienda",
  cena: "Cena",
  recena1: "Recena",
  recena2: "Recena",
};

const MEAL_ICONS = {
  desayuno: "mdi:coffee",
  tentempie1: "mdi:food-apple-outline",
  tentempie2: "mdi:food-apple-outline",
  comida: "mdi:food",
  merienda1: "mdi:cookie",
  merienda2: "mdi:cookie",
  cena: "mdi:silverware-fork-knife",
  recena1: "mdi:moon-waning-crescent",
  recena2: "mdi:moon-waning-crescent",
};

const WEEKDAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];
const WEEKDAYS_SHORT = ["L", "M", "X", "J", "V", "S", "D"];

const ENTITY_FIELDS = [
  { key: "plan_entity", suffix: "_plan_actual", label: "Plan actual" },
  { key: "meals_entity", suffix: "_comidas_hoy", label: "Comidas de hoy" },
  { key: "appointment_entity", suffix: "_proxima_cita", label: "Próxima cita" },
  { key: "tracking_entity", suffix: "_seguimientos", label: "Seguimientos" },
  { key: "dietista_entity", suffix: "_dietista", label: "Dietista" },
  { key: "chat_entity", suffix: "_mensajes_sin_leer", label: "Mensajes sin leer" },
  { key: "charlas_entity", suffix: "_charlas", label: "Charlas" },
  { key: "eni_entity", suffix: "_estado_eni", label: "Encuesta inicial (objetivos)" },
  { key: "eaten_entity", suffix: "_comidas_tomadas_hoy", label: "Comidas tomadas hoy" },
];

function formatShortDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function planLabel(plan) {
  if (!plan) return "Plan";
  return plan.titulo || formatShortDate(plan.inicio || plan.enviado || plan.creado) || `Plan ${plan.id}`;
}

// "Meals already eaten today" lives in this browser only (localStorage), keyed
// by date so it resets itself every day; older days are cleaned up on read.
const EATEN_PREFIX = "nutriplan_bridge_eaten_";
function todayKey() {
  const d = new Date();
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}
function loadEaten() {
  try {
    const key = EATEN_PREFIX + todayKey();
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k && k.startsWith(EATEN_PREFIX) && k !== key) localStorage.removeItem(k);
    }
    return new Set(JSON.parse(localStorage.getItem(key) || "[]"));
  } catch (e) {
    return new Set();
  }
}
function saveEaten(set) {
  try {
    localStorage.setItem(EATEN_PREFIX + todayKey(), JSON.stringify([...set]));
  } catch (e) {
    /* storage unavailable: the tick just won't persist */
  }
}
function loadChecked(key) {
  try {
    return new Set(JSON.parse(localStorage.getItem(key) || "[]"));
  } catch (e) {
    return new Set();
  }
}
function saveChecked(key, set) {
  try {
    localStorage.setItem(key, JSON.stringify([...set]));
  } catch (e) {
    /* ignore */
  }
}

function minutesOf(hora) {
  const m = /(\d{1,2}):(\d{2})/.exec(String(hora || ""));
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

function mealKcal(meal) {
  return (meal.platos || []).reduce((sum, p) => sum + (Number(p.calorias) || 0), 0);
}

function relativeDay(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const startOf = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(d) - startOf(new Date())) / 86400000);
  if (days === 0) return "hoy";
  if (days === 1) return "mañana";
  if (days === -1) return "ayer";
  return days > 0 ? `en ${days} días` : `hace ${-days} días`;
}

function macroBar(resumen) {
  const p = (Number(resumen.proteinas_g) || 0) * 4;
  const c = (Number(resumen.carbohidratos_g) || 0) * 4;
  const g = (Number(resumen.grasas_g) || 0) * 9;
  const total = p + c + g;
  if (!total) return "";
  const pct = (v) => ((v / total) * 100).toFixed(1);
  return `<div class="macro-bar" title="Reparto de calorías: proteínas ${Math.round((p / total) * 100)}% · hidratos ${Math.round((c / total) * 100)}% · grasas ${Math.round((g / total) * 100)}%"><span style="width:${pct(p)}%;background:#43a047"></span><span style="width:${pct(c)}%;background:#fb8c00"></span><span style="width:${pct(g)}%;background:#8e24aa"></span></div><div class="macro-legend"><span><i style="background:#43a047"></i>Proteínas ${Math.round((p / total) * 100)}%</span><span><i style="background:#fb8c00"></i>Hidratos ${Math.round((c / total) * 100)}%</span><span><i style="background:#8e24aa"></i>Grasas ${Math.round((g / total) * 100)}%</span></div>`;
}

function allergenName(a) {
  if (typeof a === "string") return a;
  if (a && typeof a === "object") return a.nombre || a.name || a.label || "";
  return String(a ?? "");
}

function allergenChips(list) {
  const names = (list || []).map(allergenName).filter(Boolean);
  if (!names.length) return "";
  return `<div class="meal-alergenos">${names
    .map((n) => `<span class="chip warn"><ha-icon icon="mdi:alert-circle-outline"></ha-icon>${escapeHtml(n)}</span>`)
    .join("")}</div>`;
}

function emptyState(icon, text) {
  return `<div class="empty-state"><ha-icon icon="${icon}"></ha-icon><span>${escapeHtml(text)}</span></div>`;
}

function pick(obj, keys) {
  if (!obj || typeof obj !== "object") return null;
  for (const k of keys) {
    if (typeof obj[k] === "string" && obj[k].trim()) return obj[k].trim();
  }
  return null;
}

function bigChart(hist) {
  const pts = hist.filter((h) => Number(h.peso) > 0).slice(-30);
  if (pts.length < 2) return "";
  const w = 300;
  const h = 130;
  const padL = 34;
  const padB = 20;
  const vals = pts.map((p) => Number(p.peso));
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const x = (i) => padL + (i / (pts.length - 1)) * (w - padL - 6);
  const y = (v) => 8 + (1 - (v - min) / span) * (h - padB - 16);
  const line = vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const dots = vals.map((v, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="2.5" fill="var(--primary-color)"/>`).join("");
  return `<svg class="bigchart" viewBox="0 0 ${w} ${h}">
    <line x1="${padL}" y1="${y(max)}" x2="${w - 6}" y2="${y(max)}" stroke="var(--divider-color, rgba(127,127,127,.3))" stroke-dasharray="3 3"/>
    <line x1="${padL}" y1="${y(min)}" x2="${w - 6}" y2="${y(min)}" stroke="var(--divider-color, rgba(127,127,127,.3))" stroke-dasharray="3 3"/>
    <text x="2" y="${y(max) + 4}" font-size="10" fill="var(--secondary-text-color)">${max}</text>
    <text x="2" y="${y(min) + 4}" font-size="10" fill="var(--secondary-text-color)">${min}</text>
    <polyline points="${line}" fill="none" stroke="var(--primary-color)" stroke-width="2" stroke-linejoin="round"/>${dots}
    <text x="${padL}" y="${h - 4}" font-size="10" fill="var(--secondary-text-color)">${escapeHtml(formatShortDate(pts[0].fecha))}</text>
    <text x="${w - 6}" y="${h - 4}" font-size="10" text-anchor="end" fill="var(--secondary-text-color)">${escapeHtml(formatShortDate(pts[pts.length - 1].fecha))}</text>
  </svg>`;
}

const NUTRIENT_ROWS = [
  ["energia_kcal", "Energía", "kcal"],
  ["proteinas_g", "Proteínas", "g"],
  ["grasas_g", "Grasas", "g"],
  ["grasas_saturadas_g", "Saturadas", "g"],
  ["carbohidratos_g", "Hidratos", "g"],
  ["azucares_g", "Azúcares", "g"],
  ["fibra_g", "Fibra", "g"],
  ["sodio_mg", "Sodio", "mg"],
];

function nutrientGrid(nutr) {
  if (!nutr) return "";
  const cells = NUTRIENT_ROWS.filter(([k]) => typeof nutr[k] === "number" && nutr[k] > 0).map(
    ([k, label, unit]) =>
      `<div class="nutri"><span class="nutri-v">${Math.round(nutr[k] * 10) / 10}<small> ${unit}</small></span><span class="nutri-l">${label}</span></div>`
  );
  return cells.length ? `<div class="nutri-grid">${cells.join("")}</div>` : "";
}

function sparkline(values) {
  if (values.length < 2) return "";
  const w = 120;
  const h = 28;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values
    .map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - 3 - ((v - min) / span) * (h - 6)).toFixed(1)}`)
    .join(" ");
  const last = pts.split(" ").pop().split(",");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="var(--primary-color)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${last[0]}" cy="${last[1]}" r="2.5" fill="var(--primary-color)"/></svg>`;
}

// Entity ids come from the entity NAMES ("Comidas de hoy" -> ..._comidas_de_hoy), but the
// card was written against the older ids (..._comidas_hoy, ..._estado_eni) that existing
// installs still have (Home Assistant keeps an entity's id after a rename). A fresh install
// gets the new spelling, so look for both - otherwise the card finds nothing on a new setup.
const SUFFIX_ALIASES = {
  _comidas_hoy: ["_comidas_hoy", "_comidas_de_hoy"],
  _estado_eni: ["_estado_eni", "_encuesta_inicial"],
};

function findEntity(hass, explicitId, suffix) {
  if (explicitId && hass.states[explicitId]) return hass.states[explicitId];
  const suffixes = SUFFIX_ALIASES[suffix] || [suffix];
  const match = Object.keys(hass.states).find(
    (id) => id.startsWith("sensor.nutriplan_bridge_") && suffixes.some((sfx) => id.endsWith(sfx))
  );
  return match ? hass.states[match] : undefined;
}

function formatDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

class NutriplanBridgeCard extends HTMLElement {
  constructor() {
    super();
    this._expanded = new Set();
    // subingesta_id of platos whose full detail (recipe, ingredients,
    // actions) is expanded - collapsed by default so a meal with several
    // dishes only shows a short, scannable list on first tap (mobile-first).
    this._platoExpanded = new Set();
    // subingesta_id -> { status: "loading"|"options"|"applying"|"error", options?, franja?, error? }
    this._changeState = new Map();
    // Image URLs that failed to load at least once - once known-broken, we
    // stop even attempting an <img> for them on future renders instead of
    // re-triggering the same failed request (and its icon-swap flicker)
    // every time the card re-renders.
    this._brokenImages = new Set();
    // Which plan/day is being viewed: planId null = the current plan, day
    // null = today. Other days/plans come from nutriplan_bridge.obtener_comidas
    // and are cached per plan ("current" or the plan id).
    this._view = { planId: null, day: null };
    this._weekCache = new Map();
    this._weekLoading = new Set();
    this._weekError = new Map();
    this._eaten = loadEaten(); // local fallback, only used when the HA sensor is missing
    this._optimistic = new Map(); // franja -> bool, shown until HA confirms the new state
    this._weekView = false;
    this._chatOpen = false;
    this._chartOpen = false;
    this._numDietas = 0;
    this._chatDraft = "";
    // Manual body measurement (opt-in: show_measurement). Never sent without a confirmation.
    this._meas = { status: "idle", values: {}, msg: "" };
    this._chatStatus = "";
    this._book = { status: "idle", slots: [], selected: null, note: "", msg: "" };
    // Shopping list panel: { scope: "week"|"day", status, items, error, todoMsg }
    this._shop = { scope: null, status: "idle" };
    // Attach the shadow root immediately: Lovelace can call setConfig()
    // (e.g. for the "add card" preview) before this element is connected
    // to the document, so connectedCallback is too late and leaves
    // this.shadowRoot null, throwing and surfacing as "Error de
    // configuración" with no useful detail.
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    this._config = config || {};
    this._safeRender();
  }

  set hass(hass) {
    this._hass = hass;
    // Home Assistant calls this setter on EVERY entity state change in the
    // whole system, not just this card's entities - re-rendering every
    // single time (recreating every <img> from scratch, resetting hover/
    // scroll state) is what made the card visibly jump/flicker. HA keeps a
    // stable object reference per entity when it hasn't changed, so
    // comparing references lets us skip the vast majority of these calls
    // and only actually re-render when something we show has changed.
    const cfg = this._config || {};
    const entities = [
      findEntity(hass, cfg.plan_entity, "_plan_actual"),
      findEntity(hass, cfg.meals_entity, "_comidas_hoy"),
      findEntity(hass, cfg.appointment_entity, "_proxima_cita"),
      findEntity(hass, cfg.tracking_entity, "_seguimientos"),
      findEntity(hass, cfg.dietista_entity, "_dietista"),
      findEntity(hass, cfg.chat_entity, "_mensajes_sin_leer"),
      findEntity(hass, cfg.charlas_entity, "_charlas"),
      findEntity(hass, cfg.eni_entity, "_estado_eni"),
      findEntity(hass, cfg.eaten_entity, "_comidas_tomadas_hoy"),
    ];
    const changed = !this._lastEntities || entities.some((e, i) => e !== this._lastEntities[i]);
    this._lastEntities = entities;
    if (changed) {
      // The plan/meals sensors changed (poll, or a dish was swapped): any
      // cached copy of the current plan's week is potentially stale.
      this._weekCache.delete("current");
      this._weekError.delete("current");
      this._optimistic.clear();
      const typing = this.shadowRoot.activeElement;
      if (!(typing && typing.matches && typing.matches("textarea"))) this._safeRender();
    }
  }

  getCardSize() {
    return 6;
  }

  // Sections (grid) dashboards: full width by default, resizable, height follows content.
  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  static getStubConfig() {
    return {};
  }

  static getConfigElement() {
    return document.createElement("nutriplan-bridge-card-editor");
  }

  _safeRender() {
    try {
      this._render();
    } catch (err) {
      // Never let a render bug surface as HA's generic "Error de
      // configuración" - show what actually broke, inside our own card.
      this._ensureDom();
      const card = this.shadowRoot.querySelector("ha-card");
      if (card) {
        card.innerHTML = `<div class="unavailable">Nutriplan Bridge: error al renderizar la tarjeta.<br>${escapeHtml(
          err.message || String(err)
        )}</div>`;
      }
      console.error("nutriplan-bridge-card render error", err);
    }
  }

  _ensureDom() {
    if (this.shadowRoot.childElementCount) return;
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 16px; }
        .header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
        .header ha-icon { color: var(--primary-color); --mdc-icon-size: 28px; }
        .header .avatar { width: 40px; height: 40px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
        .header .title { font-size: 1.2em; font-weight: 500; color: var(--primary-text-color); }
        .header .subtitle { font-size: 0.9em; color: var(--secondary-text-color); }
        .section { margin-top: 14px; }
        .section-title {
          font-size: 0.78em; text-transform: uppercase; letter-spacing: .04em;
          color: var(--secondary-text-color); margin-bottom: 6px; font-weight: 500;
        }
        .meals { display: flex; flex-direction: column; gap: 6px; }
        .meal-card {
          border-radius: 10px;
          background: var(--secondary-background-color, rgba(127,127,127,.08));
          overflow: hidden;
        }
        .meal-header {
          display: flex; align-items: center; gap: 8px; padding: 8px 12px;
          cursor: pointer; user-select: none;
        }
        /* Also not scoped to .meal-header for the same reason as .meal-thumb
           below: this is the fallback icon shown in place of a broken/missing
           thumbnail in any row, not just the meal header. */
        ha-icon.meal-type { --mdc-icon-size: 18px; color: var(--primary-color); flex-shrink: 0; }
        /* Not scoped to .meal-header: this class is reused for the small
           thumbnail in every plato/option row too. Scoping it to
           ".meal-header .meal-thumb" only meant those other thumbnails had
           NO size limit at all and rendered at the source photo's native
           resolution - fine for a small image, huge for a high-res one. */
        .meal-thumb {
          width: 28px; height: 28px; border-radius: 6px; object-fit: cover; flex-shrink: 0;
        }
        .meal-header .franja { font-size: 0.72em; color: var(--secondary-text-color); flex-shrink: 0; }
        .meal-header .plato {
          font-size: 0.92em; color: var(--primary-text-color); flex: 1; min-width: 0;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .meal-header ha-icon.chevron {
          --mdc-icon-size: 20px; color: var(--secondary-text-color);
          transition: transform .15s ease; flex-shrink: 0;
        }
        .meal-header.expanded ha-icon.chevron { transform: rotate(180deg); }
        .meal-body { padding: 0 10px 10px 10px; display: flex; flex-direction: column; gap: 4px; }
        .plato-row:not(:first-child) { border-top: 1px solid var(--divider-color, rgba(127,127,127,.2)); }
        .plato-summary {
          display: flex; align-items: center; gap: 10px; padding: 8px 4px;
          cursor: pointer; user-select: none;
        }
        .plato-summary-text { flex: 1; min-width: 0; }
        .plato-summary-nombre {
          font-size: 0.88em; color: var(--primary-text-color);
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .plato-summary-kcal { font-size: 0.75em; color: var(--secondary-text-color); }
        ha-icon.chevron.small {
          --mdc-icon-size: 18px; flex-shrink: 0; color: var(--secondary-text-color);
          transition: transform .15s ease;
        }
        ha-icon.chevron.small.expanded { transform: rotate(180deg); }
        .plato-detail { padding: 0 4px 12px 4px; }
        img.plato-img {
          width: 100%; height: 160px; object-fit: cover; border-radius: 8px; margin-bottom: 8px;
          display: block;
        }
        .plato-img-placeholder {
          width: 100%; height: 160px; border-radius: 8px; margin-bottom: 8px;
          display: flex; align-items: center; justify-content: center;
          background: var(--secondary-background-color, rgba(127,127,127,.08));
        }
        .plato-img-placeholder ha-icon { --mdc-icon-size: 40px; color: var(--secondary-text-color); }
        .meal-meta { display: flex; gap: 14px; flex-wrap: wrap; font-size: 0.78em; color: var(--secondary-text-color); margin-bottom: 6px; }
        .meal-alergenos { font-size: 0.76em; color: var(--secondary-text-color); margin-bottom: 6px; }
        .meal-receta { font-size: 0.88em; color: var(--primary-text-color); white-space: pre-line; margin-bottom: 8px; }
        .meal-ingredientes { margin: 0; padding-left: 18px; font-size: 0.85em; color: var(--primary-text-color); }
        .meal-ingredientes li { margin-bottom: 2px; }
        .empty { color: var(--secondary-text-color); font-style: italic; font-size: 0.9em; }
        .appointment { display: flex; align-items: center; gap: 10px; }
        .appointment ha-icon { --mdc-icon-size: 22px; color: var(--primary-color); }
        .metrics { display: flex; gap: 18px; flex-wrap: wrap; }
        .metric { display: flex; flex-direction: column; }
        .metric .value { font-size: 1.15em; color: var(--primary-text-color); }
        .metric .label { font-size: 0.75em; color: var(--secondary-text-color); }
        .unavailable { padding: 8px 0; color: var(--secondary-text-color); }
        .rating-stars { display: flex; gap: 2px; margin-bottom: 6px; }
        .rating-stars ha-icon.rating-star {
          --mdc-icon-size: 18px; color: var(--primary-color); cursor: pointer;
        }
        .plato-actions { margin-top: 8px; }
        .action-btn {
          font: inherit; font-size: 0.82em; color: var(--primary-color);
          background: var(--secondary-background-color, rgba(127,127,127,.12));
          border: none; border-radius: 14px; padding: 6px 12px; margin: 0 6px 6px 0;
          cursor: pointer;
        }
        .action-btn:hover { filter: brightness(0.95); }
        .cambiar-btn {
          font: inherit; font-weight: 500; font-size: 0.88em;
          color: var(--text-primary-color, #fff);
          background: var(--primary-color); border: none; border-radius: 20px;
          padding: 9px 18px; margin: 0 6px 6px 0; cursor: pointer;
          display: inline-flex; align-items: center; gap: 6px;
          box-shadow: 0 1px 3px rgba(0,0,0,.2);
        }
        .cambiar-btn:hover { filter: brightness(1.08); }
        .cambiar-btn ha-icon { --mdc-icon-size: 18px; }
        .action-status { font-size: 0.82em; color: var(--secondary-text-color); margin-bottom: 6px; }
        .action-status.error { color: var(--error-color, #c62828); }
        .opciones-header {
          font-size: 0.78em; text-transform: uppercase; letter-spacing: .03em;
          color: var(--secondary-text-color); margin: 2px 0 6px 0;
        }
        .opciones-plato {
          display: flex; flex-direction: column; gap: 4px; margin-bottom: 6px;
          background: var(--secondary-background-color, rgba(127,127,127,.06));
          border-radius: 10px; padding: 2px 8px;
        }
        .header-text { flex: 1; min-width: 0; }
        .badge {
          display: inline-flex; align-items: center; gap: 4px; flex-shrink: 0;
          background: var(--primary-color); color: var(--text-primary-color, #fff);
          border-radius: 12px; padding: 2px 9px; font-size: 0.8em;
        }
        .badge ha-icon { --mdc-icon-size: 14px; }
        .plan-nav { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
        .plan-nav .plan-name {
          flex: 1; text-align: center; font-size: 0.85em; color: var(--primary-text-color);
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .plan-nav .plan-name small { color: var(--secondary-text-color); }
        .nav-btn {
          font: inherit; border: none; border-radius: 50%; width: 30px; height: 30px; flex-shrink: 0;
          cursor: pointer; color: var(--primary-color);
          background: var(--secondary-background-color, rgba(127,127,127,.12));
          display: inline-flex; align-items: center; justify-content: center;
        }
        .nav-btn[disabled] { opacity: .3; cursor: default; }
        .nav-btn ha-icon { --mdc-icon-size: 20px; }
        .link-btn {
          font: inherit; font-size: 0.78em; border: none; background: none;
          color: var(--primary-color); cursor: pointer; padding: 4px 6px;
        }
        .days { display: flex; gap: 6px; margin-bottom: 10px; overflow-x: auto; padding-bottom: 2px; }
        .day-chip {
          font: inherit; font-size: 0.82em; min-width: 34px; height: 34px; padding: 0 8px;
          border: none; border-radius: 17px; cursor: pointer; flex-shrink: 0; position: relative;
          color: var(--primary-text-color);
          background: var(--secondary-background-color, rgba(127,127,127,.12));
        }
        .day-chip.selected { background: var(--primary-color); color: var(--text-primary-color, #fff); }
        .day-chip.today::after {
          content: ""; position: absolute; bottom: 3px; left: 50%; margin-left: -2px;
          width: 4px; height: 4px; border-radius: 50%; background: currentColor;
        }
        .readonly-note { font-size: 0.78em; color: var(--secondary-text-color); margin: 0 0 8px 0; font-style: italic; }
        .day-summary {
          display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 0.8em; margin: 0 0 8px 0;
          color: var(--secondary-text-color);
        }
        .day-summary b { color: var(--primary-text-color); font-weight: 500; }
        .trend { display: flex; align-items: center; gap: 12px; margin-top: 10px; }
        .trend .spark { width: 120px; height: 28px; flex-shrink: 0; }
        .trend .delta { font-size: 0.82em; color: var(--secondary-text-color); }
        .meal-card.next { box-shadow: inset 3px 0 0 var(--primary-color); }
        .meal-card.past { opacity: .55; }
        .next-tag {
          font-size: 0.65em; text-transform: uppercase; letter-spacing: .04em; flex-shrink: 0;
          color: var(--text-primary-color, #fff); background: var(--primary-color);
          border-radius: 8px; padding: 1px 6px;
        }
        ha-icon.eaten-toggle { --mdc-icon-size: 22px; color: var(--secondary-text-color); cursor: pointer; flex-shrink: 0; }
        ha-icon.eaten-toggle.done { color: var(--primary-color); }
        .progress { height: 6px; border-radius: 3px; background: var(--divider-color, rgba(127,127,127,.25)); overflow: hidden; margin: 2px 0 4px 0; }
        .progress > div { height: 100%; background: var(--primary-color); transition: width .2s ease; }
        .progress-label { font-size: 0.75em; color: var(--secondary-text-color); margin-bottom: 8px; }
        .macro-bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; margin: 4px 0 4px 0; }
        .macro-bar span { display: block; height: 100%; }
        .macro-legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 0.72em; color: var(--secondary-text-color); margin-bottom: 8px; }
        .macro-legend i { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }
        .section-head { display: flex; align-items: center; justify-content: space-between; }
        .appointment small { color: var(--secondary-text-color); }
        .shop-scope { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
        .shop-group { font-size: 0.75em; text-transform: uppercase; letter-spacing: .03em; color: var(--secondary-text-color); margin: 8px 0 2px 0; }
        .shop-item { display: flex; align-items: center; gap: 8px; padding: 3px 0; font-size: 0.88em; color: var(--primary-text-color); cursor: pointer; }
        .shop-item.checked span.shop-name { text-decoration: line-through; opacity: .5; }
        .shop-item .shop-qty { margin-left: auto; color: var(--secondary-text-color); font-size: 0.9em; white-space: nowrap; }
        .nutri-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin: 0 0 8px 0; }
        .nutri { display: flex; flex-direction: column; align-items: center; background: var(--secondary-background-color, rgba(127,127,127,.08)); border-radius: 8px; padding: 4px 2px; }
        .nutri-v { font-size: 0.85em; color: var(--primary-text-color); }
        .nutri-v small { color: var(--secondary-text-color); font-size: 0.8em; }
        .nutri-l { font-size: 0.66em; color: var(--secondary-text-color); }
        .week-day { border-radius: 10px; background: var(--secondary-background-color, rgba(127,127,127,.08)); padding: 8px 12px; margin-bottom: 6px; cursor: pointer; }
        .week-day.selected { box-shadow: inset 3px 0 0 var(--primary-color); }
        .week-day-head { display: flex; justify-content: space-between; font-size: 0.88em; font-weight: 500; color: var(--primary-text-color); margin-bottom: 2px; }
        .week-day-head small { color: var(--primary-color); font-weight: 400; }
        .week-meal { font-size: 0.78em; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .week-meal b { color: var(--primary-text-color); font-weight: 500; }
        .head-actions { display: flex; align-items: center; gap: 2px; flex-shrink: 0; }
        a.contact { color: var(--primary-color); display: inline-flex; padding: 4px; text-decoration: none; }
        a.contact ha-icon { --mdc-icon-size: 20px; }
        button.badge { border: none; font: inherit; font-size: 0.8em; cursor: pointer; }
        .chat-panel { background: var(--secondary-background-color, rgba(127,127,127,.08)); border-radius: 10px; padding: 8px 12px; margin-bottom: 10px; }
        .chat-msg { font-size: 0.85em; color: var(--primary-text-color); padding: 4px 0; white-space: pre-line; }
        .chat-msg small { display: block; color: var(--secondary-text-color); font-size: 0.8em; }
        .chat-compose { display: flex; gap: 6px; align-items: flex-end; margin-top: 8px; }
        .chat-compose textarea, .book-note {
          flex: 1; font: inherit; font-size: 0.88em; resize: vertical; min-height: 36px; box-sizing: border-box;
          color: var(--primary-text-color); background: var(--card-background-color, #fff);
          border: 1px solid var(--divider-color, rgba(127,127,127,.4)); border-radius: 8px; padding: 6px 8px;
        }
        .book-note { width: 100%; margin: 6px 0; }
        .meas-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin: 8px 0; }
        .meas-grid label { display: flex; flex-direction: column; font-size: 0.78em; color: var(--secondary-text-color); }
        .meas-input {
          font: inherit; font-size: 1.05em; margin-top: 2px; box-sizing: border-box; width: 100%;
          color: var(--primary-text-color); background: var(--card-background-color, #fff);
          border: 1px solid var(--divider-color, rgba(127,127,127,.4)); border-radius: 8px; padding: 6px 8px;
        }
        .chat-attach { cursor: pointer; display: inline-flex; align-items: center; gap: 4px; }
        .chat-attach ha-icon { --mdc-icon-size: 16px; }
        .alimento-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; }
        .alimento-row + .alimento-row { border-top: 1px solid var(--divider-color, rgba(127,127,127,.2)); }
        .appt-actions { margin-top: 8px; }
        .appt-meta { font-size: 0.78em; color: var(--secondary-text-color); margin: 2px 0 0 32px; }
        .charla { display: flex; align-items: center; gap: 8px; padding: 6px 0; font-size: 0.88em; }
        .charla + .charla { border-top: 1px solid var(--divider-color, rgba(127,127,127,.2)); }
        .charla-text { flex: 1; min-width: 0; }
        .charla-title { color: var(--primary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .charla-sub { font-size: 0.8em; color: var(--secondary-text-color); }
        .charla .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--primary-color); flex-shrink: 0; }
        a.charla-link { color: var(--primary-color); display: inline-flex; padding: 4px; text-decoration: none; }
        a.charla-link ha-icon { --mdc-icon-size: 20px; }
        .slot-day { font-size: 0.78em; color: var(--secondary-text-color); margin: 8px 0 4px 0; text-transform: capitalize; }
        .slot-btn {
          font: inherit; font-size: 0.82em; border: 1px solid var(--divider-color, rgba(127,127,127,.4));
          background: none; color: var(--primary-text-color); border-radius: 14px; padding: 4px 10px; margin: 0 4px 4px 0; cursor: pointer;
        }
        .slot-btn.selected { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
        [tabindex]:focus-visible, button:focus-visible, a:focus-visible, textarea:focus-visible {
          outline: 2px solid var(--primary-color); outline-offset: 2px;
        }
        .chip { display: inline-flex; align-items: center; gap: 4px; border-radius: 12px; padding: 2px 8px; margin: 0 4px 4px 0; font-size: 0.78em; }
        .chip ha-icon { --mdc-icon-size: 14px; }
        .chip.warn { color: var(--warning-color, #b26a00); background: rgba(255, 152, 0, .14); }
        .meal-alergenos { margin-bottom: 6px; }
        .kcal-pill {
          font-size: 0.68em; color: var(--secondary-text-color); border: 1px solid var(--divider-color, rgba(127,127,127,.35));
          border-radius: 10px; padding: 0 6px; flex-shrink: 0; white-space: nowrap;
        }
        .stars-mini { font-size: 0.75em; color: #f9a825; margin-left: 6px; }
        .section-title ha-icon { --mdc-icon-size: 14px; vertical-align: -3px; margin-right: 4px; }
        .empty-state { display: flex; align-items: center; gap: 8px; color: var(--secondary-text-color); font-size: 0.9em; padding: 6px 0; }
        .empty-state ha-icon { --mdc-icon-size: 22px; opacity: .7; }
        .skeleton { height: 38px; border-radius: 10px; margin-bottom: 6px;
          background: linear-gradient(90deg, rgba(127,127,127,.10) 25%, rgba(127,127,127,.22) 37%, rgba(127,127,127,.10) 63%);
          background-size: 400% 100%; animation: shimmer 1.4s ease infinite; }
        @keyframes shimmer { 0% { background-position: 100% 50%; } 100% { background-position: 0 50%; } }
        ha-card.compact { padding: 10px; }
        ha-card.compact .meal-header { padding: 5px 10px; }
        ha-card.compact .section { margin-top: 8px; }
        @media (max-width: 380px) { .kcal-pill { display: none; } }
        @media (prefers-reduced-motion: reduce) {
          .skeleton, .progress > div, ha-icon.chevron, ha-icon.chevron.small { animation: none; transition: none; }
        }
        .trend { cursor: pointer; }
        .bigchart { width: 100%; height: auto; margin-top: 8px; }
        .opciones-plato .plato-row:not(:first-child) { border-top: 1px solid var(--divider-color, rgba(127,127,127,.2)); }
      </style>
      <ha-card>
        <div class="unavailable">Nutriplan Bridge: esperando datos de Home Assistant…</div>
      </ha-card>
    `;
    // Delegated once on the shadow root (survives ha-card's innerHTML being
    // replaced on every render) instead of re-attaching listeners each time.
    this.shadowRoot.addEventListener("click", (ev) => this._handleClick(ev));
    this.shadowRoot.addEventListener("change", (ev) => this._handleChange(ev));
    // Keyboard: Enter/Space activates anything exposed as a button/checkbox.
    this.shadowRoot.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      const t = ev.target;
      if (t && t.matches && t.matches('[role="button"], [role="checkbox"]')) {
        ev.preventDefault();
        t.click();
      }
    });
    this.shadowRoot.addEventListener("input", (ev) => {
      if (ev.target.classList && ev.target.classList.contains("chat-input")) this._chatDraft = ev.target.value;
      if (ev.target.classList && ev.target.classList.contains("book-note")) this._book.note = ev.target.value;
      if (ev.target.classList && ev.target.classList.contains("meas-input")) this._meas.values[ev.target.dataset.field] = ev.target.value;
    });
    // Swipe left/right over the meal list to move to the next/previous day.
    let sx = null;
    let sy = null;
    this.shadowRoot.addEventListener(
      "touchstart",
      (ev) => {
        sx = ev.changedTouches[0].clientX;
        sy = ev.changedTouches[0].clientY;
      },
      { passive: true }
    );
    this.shadowRoot.addEventListener(
      "touchend",
      (ev) => {
        if (sx === null) return;
        const dx = ev.changedTouches[0].clientX - sx;
        const dy = ev.changedTouches[0].clientY - sy;
        sx = null;
        if (Math.abs(dx) < 70 || Math.abs(dy) > 40 || this._numDietas < 2) return;
        if (!ev.target.closest || !ev.target.closest(".meals")) return;
        const next = Math.max(0, Math.min(this._numDietas - 1, (this._dietaIndex ?? 0) + (dx < 0 ? 1 : -1)));
        if (next !== this._dietaIndex) this._selectDay(next);
      },
      { passive: true }
    );
    // "error" on <img> doesn't bubble, so this only works with capture:true.
    this.shadowRoot.addEventListener("error", (ev) => this._handleImageError(ev), true);
  }

  _handleImageError(ev) {
    const img = ev.target;
    if (!(img instanceof HTMLImageElement)) return;
    if (img.src) this._brokenImages.add(img.src);
    if (img.classList.contains("meal-thumb")) {
      img.replaceWith(
        Object.assign(document.createElement("ha-icon"), {
          className: "meal-type",
          icon: img.dataset.fallbackIcon || "mdi:food",
        })
      );
    } else if (img.classList.contains("avatar")) {
      img.replaceWith(Object.assign(document.createElement("ha-icon"), { icon: "mdi:food-apple" }));
    } else if (img.classList.contains("plato-img")) {
      const placeholder = document.createElement("div");
      placeholder.className = "plato-img-placeholder";
      placeholder.innerHTML = '<ha-icon icon="mdi:food"></ha-icon>';
      img.replaceWith(placeholder);
    } else {
      img.style.display = "none";
    }
  }

  _selectDay(day) {
    this._view.day = day;
    this._weekError.delete(this._viewKey());
    this._safeRender();
  }

  _selectPlan(planId) {
    this._view.planId = planId;
    this._weekError.delete(this._viewKey());
    this._safeRender();
  }

  _viewKey() {
    return this._view.planId === null ? "current" : String(this._view.planId);
  }

  async _loadWeek(key, planId) {
    this._weekLoading.add(key);
    try {
      const data = { };
      if (planId !== null && planId !== undefined) data.plan_id = planId;
      const week = await this._callService("obtener_comidas", this._withAccount(data), true);
      this._weekCache.set(key, week);
      this._weekError.delete(key);
    } catch (err) {
      this._weekError.set(key, err.message || String(err));
    }
    this._weekLoading.delete(key);
    this._safeRender();
  }

  _handleChange(ev) {
    const fileInput = ev.target.closest && ev.target.closest(".chat-file");
    if (fileInput) {
      const file = fileInput.files && fileInput.files[0];
      fileInput.value = "";
      this._uploadChatFile(file);
      return;
    }
    const box = ev.target.closest && ev.target.closest(".shop-check");
    if (!box) return;
    const key = this._shopStorageKey();
    const checked = loadChecked(key);
    if (box.checked) checked.add(box.dataset.name);
    else checked.delete(box.dataset.name);
    saveChecked(key, checked);
    this._safeRender();
  }

  _shopStorageKey() {
    return `nutriplan_bridge_shop_${this._planId}_${this._shop.scope}_${this._shop.scope === "day" ? this._dietaIndex : "all"}`;
  }

  async _loadShopping(scope, todoEntity) {
    this._shop = { scope, status: "loading" };
    this._safeRender();
    const data = {};
    if (this._view.planId !== null) data.plan_id = this._view.planId;
    if (scope === "day") data.dias = [this._dietaIndex];
    if (todoEntity) data.todo_entity = todoEntity;
    try {
      const res = await this._callService("lista_compra", this._withAccount(data), true);
      this._shop = {
        scope,
        status: "ready",
        items: res.items || [],
        todoMsg: todoEntity ? `Añadidos ${res.total} ingredientes a la lista.` : "",
      };
    } catch (err) {
      this._shop = { scope, status: "error", error: err.message || String(err) };
    }
    this._safeRender();
  }

  _eatenSet() {
    const entity = this._eatenEntity;
    if (!entity) return this._eaten;
    const set = new Set((entity.attributes && entity.attributes.tomadas) || []);
    this._optimistic.forEach((on, franja) => (on ? set.add(franja) : set.delete(franja)));
    return set;
  }

  _toggleEaten(franja) {
    if (this._eatenEntity) {
      const now = !this._eatenSet().has(franja);
      this._optimistic.set(franja, now);
      this._safeRender();
      this._callService("marcar_comida", this._withAccount({ franja, tomada: now })).catch((err) => {
        console.error("nutriplan-bridge-card: marcar_comida failed", err);
        this._optimistic.delete(franja);
        this._safeRender();
      });
      return;
    }
    if (this._eaten.has(franja)) this._eaten.delete(franja);
    else this._eaten.add(franja);
    saveEaten(this._eaten);
    this._safeRender();
  }

  _imgOk(src) {
    return !!src && !this._brokenImages.has(src) && (this._config || {}).hide_images !== true;
  }

  async _copyShopping() {
    const items = (this._shop && this._shop.items) || [];
    const checked = loadChecked(this._shopStorageKey());
    const text =
      "Lista de la compra\n" +
      items
        .filter((it) => !checked.has(it.nombre))
        .map((it) => `- ${it.nombre}${it.cantidad_g ? ` (${it.cantidad_g} g)` : ""}`)
        .join("\n");
    let msg = "Lista copiada al portapapeles.";
    try {
      if (navigator.share && /Mobi|Android/i.test(navigator.userAgent)) await navigator.share({ text });
      else await navigator.clipboard.writeText(text);
    } catch (e) {
      msg = "No se pudo copiar la lista.";
    }
    this._shop = { ...this._shop, todoMsg: msg };
    this._safeRender();
  }

  async _sendChat() {
    const text = (this._chatDraft || "").trim();
    if (!text) return;
    this._chatStatus = "Enviando…";
    this._safeRender();
    try {
      await this._callService("enviar_mensaje", this._withAccount({ texto: text }));
      this._chatDraft = "";
      this._chatStatus = "Mensaje enviado.";
    } catch (err) {
      this._chatStatus = err.message || "No se pudo enviar el mensaje.";
    }
    this._safeRender();
  }

  async _loadSlots() {
    this._book = { status: "loading", slots: [], selected: null, note: "", msg: "" };
    this._safeRender();
    try {
      const res = await this._callService("citas_disponibles", this._withAccount({}), true);
      this._book = { status: "ready", slots: res.huecos || [], selected: null, note: "", msg: "" };
    } catch (err) {
      this._book = { status: "error", slots: [], selected: null, note: "", msg: err.message || String(err) };
    }
    this._safeRender();
  }

  async _sendBooking() {
    const sel = this._book.selected;
    if (!sel) return;
    if (!window.confirm(`¿Pedir cita el ${sel.fecha} a las ${sel.hora}? Se enviará la solicitud a tu dietista.`)) return;
    const note = (this._book.note || "").trim();
    this._book = { ...this._book, status: "sending" };
    this._safeRender();
    try {
      await this._callService(
        "solicitar_cita",
        this._withAccount({ fecha: sel.fecha, hora: String(sel.hora), ...(note ? { nota: note } : {}) }),
        true
      );
      this._book = { status: "done", slots: [], selected: null, note: "", msg: "Solicitud de cita enviada." };
    } catch (err) {
      this._book = { ...this._book, status: "ready", msg: err.message || String(err) };
    }
    this._safeRender();
  }

  async _confirmCita(citaId) {
    try {
      await this._callService("confirmar_cita", this._withAccount(citaId ? { cita_id: citaId } : {}), true);
      this._book = { status: "done", slots: [], selected: null, note: "", msg: "Cita confirmada." };
    } catch (err) {
      this._book = { status: "error", slots: [], selected: null, note: "", msg: err.message || String(err) };
    }
    this._safeRender();
  }

  async _cancelCita(citaId) {
    if (!window.confirm("¿Cancelar tu próxima cita? No se puede deshacer desde aquí.")) return;
    try {
      await this._callService("cancelar_cita", this._withAccount(citaId ? { cita_id: citaId } : {}), true);
      this._book = { status: "done", slots: [], selected: null, note: "", msg: "Cita cancelada." };
    } catch (err) {
      this._book = { status: "error", slots: [], selected: null, note: "", msg: err.message || String(err) };
    }
    this._safeRender();
  }

  async _uploadChatFile(file) {
    if (!file) return;
    if (file.size > 15 * 1024 * 1024) {
      this._chatStatus = "El archivo supera los 15 MB.";
      this._safeRender();
      return;
    }
    this._chatStatus = `Enviando ${file.name}…`;
    this._safeRender();
    try {
      const auth = this._hass.auth;
      if (auth && auth.expired && auth.refreshAccessToken) await auth.refreshAccessToken();
      const token = auth && (auth.accessToken || (auth.data && auth.data.access_token));
      const form = new FormData();
      form.append("text", (this._chatDraft || "").trim());
      if (this._configEntryId) form.append("config_entry_id", this._configEntryId);
      form.append("file", file, file.name);
      const url = this._hass.hassUrl ? this._hass.hassUrl("/api/nutriplan_bridge/chat_upload") : "/api/nutriplan_bridge/chat_upload";
      const res = await fetch(url, { method: "POST", headers: token ? { Authorization: `Bearer ${token}` } : {}, body: form });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.message || `Error ${res.status}`);
      this._chatDraft = "";
      this._chatStatus = `Enviado: ${file.name}`;
    } catch (err) {
      this._chatStatus = err.message || "No se pudo enviar el archivo.";
    }
    this._safeRender();
  }

  async _openMeasurement() {
    this._meas = { status: "loading", values: {}, msg: "" };
    this._safeRender();
    try {
      const res = await this._callService("medicion_abierta", this._withAccount({}), true);
      this._meas = res && res.abierta
        ? { status: "form", values: {}, msg: "" }
        : { status: "none", values: {}, msg: "No hay ninguna medición abierta: tu dietista tiene que abrirla en DietoPro." };
    } catch (err) {
      this._meas = { status: "none", values: {}, msg: err.message || String(err) };
    }
    this._safeRender();
  }

  async _sendMeasurement() {
    const entries = Object.entries(this._meas.values)
      .map(([k, v]) => [k, Number(String(v).replace(",", "."))])
      .filter(([, v]) => Number.isFinite(v) && v > 0);
    if (!entries.length) {
      this._meas = { ...this._meas, msg: "Escribe al menos un valor." };
      this._safeRender();
      return;
    }
    const labels = { peso: "peso", porcentaje_grasa: "% grasa", perimetro_cintura: "cintura", perimetro_cadera: "cadera" };
    const summary = entries.map(([k, v]) => `${labels[k] || k} ${v}`).join(", ");
    if (!window.confirm(`¿Enviar a tu dietista (${summary}) y cerrar la medición? Se escribe en tu cuenta real de DietoPro.`)) return;
    this._meas = { ...this._meas, status: "sending", msg: "" };
    this._safeRender();
    try {
      await this._callService("registrar_medicion", this._withAccount({ ...Object.fromEntries(entries), finalizar: true }), true);
      this._meas = { status: "done", values: {}, msg: "Medición enviada." };
    } catch (err) {
      this._meas = { status: "form", values: this._meas.values, msg: err.message || String(err) };
    }
    this._safeRender();
  }

  async _markChatRead() {
    try {
      await this._callService("marcar_chat_leido", this._withAccount({}));
    } catch (err) {
      console.error("nutriplan-bridge-card: marcar_chat_leido failed", err);
    }
    this._chatOpen = false;
    this._safeRender();
  }

  _toggle(franja) {
    if (this._expanded.has(franja)) {
      this._expanded.delete(franja);
    } else {
      this._expanded.add(franja);
    }
    this._safeRender();
  }

  async _callService(service, data, wantResponse) {
    const msg = { type: "call_service", domain: "nutriplan_bridge", service, service_data: data };
    if (wantResponse) msg.return_response = true;
    const result = await this._hass.connection.sendMessagePromise(msg);
    return wantResponse ? result?.response ?? result : result;
  }

  _extractOptions(response) {
    // The backend already finds the right list and shapes each item with
    // sensor.py's own _plato_detail() (same fields as every other plato:
    // nombre, receta, ingredientes, imagen, calorias...), so there's no
    // guessing left to do here.
    const opciones = response && response.opciones;
    return Array.isArray(opciones) ? opciones : [];
  }

  _withAccount(data) {
    return this._configEntryId ? { ...data, config_entry_id: this._configEntryId } : data;
  }

  async _startCambiarPlato(subingestaId, platoId, franja) {
    this._changeState.set(subingestaId, { status: "loading" });
    this._safeRender();
    try {
      const response = await this._callService(
        "opciones_plato",
        this._withAccount({ plato_id: platoId, franja }),
        true
      );
      this._changeState.set(subingestaId, {
        status: "options",
        options: this._extractOptions(response),
        expandedOptionId: null,
        franja,
        platoId,
      });
    } catch (err) {
      this._changeState.set(subingestaId, { status: "error", error: err.message || String(err), franja });
    }
    this._safeRender();
  }

  async _startCambiarAlimento(subingestaId, alimentoId, cantidad, franja) {
    const base = { kind: "alimento", franja, alimentoId, cantidad };
    this._changeState.set(subingestaId, { ...base, status: "loading" });
    this._safeRender();
    try {
      const res = await this._callService(
        "opciones_alimento",
        this._withAccount({ alimento_id: alimentoId, cantidad: Number(cantidad) }),
        true
      );
      this._changeState.set(subingestaId, { ...base, status: "options", options: (res && res.opciones) || [] });
    } catch (err) {
      this._changeState.set(subingestaId, { ...base, status: "error", error: err.message || String(err) });
    }
    this._safeRender();
  }

  async _applyCambiarAlimento(subingestaId, nuevoId, nuevaCantidad, nombre) {
    const prev = this._changeState.get(subingestaId) || {};
    if (!window.confirm(`¿Cambiar por ${nombre || "este alimento"} (${nuevaCantidad} g)? Se modificará tu plan real.`)) return;
    this._changeState.set(subingestaId, { ...prev, status: "applying" });
    this._safeRender();
    try {
      await this._callService(
        "cambiar_alimento",
        this._withAccount({
          plan_id: this._planId,
          dieta: this._dietaIndex,
          franja: prev.franja,
          alimento_actual_id: Number(prev.alimentoId),
          cantidad_actual: Number(prev.cantidad),
          nuevo_alimento_id: Number(nuevoId),
          nueva_cantidad: Number(nuevaCantidad),
        }),
        true
      );
      this._changeState.delete(subingestaId);
      this._weekCache.delete("current");
    } catch (err) {
      this._changeState.set(subingestaId, { ...prev, status: "error", error: err.message || String(err) });
    }
    this._safeRender();
  }

  async _applyCambiarPlato(subingestaId, nuevoPlatoId) {
    const prev = this._changeState.get(subingestaId) || {};
    this._changeState.set(subingestaId, { status: "applying" });
    this._safeRender();
    try {
      await this._callService(
        "cambiar_plato",
        this._withAccount({
          plan_id: this._planId,
          dieta: this._dietaIndex,
          franja: prev.franja,
          plato_actual_id: prev.platoId,
          nuevo_plato_id: nuevoPlatoId,
        }),
        true
      );
      this._changeState.delete(subingestaId);
      this._weekCache.delete("current");
    } catch (err) {
      this._changeState.set(subingestaId, { status: "error", error: err.message || String(err), franja: prev.franja });
    }
    this._safeRender();
  }

  async _rateDish(superPlatoId, rating) {
    try {
      await this._callService("valorar_plato", this._withAccount({ super_plato_id: superPlatoId, rating }));
    } catch (err) {
      console.error("nutriplan-bridge-card: valorar_plato failed", err);
    }
  }

  _handleClick(ev) {
    if (ev.target.closest(".meas-open")) {
      this._openMeasurement();
      return;
    }
    if (ev.target.closest(".meas-close")) {
      this._meas = { status: "idle", values: {}, msg: "" };
      this._safeRender();
      return;
    }
    if (ev.target.closest(".meas-send")) {
      this._sendMeasurement();
      return;
    }
    if (ev.target.closest(".chat-send")) {
      this._sendChat();
      return;
    }
    if (ev.target.closest(".book-open")) {
      this._loadSlots();
      return;
    }
    if (ev.target.closest(".book-close")) {
      this._book = { status: "idle", slots: [], selected: null, note: "", msg: "" };
      this._safeRender();
      return;
    }
    const slotBtn = ev.target.closest(".slot-btn");
    if (slotBtn) {
      this._book.selected = { fecha: slotBtn.dataset.fecha, hora: slotBtn.dataset.hora };
      this._safeRender();
      return;
    }
    if (ev.target.closest(".book-send")) {
      this._sendBooking();
      return;
    }
    const confirmCita = ev.target.closest(".cita-confirm");
    if (confirmCita) {
      this._confirmCita(confirmCita.dataset.citaId);
      return;
    }
    const cancelCita = ev.target.closest(".cita-cancel");
    if (cancelCita) {
      this._cancelCita(cancelCita.dataset.citaId);
      return;
    }
    if (ev.target.closest(".week-toggle")) {
      this._weekView = !this._weekView;
      this._safeRender();
      return;
    }
    const weekDay = ev.target.closest(".week-day");
    if (weekDay) {
      this._weekView = false;
      this._selectDay(Number(weekDay.dataset.day));
      return;
    }
    if (ev.target.closest(".chat-toggle")) {
      this._chatOpen = !this._chatOpen;
      this._safeRender();
      return;
    }
    if (ev.target.closest(".chat-read")) {
      this._markChatRead();
      return;
    }
    if (ev.target.closest(".trend")) {
      this._chartOpen = !this._chartOpen;
      this._safeRender();
      return;
    }
    if (ev.target.closest(".shop-copy")) {
      this._copyShopping();
      return;
    }
    const eatenBtn = ev.target.closest(".eaten-toggle");
    if (eatenBtn) {
      this._toggleEaten(eatenBtn.dataset.franja);
      return;
    }
    const shopScope = ev.target.closest(".shop-load");
    if (shopScope) {
      this._loadShopping(shopScope.dataset.scope);
      return;
    }
    if (ev.target.closest(".shop-close")) {
      this._shop = { scope: null, status: "idle" };
      this._safeRender();
      return;
    }
    const shopTodo = ev.target.closest(".shop-todo");
    if (shopTodo) {
      this._loadShopping(this._shop.scope || "week", (this._config || {}).todo_entity);
      return;
    }
    const dayChip = ev.target.closest(".day-chip");
    if (dayChip) {
      this._selectDay(Number(dayChip.dataset.day));
      return;
    }
    const navBtn = ev.target.closest(".nav-btn");
    if (navBtn) {
      if (!navBtn.disabled && navBtn.dataset.planId !== undefined) {
        this._selectPlan(navBtn.dataset.planId === "" ? null : navBtn.dataset.planId);
      }
      return;
    }
    const currentBtn = ev.target.closest(".link-btn");
    if (currentBtn) {
      this._view = { planId: null, day: null };
      this._safeRender();
      return;
    }
    const header = ev.target.closest(".meal-header");
    if (header) {
      this._toggle(header.dataset.franja);
      return;
    }
    // Checked before the generic ".plato-summary" case below: an option row
    // carries both classes (same look/CSS), but toggles its own expand
    // state inside _changeState instead of the meal's _platoExpanded set.
    const optionToggle = ev.target.closest(".opcion-summary");
    if (optionToggle) {
      const subingesta = optionToggle.dataset.subingesta;
      const state = this._changeState.get(subingesta);
      if (state && state.status === "options") {
        const optionId = optionToggle.dataset.optionId;
        state.expandedOptionId = state.expandedOptionId === optionId ? null : optionId;
        this._safeRender();
      }
      return;
    }
    const platoToggle = ev.target.closest(".plato-summary");
    if (platoToggle) {
      const key = platoToggle.dataset.platoToggle;
      if (this._platoExpanded.has(key)) {
        this._platoExpanded.delete(key);
      } else {
        this._platoExpanded.add(key);
      }
      this._safeRender();
      return;
    }
    const acceptBtn = ev.target.closest(".opcion-aceptar");
    if (acceptBtn) {
      this._applyCambiarPlato(acceptBtn.dataset.subingesta, acceptBtn.dataset.nuevoId);
      return;
    }
    const alimentoPick = ev.target.closest(".alimento-pick");
    if (alimentoPick) {
      this._applyCambiarAlimento(
        alimentoPick.dataset.subingesta,
        alimentoPick.dataset.nuevoId,
        alimentoPick.dataset.nuevaCantidad,
        alimentoPick.dataset.nombre
      );
      return;
    }
    const cambiarBtn = ev.target.closest(".cambiar-btn");
    if (cambiarBtn && cambiarBtn.dataset.kind === "alimento") {
      this._startCambiarAlimento(
        cambiarBtn.dataset.subingesta,
        cambiarBtn.dataset.alimentoId,
        cambiarBtn.dataset.cantidad,
        cambiarBtn.dataset.franja
      );
      return;
    }
    if (cambiarBtn) {
      this._startCambiarPlato(cambiarBtn.dataset.subingesta, cambiarBtn.dataset.platoId, cambiarBtn.dataset.franja);
      return;
    }
    const cancelarBtn = ev.target.closest(".cancelar-btn");
    if (cancelarBtn) {
      this._changeState.delete(cancelarBtn.dataset.subingesta);
      this._safeRender();
      return;
    }
    const star = ev.target.closest(".rating-star");
    if (star) {
      const container = star.closest(".rating-stars");
      this._rateDish(container.dataset.superPlato, Number(star.dataset.value));
    }
  }

  _renderOptionRow(subingestaId, opt, expandedOptionId) {
    const optionId = opt.plato_id;
    const isOpen = expandedOptionId !== null && String(expandedOptionId) === String(optionId);
    const thumb =
      this._imgOk(opt.imagen)
        ? `<img class="meal-thumb" data-fallback-icon="mdi:food" src="${escapeHtml(opt.imagen)}" alt="" loading="lazy" />`
        : `<ha-icon class="meal-type" icon="mdi:food"></ha-icon>`;
    const kcal = opt.calorias ? `<div class="plato-summary-kcal">${escapeHtml(opt.calorias)} kcal</div>` : "";

    let detail = "";
    if (isOpen) {
      const alergenos = allergenChips(opt.alergenos);
      const receta = opt.receta
        ? `<div class="meal-receta">${escapeHtml(opt.receta)}</div>`
        : '<div class="empty">No requiere preparación</div>';
      const ingredientes =
        opt.ingredientes && opt.ingredientes.length
          ? `<ul class="meal-ingredientes">${opt.ingredientes
              .map((ing) => {
                const cantidad = ing.cantidad ? `${escapeHtml(ing.cantidad)}g ` : "";
                const medida = ing.medida_casera ? ` (${escapeHtml(ing.medida_casera)})` : "";
                return `<li>${cantidad}${escapeHtml(ing.nombre || "")}${medida}</li>`;
              })
              .join("")}</ul>`
          : "";
      // Belt-and-suspenders: the backend already drops options with no
      // usable id (one slipped through in a real response and produced an
      // empty "Aceptar" value, which failed cambiar_plato's validation
      // before it could even show a friendly error), but never render a
      // selectable button for one here either, just in case.
      const acceptBtn =
        optionId !== undefined && optionId !== null
          ? `<button class="cambiar-btn opcion-aceptar" data-subingesta="${escapeHtml(
              subingestaId
            )}" data-nuevo-id="${escapeHtml(optionId)}"><ha-icon icon="mdi:check"></ha-icon>Aceptar este plato</button>`
          : '<div class="action-status">Esta opción no se puede seleccionar (falta información).</div>';
      detail = `
        <div class="plato-detail">
          ${alergenos}
          ${receta}
          ${ingredientes}
          ${acceptBtn}
        </div>
      `;
    }

    return `
      <div class="plato-row">
        <div class="plato-summary opcion-summary" role="button" tabindex="0" aria-expanded="${isOpen}" data-subingesta="${escapeHtml(
          subingestaId
        )}" data-option-id="${escapeHtml(optionId)}">
          ${thumb}
          <div class="plato-summary-text">
            <div class="plato-summary-nombre">${escapeHtml(opt.nombre || "Sin especificar")}</div>
            ${kcal}
          </div>
          <ha-icon class="chevron small${isOpen ? " expanded" : ""}" icon="mdi:chevron-down"></ha-icon>
        </div>
        ${detail}
      </div>
    `;
  }

  _renderAlimentoActions(plato, franja, stateKey, state) {
    const sk = escapeHtml(stateKey);
    const attrs = `data-kind="alimento" data-subingesta="${sk}" data-alimento-id="${escapeHtml(plato.alimento_id)}" data-cantidad="${escapeHtml(
      plato.cantidad ?? ""
    )}" data-franja="${escapeHtml(franja)}"`;
    if (state.status === "loading") return '<div class="action-status">Buscando equivalentes…</div>';
    if (state.status === "applying") return '<div class="action-status">Aplicando cambio…</div>';
    if (state.status === "options") {
      const opts = state.options || [];
      if (!opts.length) {
        return `<div class="action-status">Sin alimentos equivalentes.</div><button class="action-btn cancelar-btn" data-subingesta="${sk}">Cerrar</button>`;
      }
      const rows = opts
        .map(
          (o) =>
            `<div class="alimento-row"><div class="plato-summary-text"><div class="plato-summary-nombre">${escapeHtml(
              o.nombre || "Sin nombre"
            )}</div><div class="plato-summary-kcal">${escapeHtml(o.cantidad)} g${o.medida_casera ? ` (${escapeHtml(o.medida_casera)})` : ""}</div></div><button class="action-btn alimento-pick" data-subingesta="${sk}" data-nuevo-id="${escapeHtml(
              o.alimento_id
            )}" data-nueva-cantidad="${escapeHtml(o.cantidad)}" data-nombre="${escapeHtml(o.nombre || "")}">Elegir</button></div>`
        )
        .join("");
      return `<div class="opciones-header">Equivalentes (${opts.length})</div><div class="opciones-plato">${rows}</div><button class="action-btn cancelar-btn" data-subingesta="${sk}">Cancelar</button>`;
    }
    if (state.status === "error") {
      return `<div class="action-status error">${escapeHtml(state.error)}</div><button class="cambiar-btn" ${attrs}><ha-icon icon="mdi:refresh"></ha-icon>Reintentar</button>`;
    }
    return `<button class="cambiar-btn" ${attrs}><ha-icon icon="mdi:swap-horizontal"></ha-icon>Cambiar alimento</button>`;
  }

  _renderPlatoActions(plato, franja) {
    if (this._readOnly) return "";
    if (plato.subingesta_id === undefined || plato.subingesta_id === null) return "";
    const isAlimento = plato.tipo === "alimento" && plato.alimento_id !== undefined && plato.alimento_id !== null;
    if (!isAlimento && (plato.plato_id === undefined || plato.plato_id === null)) {
      return `<div class="action-status">${plato.tipo === "suplemento" ? "Suplemento" : "Alimento"}: se cambia desde la app oficial.</div>`;
    }
    const stateKey = String(plato.subingesta_id);
    const state = this._changeState.get(stateKey) || { status: "idle" };
    if (isAlimento) return this._renderAlimentoActions(plato, franja, stateKey, state);
    const dataAttrs = `data-subingesta="${escapeHtml(stateKey)}" data-plato-id="${escapeHtml(
      plato.plato_id
    )}" data-franja="${escapeHtml(franja)}"`;

    if (state.status === "loading") {
      return '<div class="action-status">Buscando alternativas…</div>';
    }
    if (state.status === "applying") {
      return '<div class="action-status">Aplicando cambio…</div>';
    }
    if (state.status === "options") {
      const opts = state.options || [];
      if (!opts.length) {
        return `<div class="action-status">Sin alternativas disponibles.</div><button class="action-btn cancelar-btn" data-subingesta="${escapeHtml(
          stateKey
        )}">Cerrar</button>`;
      }
      const rows = opts.map((opt) => this._renderOptionRow(stateKey, opt, state.expandedOptionId)).join("");
      return `<div class="opciones-header">Elige un plato (${opts.length})</div><div class="opciones-plato">${rows}</div><button class="action-btn cancelar-btn" data-subingesta="${escapeHtml(
        stateKey
      )}">Cancelar</button>`;
    }
    if (state.status === "error") {
      return `<div class="action-status error">${escapeHtml(
        state.error
      )}</div><button class="cambiar-btn" ${dataAttrs}><ha-icon icon="mdi:refresh"></ha-icon>Reintentar</button>`;
    }
    return `<button class="cambiar-btn" ${dataAttrs}><ha-icon icon="mdi:swap-horizontal"></ha-icon>Cambiar plato</button>`;
  }

  _renderPlatoDetail(plato, franja) {
    const isLoose = !!plato.tipo && plato.tipo !== "plato";
    const img =
      this._imgOk(plato.imagen)
        ? `<img class="plato-img" src="${escapeHtml(plato.imagen)}" alt="${escapeHtml(plato.nombre || "")}" loading="lazy" />`
        : (this._config || {}).hide_images === true
        ? ""
        : '<div class="plato-img-placeholder"><ha-icon icon="mdi:food"></ha-icon></div>';
    const metaParts = [];
    if (plato.calorias) metaParts.push(`${plato.calorias_estimadas ? "~" : ""}${escapeHtml(plato.calorias)} kcal`);
    if (plato.comensales) metaParts.push(`${escapeHtml(plato.comensales)} ${Number(plato.comensales) === 1 ? "comensal" : "comensales"}`);
    if (plato.duracion_min) metaParts.push(`${escapeHtml(plato.duracion_min)} min`);
    const meta = metaParts.length ? `<div class="meal-meta">${metaParts.join(" · ")}</div>` : "";

    const ratingValue = Math.round(Number(plato.rating) || 0);
    const starsHtml = plato.super_plato_id
      ? `<div class="rating-stars" data-super-plato="${escapeHtml(plato.super_plato_id)}">${[1, 2, 3, 4, 5]
          .map(
            (n) =>
              `<ha-icon class="rating-star" data-value="${n}" icon="${
                n <= ratingValue ? "mdi:star" : "mdi:star-outline"
              }"></ha-icon>`
          )
          .join("")}</div>`
      : "";

    const alergenos = allergenChips(plato.alergenos);

    const receta = plato.receta
      ? `<div class="meal-receta">${escapeHtml(plato.receta)}</div>`
      : '<div class="empty">No requiere preparación</div>';

    const ingredientes =
      plato.ingredientes && plato.ingredientes.length
        ? `<ul class="meal-ingredientes">${plato.ingredientes
            .map((ing) => {
              const cantidad = ing.cantidad ? `${escapeHtml(ing.cantidad)}g ` : "";
              const medida = ing.medida_casera ? ` (${escapeHtml(ing.medida_casera)})` : "";
              return `<li>${cantidad}${escapeHtml(ing.nombre || "")}${medida}</li>`;
            })
            .join("")}</ul>`
        : "";

    return `
      <div class="plato-detail">
        ${isLoose ? "" : img}
        ${meta}
        ${nutrientGrid(plato.nutrientes)}
        ${starsHtml}
        ${alergenos}
        ${isLoose ? "" : receta}
        ${ingredientes}
        <div class="plato-actions">${this._renderPlatoActions(plato, franja)}</div>
      </div>
    `;
  }

  _renderPlatoRow(plato, franja) {
    const canExpand = plato.subingesta_id !== undefined && plato.subingesta_id !== null;
    const key = canExpand ? String(plato.subingesta_id) : undefined;
    const isOpen = canExpand && this._platoExpanded.has(key);
    const thumbSrc = plato.imagen;
    const thumbHtml =
      this._imgOk(thumbSrc)
        ? `<img class="meal-thumb" data-fallback-icon="mdi:food" src="${escapeHtml(thumbSrc)}" alt="" loading="lazy" />`
        : `<ha-icon class="meal-type" icon="mdi:food"></ha-icon>`;
    const kcal = plato.calorias
      ? `<div class="plato-summary-kcal" ${plato.calorias_estimadas ? 'title="Estimación a partir de la composición del alimento"' : ""}>${plato.calorias_estimadas ? "~" : ""}${escapeHtml(plato.calorias)} kcal</div>`
      : "";

    return `
      <div class="plato-row">
        <div class="plato-summary" data-plato-toggle="${escapeHtml(key)}"${canExpand ? ` role="button" tabindex="0" aria-expanded="${isOpen}"` : ""}>
          ${thumbHtml}
          <div class="plato-summary-text">
            <div class="plato-summary-nombre">${escapeHtml(plato.nombre || "Sin especificar")}</div>
            ${kcal}${Number(plato.rating) > 0 ? `<span class="stars-mini" title="Tu valoración">★ ${escapeHtml(Math.round(Number(plato.rating) * 10) / 10)}</span>` : ""}
          </div>
          ${canExpand ? `<ha-icon class="chevron small${isOpen ? " expanded" : ""}" icon="mdi:chevron-down"></ha-icon>` : ""}
        </div>
        ${isOpen ? this._renderPlatoDetail(plato, franja) : ""}
      </div>
    `;
  }

  _renderMeal(meal, labelsOverride, ctx = {}) {
    const franja = meal.franja;
    const isOpen = this._expanded.has(franja);
    const icon = MEAL_ICONS[franja] || "mdi:food";
    const label = (labelsOverride && labelsOverride[franja]) || MEAL_LABELS[franja] || franja;
    const platos = meal.platos || [];
    const summary = platos.length
      ? platos.map((p) => p.nombre || "Sin especificar").join(" + ")
      : "Sin especificar";
    const thumbSrc = platos.find((p) => p.imagen)?.imagen;
    const thumbHtml =
      this._imgOk(thumbSrc)
        ? `<img class="meal-thumb" data-fallback-icon="${icon}" src="${escapeHtml(thumbSrc)}" alt="" loading="lazy" />`
        : `<ha-icon class="meal-type" icon="${icon}"></ha-icon>`;

    let bodyHtml = "";
    if (isOpen) {
      bodyHtml = `<div class="meal-body">${platos.map((p) => this._renderPlatoRow(p, franja)).join("")}</div>`;
    }

    const isNext = ctx.nextFranja === franja;
    const isEaten = ctx.eatenEnabled && this._eatenSet().has(franja);
    const isPast = ctx.pastFranjas && ctx.pastFranjas.has(franja) && !isNext;
    const eatenHtml = ctx.eatenEnabled
      ? `<ha-icon class="eaten-toggle${isEaten ? " done" : ""}" data-franja="${escapeHtml(franja)}" role="checkbox" tabindex="0" aria-checked="${!!isEaten}" aria-label="Marcar como tomada" icon="${
          isEaten ? "mdi:check-circle" : "mdi:circle-outline"
        }" title="Marcar como tomada"></ha-icon>`
      : "";

    return `
      <div class="meal-card${isNext ? " next" : ""}${isPast || isEaten ? " past" : ""}">
        <div class="meal-header${isOpen ? " expanded" : ""}" data-franja="${escapeHtml(franja)}" role="button" tabindex="0" aria-expanded="${isOpen}">
          ${thumbHtml}
          <span class="franja">${label}${meal.hora ? ` · ${escapeHtml(meal.hora)}` : ""}</span>
          ${isNext ? '<span class="next-tag">Siguiente</span>' : ""}
          <span class="plato">${escapeHtml(summary)}</span>
          ${mealKcal(meal) ? `<span class="kcal-pill">${Math.round(mealKcal(meal))} kcal</span>` : ""}
          ${eatenHtml}
          <ha-icon class="chevron" icon="mdi:chevron-down"></ha-icon>
        </div>
        ${bodyHtml}
      </div>
    `;
  }

  _render() {
    if (!this._hass) return;
    this._ensureDom();
    const hass = this._hass;
    const cfg = this._config || {};

    const planEntity = findEntity(hass, cfg.plan_entity, "_plan_actual");
    const mealsEntity = findEntity(hass, cfg.meals_entity, "_comidas_hoy");
    const apptEntity = findEntity(hass, cfg.appointment_entity, "_proxima_cita");
    const trackingEntity = findEntity(hass, cfg.tracking_entity, "_seguimientos");
    const dietistaEntity = findEntity(hass, cfg.dietista_entity, "_dietista");

    const card = this.shadowRoot.querySelector("ha-card");
    if (!planEntity && !mealsEntity && !apptEntity && !trackingEntity) {
      card.innerHTML =
        '<div class="unavailable">No se han encontrado entidades de Nutriplan Bridge.<br>Configura primero la integración en Ajustes → Dispositivos y servicios, o indica las entidades manualmente editando esta tarjeta.</div>';
      return;
    }

    const mealsAttrs = (mealsEntity && mealsEntity.attributes) || {};
    if (cfg.accent_color) card.style.setProperty("--primary-color", cfg.accent_color);
    else card.style.removeProperty("--primary-color");
    card.classList.toggle("compact", cfg.compact === true);
    this._eatenEntity = findEntity(hass, cfg.eaten_entity, "_comidas_tomadas_hoy");
    const planAttrs = (planEntity && planEntity.attributes) || {};
    const chatEntity = findEntity(hass, cfg.chat_entity, "_mensajes_sin_leer");
    const todayIndex = mealsAttrs.dieta_index;
    const numDietas = Number(planAttrs.num_dietas || mealsAttrs.num_dietas || 0);
    const planes = Array.isArray(planAttrs.planes) ? planAttrs.planes : [];
    const currentPlanId = mealsAttrs.plan_id ?? planAttrs.plan_id;
    const viewingCurrent = this._view.planId === null;
    const day = this._view.day ?? todayIndex ?? 0;
    this._numDietas = numDietas;
    this._readOnly = !viewingCurrent;
    this._planId = viewingCurrent ? currentPlanId : this._view.planId;
    this._dietaIndex = day;
    // Needed so the actions below know WHICH configured account a plato
    // belongs to - without it, calling them with more than one Nutriplan
    // Bridge account configured fails ("Multiple accounts are configured;
    // pass config_entry_id to pick one"). Prefer the sensor's own attribute
    // (works on any HA version); fall back to the frontend's entity
    // registry (hass.entities, only on newer HA) in case that attribute
    // isn't there yet - e.g. the integration was updated but not restarted.
    this._configEntryId =
      mealsAttrs.config_entry_id || hass.entities?.[mealsEntity?.entity_id]?.config_entry_id;

    const dietistaAttrs = (dietistaEntity && dietistaEntity.attributes) || {};
    const dietistaName =
      dietistaEntity && !["unknown", "unavailable"].includes(dietistaEntity.state)
        ? dietistaEntity.state
        : "Nutriplan Bridge";
    const dietistaAvatar = dietistaAttrs.avatar;
    const mealLabels = dietistaAttrs.nombres_franjas || {};

    const planName =
      planEntity && !["unknown", "unavailable"].includes(planEntity.state) ? planEntity.state : "Sin plan activo";

    let meals = [];
    let resumen = null;
    let mealsMessage = null;
    if (mealsEntity && mealsEntity.state === "unavailable" && viewingCurrent) {
      mealsMessage = "Sin conexión con DietoPro: datos no disponibles";
    } else if (viewingCurrent && day === todayIndex) {
      meals = mealsAttrs.comidas || [];
      resumen = mealsAttrs.resumen_nutricional;
    } else {
      const key = this._viewKey();
      const week = this._weekCache.get(key);
      if (week) {
        const d = (week.dias || [])[day];
        meals = d ? d.comidas || [] : [];
        resumen = d && d.resumen_nutricional;
      } else if (this._weekError.has(key)) {
        mealsMessage = this._weekError.get(key);
      } else {
        mealsMessage = "Cargando…";
        if (!this._weekLoading.has(key)) this._loadWeek(key, this._view.planId);
      }
    }

    const dayName = (i) => (numDietas === 7 ? WEEKDAYS[i] : `Día ${i + 1}`);
    const isToday = viewingCurrent && day === todayIndex;
    const viewedPlan = planes.find((p) => String(p.id) === String(this._planId));
    const sectionTitle = isToday
      ? "Comidas de hoy"
      : `${dayName(day)}${viewingCurrent ? "" : ` · ${planLabel(viewedPlan)}`}`;

    let mealsHtml;
    if (mealsMessage) {
      mealsHtml =
        mealsMessage === "Cargando…"
          ? '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>'
          : emptyState(/conexi|error|No se/i.test(mealsMessage) ? "mdi:cloud-off-outline" : "mdi:information-outline", mealsMessage);
    } else if (meals.length) {
      const ctx = { eatenEnabled: isToday && cfg.hide_eaten !== true };
      if (isToday) {
        const now = new Date();
        const nowMin = now.getHours() * 60 + now.getMinutes();
        const timed = meals.map((m) => [m, minutesOf(m.hora)]).filter(([, t]) => t !== null);
        const upcoming = timed.filter(([, t]) => t + 30 >= nowMin).sort((a, b) => a[1] - b[1])[0];
        ctx.nextFranja = upcoming ? upcoming[0].franja : null;
        ctx.pastFranjas = new Set(timed.filter(([, t]) => t + 30 < nowMin).map(([m]) => m.franja));
      }
      mealsHtml = `<div class="meals">${meals.map((m) => this._renderMeal(m, mealLabels, ctx)).join("")}</div>`;
      if (ctx.eatenEnabled) {
        const total = Math.round((resumen && resumen.energia_kcal) || meals.reduce((a, m) => a + mealKcal(m), 0));
        const eatenKcal = Math.round(meals.filter((m) => this._eatenSet().has(m.franja)).reduce((a, m) => a + mealKcal(m), 0));
        if (total) {
          mealsHtml =
            `<div class="progress"><div style="width:${Math.min(100, (eatenKcal / total) * 100).toFixed(1)}%"></div></div>` +
            `<div class="progress-label">${eatenKcal} / ${total} kcal tomadas hoy${
              this._eatenEntity && this._eatenEntity.attributes && this._eatenEntity.attributes.cumplimiento_semana != null
                ? ` · semana: ${this._eatenEntity.attributes.cumplimiento_semana}% (${this._eatenEntity.attributes.tomadas_semana}/${this._eatenEntity.attributes.planificadas_semana})`
                : ""
            }</div>` +
            mealsHtml;
        }
      }
    } else {
      mealsHtml = emptyState("mdi:food-off-outline", `Sin comidas programadas ${isToday ? "para hoy" : "este día"}`);
    }

    const eniEntity = findEntity(hass, cfg.eni_entity, "_estado_eni");
    const eniAttrs = (eniEntity && eniEntity.attributes) || {};
    const targetKcal = Array.isArray(eniAttrs.valor_energetico_por_dia) ? Number(eniAttrs.valor_energetico_por_dia[day]) || null : null;
    const targetMacros = Array.isArray(eniAttrs.porcentaje_macros_por_dia) ? eniAttrs.porcentaje_macros_por_dia[day] : null;
    let summaryHtml = "";
    if (resumen && resumen.energia_kcal) {
      const parts = [`<span><b>${escapeHtml(Math.round(resumen.energia_kcal))}</b> kcal</span>`];
      if (targetKcal) parts.push(`<span>objetivo <b>${escapeHtml(Math.round(targetKcal))}</b></span>`);
      [["proteinas_g", "Prot"], ["grasas_g", "Grasa"], ["carbohidratos_g", "Hidr"]].forEach(([k, l]) => {
        if (resumen[k] !== undefined && resumen[k] !== null) {
          parts.push(`<span>${l} <b>${escapeHtml(Math.round(resumen[k]))}</b> g</span>`);
        }
      });
      const week = this._weekCache.get(this._viewKey());
      if (week && week.promedio_kcal && !isToday) {
        parts.push(`<span>Media semana <b>${escapeHtml(week.promedio_kcal)}</b> kcal</span>`);
      }
      summaryHtml = `<div class="day-summary">${parts.join("")}</div>`;
      if (cfg.hide_macros !== true) {
        summaryHtml += macroBar(resumen);
        if (targetMacros && typeof targetMacros === "object") {
          const t = (v) => (Number.isFinite(Number(v)) ? Math.round(Number(v)) : "?");
          summaryHtml += `<div class="macro-legend">Objetivo: proteínas ${t(targetMacros.proteinas)}% · hidratos ${t(targetMacros.glucidos)}% · grasas ${t(targetMacros.grasas)}%</div>`;
        }
      }
    }

    let weekHtml = "";
    if (this._weekView) {
      const key = this._viewKey();
      const week = this._weekCache.get(key);
      if (week) {
        const labelOf = (franja) => mealLabels[franja] || MEAL_LABELS[franja] || franja;
        weekHtml = (week.dias || [])
          .map((d, i) => {
            const kcal = d.resumen_nutricional && d.resumen_nutricional.energia_kcal;
            const lines = (d.comidas || [])
              .map(
                (m) =>
                  `<div class="week-meal"><b>${escapeHtml(labelOf(m.franja))}</b> ${escapeHtml(
                    (m.platos || []).map((p) => p.nombre || "Sin especificar").join(" + ")
                  )}</div>`
              )
              .join("");
            const isTodayRow = viewingCurrent && i === todayIndex;
            return `<div class="week-day${i === day ? " selected" : ""}" data-day="${i}" role="button" tabindex="0"><div class="week-day-head"><span>${escapeHtml(
              dayName(i)
            )}${isTodayRow ? " <small>hoy</small>" : ""}</span><span>${kcal ? Math.round(kcal) + " kcal" : ""}</span></div>${lines}</div>`;
          })
          .join("");
      } else if (this._weekError.has(key)) {
        weekHtml = `<div class="empty">${escapeHtml(this._weekError.get(key))}</div>`;
      } else {
        weekHtml = '<div class="empty">Cargando…</div>';
        if (!this._weekLoading.has(key)) this._loadWeek(key, this._view.planId);
      }
    }

    let daysHtml = "";
    if (numDietas > 1) {
      const chips = [];
      for (let i = 0; i < numDietas; i++) {
        const label = numDietas === 7 ? WEEKDAYS_SHORT[i] : `D${i + 1}`;
        chips.push(
          `<button class="day-chip${i === day ? " selected" : ""}${viewingCurrent && i === todayIndex ? " today" : ""}" data-day="${i}" title="${escapeHtml(dayName(i))}" aria-label="${escapeHtml(dayName(i))}" aria-pressed="${i === day}">${label}</button>`
        );
      }
      daysHtml = `<div class="days">${chips.join("")}</div>`;
    }

    let planNavHtml = "";
    if (planes.length > 1) {
      const idx = planes.findIndex((p) => String(p.id) === String(this._planId));
      const older = idx >= 0 && idx < planes.length - 1 ? planes[idx + 1] : null;
      const newer = idx > 0 ? planes[idx - 1] : null;
      const newerTarget = newer ? (String(newer.id) === String(currentPlanId) ? "" : newer.id) : null;
      planNavHtml = `
        <div class="plan-nav">
          <button class="nav-btn" ${older ? `data-plan-id="${escapeHtml(older.id)}"` : "disabled"} title="Plan anterior" aria-label="Plan anterior"><ha-icon icon="mdi:chevron-left"></ha-icon></button>
          <div class="plan-name">${escapeHtml(planLabel(viewedPlan))} <small>${viewingCurrent ? "(actual)" : `· ${escapeHtml(formatShortDate(viewedPlan && (viewedPlan.inicio || viewedPlan.enviado || viewedPlan.creado)))}`}</small></div>
          <button class="nav-btn" ${newer ? `data-plan-id="${escapeHtml(newerTarget)}"` : "disabled"} title="Plan siguiente" aria-label="Plan siguiente"><ha-icon icon="mdi:chevron-right"></ha-icon></button>
        </div>
        ${viewingCurrent ? "" : '<div class="readonly-note">Estás viendo otro plan: solo lectura. <button class="link-btn">Volver al plan actual</button></div>'}
      `;
    }

    const apptAttrs = (apptEntity && apptEntity.attributes) || {};
    let apptHtml = emptyState("mdi:calendar-blank-outline", "Sin próxima cita");
    if (apptEntity && apptEntity.state && !["unknown", "unavailable"].includes(apptEntity.state)) {
      const label =
        apptEntity.state === "programada" ? "Cita programada" : formatDate(apptEntity.state);
      const rel = apptEntity.state === "programada" ? "" : relativeDay(apptEntity.state);
      apptHtml = `<div class="appointment"><ha-icon icon="mdi:calendar-clock"></ha-icon><span>${escapeHtml(
        label
      )}${rel ? ` <small>(${escapeHtml(rel)})</small>` : ""}</span></div>`;
      const bits = [];
      if (apptAttrs.duracion_min) bits.push(`${apptAttrs.duracion_min} min`);
      if (apptAttrs.confirmada_por_dietista === true) bits.push("confirmada por tu dietista");
      else if (apptAttrs.pendiente_confirmacion_dietista === true || apptAttrs.pedida_por_paciente === true) bits.push("pendiente de que la confirme tu dietista");
      if (apptAttrs.requiere_confirmacion_paciente === true && apptAttrs.confirmada_por_paciente === false) bits.push("falta tu confirmación");
      if (apptAttrs.observaciones) bits.push(String(apptAttrs.observaciones));
      if (bits.length) apptHtml += `<div class="appt-meta">${escapeHtml(bits.join(" · "))}</div>`;
    }

    let apptActionsHtml = "";
    // The real response says what the account allows: booking can be switched
    // off, cancellation can be "disabled", confirmation may not be required.
    // Only offer actions that can actually work (unknown = still offered).
    const canBook = apptAttrs.reserva_habilitada !== false;
    const canCancel = apptAttrs.cancelacion_habilitada !== false;
    const needsConfirm = apptAttrs.requiere_confirmacion_paciente === true && apptAttrs.confirmada_por_paciente === false;
    if (cfg.hide_booking !== true && (canBook || canCancel || needsConfirm)) {
      const bk = this._book;
      const citaId = apptAttrs.cita_id ?? null;
      let panel = "";
      if (bk.status === "loading") {
        panel = '<div class="action-status">Buscando huecos libres…</div>';
      } else if (bk.status === "sending") {
        panel = '<div class="action-status">Enviando solicitud…</div>';
      } else if (bk.status === "ready") {
        const byDay = new Map();
        (bk.slots || []).forEach((sl) => {
          if (!byDay.has(sl.fecha)) byDay.set(sl.fecha, []);
          byDay.get(sl.fecha).push(sl);
        });
        const dayLabel = (f) => {
          const d = new Date(f);
          return isNaN(d.getTime()) ? String(f) : d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
        };
        const days = [...byDay.entries()]
          .map(
            ([f, sl]) =>
              `<div class="slot-day">${escapeHtml(dayLabel(f))}</div>` +
              sl
                .map(
                  (x) =>
                    `<button class="slot-btn${bk.selected && bk.selected.fecha === x.fecha && String(bk.selected.hora) === String(x.hora) ? " selected" : ""}" data-fecha="${escapeHtml(x.fecha)}" data-hora="${escapeHtml(x.hora)}">${escapeHtml(x.hora)}</button>`
                )
                .join("")
          )
          .join("");
        panel = `${days || '<div class="empty">No hay huecos libres en los próximos días.</div>'}
          ${
            bk.selected
              ? `<textarea class="book-note" rows="2" placeholder="Nota para el dietista (opcional)">${escapeHtml(bk.note)}</textarea><button class="cambiar-btn book-send">Pedir cita ${escapeHtml(bk.selected.hora)}</button>`
              : ""
          }
          <button class="action-btn book-close">Cerrar</button>
          ${bk.msg ? `<div class="action-status error">${escapeHtml(bk.msg)}</div>` : ""}`;
      } else {
        panel = `${canBook ? '<button class="action-btn book-open">Pedir cita</button>' : ""}${
          citaId && needsConfirm ? `<button class="action-btn cita-confirm" data-cita-id="${escapeHtml(citaId)}">Confirmar cita</button>` : ""
        }${citaId && canCancel ? `<button class="action-btn cita-cancel" data-cita-id="${escapeHtml(citaId)}">Cancelar cita</button>` : ""}${bk.msg ? `<div class="action-status${bk.status === "error" ? " error" : ""}">${escapeHtml(bk.msg)}</div>` : ""}`;
      }
      apptActionsHtml = `<div class="appt-actions">${panel}</div>`;
    }

    let metricsHtml = "";
    if (trackingEntity) {
      const a = trackingEntity.attributes || {};
      const metrics = [
        ["ultimo_peso", "Peso (kg)", "delta_peso"],
        ["ultimo_imc", "IMC", "delta_imc"],
        ["ultimo_peso_grasa", "Grasa (kg)", "delta_peso_grasa"],
        ["ultimo_porcentaje_grasa", "Grasa (%)", "delta_porcentaje_grasa"],
        ["ultimo_perimetro_cintura", "Cintura (cm)", null],
      ].filter(([key]) => a[key] !== undefined && a[key] !== null);
      if (metrics.length) {
        metricsHtml = `<div class="metrics">${metrics
          .map(([key, label, deltaKey]) => {
            const d = deltaKey ? a[deltaKey] : null;
            const arrow =
              typeof d === "number" && d !== 0
                ? ` <small title="Variación respecto a la medición anterior" style="color:${d < 0 ? "#43a047" : "#fb8c00"}">${d < 0 ? "▼" : "▲"}${Math.abs(d)}</small>`
                : "";
            return `<div class="metric"><span class="value">${escapeHtml(a[key])}${arrow}</span><span class="label">${label}</span></div>`;
          })
          .join("")}</div>`;
        const pesos = (Array.isArray(a.historial) ? a.historial : [])
          .map((h) => Number(h.peso))
          .filter((v) => !isNaN(v) && v > 0)
          .slice(-12);
        const delta = a.delta_peso;
        const deltaHtml =
          typeof delta === "number" && delta !== 0
            ? `${delta < 0 ? "▼" : "▲"} ${Math.abs(delta)} kg desde la anterior`
            : "";
        if (pesos.length >= 2) {
          metricsHtml += `<div class="trend" title="Toca para ampliar">${sparkline(pesos)}<span class="delta">${deltaHtml}</span></div>`;
          if (this._chartOpen) metricsHtml += bigChart(Array.isArray(a.historial) ? a.historial : []);
        } else if (deltaHtml) {
          metricsHtml += `<div class="trend"><span class="delta">${deltaHtml}</span></div>`;
        }
      } else {
        metricsHtml = '<div class="empty">Sin seguimientos registrados</div>';
      }
    }

    const unread = chatEntity ? Number(chatEntity.state) || 0 : 0;

    let measHtml = "";
    if (cfg.show_measurement === true) {
      const m = this._meas;
      const fields = [
        ["peso", "Peso (kg)"],
        ["porcentaje_grasa", "Grasa (%)"],
        ["perimetro_cintura", "Cintura (cm)"],
        ["perimetro_cadera", "Cadera (cm)"],
      ];
      if (m.status === "loading") measHtml = '<div class="action-status">Comprobando si hay una medición abierta…</div>';
      else if (m.status === "sending") measHtml = '<div class="action-status">Enviando…</div>';
      else if (m.status === "form") {
        measHtml = `<div class="meas-grid">${fields
          .map(
            ([k, label]) =>
              `<label>${label}<input class="meas-input" data-field="${k}" type="text" inputmode="decimal" autocomplete="off" placeholder="0,0" value="${escapeHtml(m.values[k] ?? "")}" /></label>`
          )
          .join("")}</div><button class="cambiar-btn meas-send">Enviar y cerrar</button><button class="action-btn meas-close">Cancelar</button>${
          m.msg ? `<div class="action-status error">${escapeHtml(m.msg)}</div>` : ""
        }`;
      } else {
        measHtml = `<button class="action-btn meas-open"><ha-icon icon="mdi:scale-bathroom"></ha-icon> Registrar medición</button>${
          m.msg ? `<div class="action-status${m.status === "none" ? " error" : ""}">${escapeHtml(m.msg)}</div>` : ""
        }`;
      }
      measHtml = `<div class="appt-actions">${measHtml}</div>`;
    }

    let contactHtml = "";
    if (cfg.hide_contact !== true) {
      if (dietistaAttrs.telefono) {
        contactHtml += `<a class="contact" href="tel:${escapeHtml(dietistaAttrs.telefono)}" title="Llamar al dietista"><ha-icon icon="mdi:phone"></ha-icon></a>`;
      }
      if (dietistaAttrs.email) {
        contactHtml += `<a class="contact" href="mailto:${escapeHtml(dietistaAttrs.email)}" title="Escribir al dietista"><ha-icon icon="mdi:email-outline"></ha-icon></a>`;
      }
    }

    let chatHtml = "";
    if (this._chatOpen) {
      const raw = chatEntity && chatEntity.attributes && chatEntity.attributes.mensajes;
      const list = Array.isArray(raw) ? raw : raw && Array.isArray(raw.messages) ? raw.messages : [];
      const msgs = list
        .slice(-5)
        .map((m) => {
          const text = pick(m, ["message", "text", "content", "body", "mensaje", "texto"]);
          if (!text) return "";
          const when = pick(m, ["createdAt", "created_at", "created", "date", "fecha"]);
          return `<div class="chat-msg">${escapeHtml(text.slice(0, 280))}${when ? `<small>${escapeHtml(formatDate(when))}</small>` : ""}</div>`;
        })
        .join("");
      const compose =
        cfg.hide_chat_compose === true
          ? ""
          : `<div class="chat-compose"><textarea class="chat-input" rows="2" placeholder="Escribe a tu dietista…">${escapeHtml(
              this._chatDraft
            )}</textarea><button class="action-btn chat-send">Enviar</button></div><label class="action-btn chat-attach"><ha-icon icon="mdi:paperclip"></ha-icon>Adjuntar foto o documento<input type="file" class="chat-file" hidden accept="image/*,application/pdf,.doc,.docx,.xls,.xlsx,.txt" /></label>${
              this._chatStatus ? `<div class="action-status">${escapeHtml(this._chatStatus)}</div>` : ""
            }`;
      chatHtml = `<div class="chat-panel">${
        msgs || (unread > 0 ? `<div class="chat-msg">Tienes ${unread} mensaje(s) sin leer del dietista.</div>` : '<div class="chat-msg">Sin mensajes recientes.</div>')
      }${unread > 0 ? '<button class="action-btn chat-read">Marcar como leído</button>' : ""}${compose}</div>`;
    }

    let charlasHtml = "";
    const charlasEntity = findEntity(hass, cfg.charlas_entity, "_charlas");
    const charlas = (charlasEntity && charlasEntity.attributes && charlasEntity.attributes.charlas) || [];
    if (cfg.hide_charlas !== true && Array.isArray(charlas) && charlas.length) {
      const recent = [...charlas]
        .sort((a, b) => String(b.fecha || "").localeCompare(String(a.fecha || "")))
        .slice(0, 3);
      charlasHtml = `<div class="section"><div class="section-title"><ha-icon icon="mdi:school-outline"></ha-icon>Charlas</div>${recent
        .map(
          (c) =>
            `<div class="charla">${c.visto === false ? '<span class="dot" title="Sin ver"></span>' : ""}<div class="charla-text"><div class="charla-title">${escapeHtml(
              c.titulo || c.tema || "Charla"
            )}</div><div class="charla-sub">${escapeHtml([c.tema && c.titulo ? c.tema : "", c.fecha ? formatShortDate(c.fecha) : ""].filter(Boolean).join(" · "))}</div></div>${
              c.enlace
                ? `<a class="charla-link" href="${escapeHtml(c.enlace)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(c.enlace_nombre || "Abrir enlace")}"><ha-icon icon="mdi:open-in-new"></ha-icon></a>`
                : ""
            }</div>`
        )
        .join("")}</div>`;
    }

    let shopHtml = "";
    if (cfg.hide_shopping !== true) {
      const sh = this._shop;
      const scopeBtns = `<div class="shop-scope"><button class="action-btn shop-load" data-scope="week">Todo el plan</button>${
        numDietas > 1 ? `<button class="action-btn shop-load" data-scope="day">Solo ${escapeHtml(dayName(day))}</button>` : ""
      }</div>`;
      let body;
      if (sh.status === "loading") {
        body = '<div class="action-status">Calculando la lista…</div>';
      } else if (sh.status === "error") {
        body = `${scopeBtns}<div class="action-status error">${escapeHtml(sh.error)}</div>`;
      } else if (sh.status === "ready") {
        const checked = loadChecked(this._shopStorageKey());
        const groups = new Map();
        (sh.items || []).forEach((it) => {
          const g = it.categoria || it.grupo || "Otros";
          if (!groups.has(g)) groups.set(g, []);
          groups.get(g).push(it);
        });
        const list = [...groups.entries()]
          .map(
            ([g, items]) =>
              `<div class="shop-group">${escapeHtml(g)}</div>` +
              items
                .map(
                  (it) =>
                    `<label class="shop-item${checked.has(it.nombre) ? " checked" : ""}"><input type="checkbox" class="shop-check" data-name="${escapeHtml(it.nombre)}" ${checked.has(it.nombre) ? "checked" : ""}><span class="shop-name">${escapeHtml(it.nombre)}</span><span class="shop-qty">${it.cantidad_g ? escapeHtml(it.cantidad_g) + " g" : ""}</span></label>`
                )
                .join("")
          )
          .join("");
        body = `${scopeBtns}${list || '<div class="empty">Este plan no tiene ingredientes.</div>'}
          ${cfg.todo_entity ? '<button class="action-btn shop-todo">Añadir a la lista de Home Assistant</button>' : ""}
          <button class="action-btn shop-copy">Copiar lista</button>
          <button class="action-btn shop-close">Cerrar</button>
          ${sh.todoMsg ? `<div class="action-status">${escapeHtml(sh.todoMsg)}</div>` : ""}`;
      } else {
        body = scopeBtns;
      }
      shopHtml = `<div class="section"><div class="section-title"><ha-icon icon="mdi:cart-outline"></ha-icon>Lista de la compra</div>${body}</div>`;
    }

    const avatarHtml =
      dietistaAvatar && !this._brokenImages.has(dietistaAvatar)
        ? `<img class="avatar" src="${escapeHtml(dietistaAvatar)}" alt="${escapeHtml(dietistaName)}" />`
        : `<ha-icon icon="mdi:food-apple"></ha-icon>`;

    card.innerHTML = `
      <div class="header">
        ${avatarHtml}
        <div class="header-text">
          <div class="title">${escapeHtml(cfg.title || planName)}</div>
          <div class="subtitle">${escapeHtml(dietistaName)}</div>
        </div>
        <div class="head-actions">
          ${contactHtml}
          ${
            unread > 0
              ? `<button class="badge chat-toggle" title="Mensajes del dietista"><ha-icon icon="mdi:chat-processing"></ha-icon>${unread}</button>`
              : cfg.hide_chat === true
              ? ""
              : `<a class="contact chat-toggle" title="Mensajes del dietista" style="cursor:pointer"><ha-icon icon="mdi:chat-outline"></ha-icon></a>`
          }
        </div>
      </div>
      ${chatHtml}

      <div class="section">
        ${planNavHtml}
        <div class="section-head">
          <div class="section-title"><ha-icon icon="${this._weekView ? "mdi:calendar-week" : "mdi:silverware-fork-knife"}"></ha-icon>${escapeHtml(this._weekView ? "Semana completa" : sectionTitle)}</div>
          <span>
            ${viewingCurrent && !isToday && todayIndex !== undefined && !this._weekView ? '<button class="link-btn">Volver a hoy</button>' : ""}
            ${numDietas > 1 ? `<button class="link-btn week-toggle">${this._weekView ? "Vista día" : "Vista semana"}</button>` : ""}
          </span>
        </div>
        ${this._weekView ? weekHtml : `${daysHtml}${summaryHtml}${mealsHtml}`}
      </div>

      ${
        cfg.hide_appointment === true
          ? ""
          : `<div class="section">
        <div class="section-title"><ha-icon icon="mdi:calendar-clock"></ha-icon>Próxima cita</div>
        ${apptHtml}
        ${apptActionsHtml}
      </div>`
      }

      ${shopHtml}

      ${charlasHtml}

      ${
        trackingEntity && cfg.hide_tracking !== true
          ? `<div class="section">
              <div class="section-title"><ha-icon icon="mdi:chart-line"></ha-icon>Último seguimiento</div>
              ${metricsHtml}
              ${measHtml}
            </div>`
          : ""
      }
    `;
  }
}

// Guarded: if this module is ever evaluated more than once in the same page
// (duplicate resource registration, a stale + fresh copy both injected,
// dev-tools re-injection, etc.) a second bare customElements.define() call
// throws "has already been used with this registry" - and since that throw
// happens at the TOP LEVEL of the module (not inside any try/catch), it
// would silently abort the rest of the script, including the
// window.customCards.push() below, which is exactly why the card could
// disappear entirely from the "Add card" picker instead of showing an error.
if (!customElements.get("nutriplan-bridge-card")) {
  customElements.define("nutriplan-bridge-card", NutriplanBridgeCard);
}

/* Visual (GUI) editor: shown when the user clicks "Edit" on the card in the
 * dashboard. Everything is optional - entities are auto-detected; the entity
 * pickers are only useful with more than one Nutriplan Bridge account. */
const EDITOR_SCHEMA = [
  { name: "title", label: "Título (opcional)", selector: { text: {} } },
  { name: "hide_appointment", label: "Ocultar próxima cita", selector: { boolean: {} } },
  { name: "hide_tracking", label: "Ocultar último seguimiento", selector: { boolean: {} } },
  { name: "hide_shopping", label: "Ocultar lista de la compra", selector: { boolean: {} } },
  { name: "hide_macros", label: "Ocultar reparto de macros", selector: { boolean: {} } },
  { name: "hide_eaten", label: "Ocultar marcar comidas como tomadas", selector: { boolean: {} } },
  { name: "compact", label: "Modo compacto (menos espacio)", selector: { boolean: {} } },
  { name: "accent_color", label: "Color de acento (p. ej. #43a047, opcional)", selector: { text: {} } },
  { name: "hide_contact", label: "Ocultar botones de llamar/escribir al dietista", selector: { boolean: {} } },
  { name: "hide_chat", label: "Ocultar el icono de mensajes (si no hay mensajes sin leer)", selector: { boolean: {} } },
  { name: "hide_chat_compose", label: "Ocultar la caja para escribir al dietista", selector: { boolean: {} } },
  { name: "show_measurement", label: "Mostrar «Registrar medición» (manual, escribe en tu cuenta)", selector: { boolean: {} } },
  { name: "hide_booking", label: "Ocultar pedir/confirmar/cancelar cita", selector: { boolean: {} } },
  { name: "hide_charlas", label: "Ocultar charlas", selector: { boolean: {} } },
  { name: "hide_images", label: "Ocultar fotos de los platos", selector: { boolean: {} } },
  {
    name: "todo_entity",
    label: "Lista de Home Assistant para la compra (opcional)",
    selector: { entity: { domain: "todo" } },
  },
  ...ENTITY_FIELDS.map(({ key, label }) => ({
    name: key,
    label: `Entidad: ${label} (solo con varias cuentas)`,
    selector: { entity: { domain: "sensor" } },
  })),
];

class NutriplanBridgeCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
  }

  connectedCallback() {
    this._render();
  }

  _onChange(ev) {
    ev.stopPropagation();
    const next = {};
    Object.entries(ev.detail.value || {}).forEach(([k, v]) => {
      if (v !== "" && v !== undefined && v !== null && v !== false) next[k] = v;
    });
    this._config = next;
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: next }, bubbles: true, composed: true }));
  }

  _render() {
    if (!this.isConnected) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (schema) => schema.label || schema.name;
      this._form.addEventListener("value-changed", (ev) => this._onChange(ev));
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = EDITOR_SCHEMA;
    this._form.data = this._config || {};
  }
}

if (!customElements.get("nutriplan-bridge-card-editor")) {
  customElements.define("nutriplan-bridge-card-editor", NutriplanBridgeCardEditor);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "nutriplan-bridge-card")) {
  window.customCards.push({
    type: "nutriplan-bridge-card",
    name: "Nutriplan Bridge",
    preview: true,
    documentationURL: "https://github.com/MUbeira0/nutriplan-bridge",
    description: "Plan actual y planes anteriores, comidas por día con siguiente comida, progreso de calorías, macros, lista de la compra, próxima cita y seguimiento del peso.",
  });
}
})();
