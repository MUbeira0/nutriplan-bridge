/* Nutriplan Bridge custom Lovelace card.
 * Bundled with and auto-registered by the custom_components/nutriplan_bridge
 * integration (see __init__.py -> add_extra_js_url). No manual resource
 * registration needed.
 *
 * Zero-config by default: it looks for entities created by this integration
 * (unique object_id suffixes "plan_actual", "comidas_hoy", "proxima_cita",
 * "seguimientos", "dietista"). You can also point it at explicit entity ids:
 *
 * type: custom:nutriplan-bridge-card
 * plan_entity: sensor.nutriplan_bridge_plan_actual
 * meals_entity: sensor.nutriplan_bridge_comidas_hoy
 * appointment_entity: sensor.nutriplan_bridge_proxima_cita
 * tracking_entity: sensor.nutriplan_bridge_seguimientos
 * dietista_entity: sensor.nutriplan_bridge_dietista
 *
 * The "comidas_hoy" sensor's "comidas" attribute carries, per meal slot:
 * { franja, plato, receta, comensales, rating, imagen, ingredientes: [{nombre, cantidad, medida_casera}] }
 */

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

function findEntity(hass, explicitId, suffix) {
  if (explicitId && hass.states[explicitId]) return hass.states[explicitId];
  const match = Object.keys(hass.states).find(
    (id) => id.startsWith("sensor.nutriplan_bridge_") && id.endsWith(suffix)
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
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 6;
  }

  static getStubConfig() {
    return {};
  }

  connectedCallback() {
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
  }

  _ensureDom() {
    if (this.shadowRoot.childElementCount) return;
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 16px; }
        .header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
        .header ha-icon { color: var(--primary-color); --mdc-icon-size: 28px; }
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
        .meal-header ha-icon.meal-type { --mdc-icon-size: 18px; color: var(--primary-color); }
        .meal-header .franja { font-size: 0.72em; color: var(--secondary-text-color); }
        .meal-header .plato { font-size: 0.92em; color: var(--primary-text-color); flex: 1; }
        .meal-header ha-icon.chevron {
          --mdc-icon-size: 20px; color: var(--secondary-text-color);
          transition: transform .15s ease;
        }
        .meal-header.expanded ha-icon.chevron { transform: rotate(180deg); }
        .meal-body { padding: 0 14px 14px 14px; }
        .meal-body img {
          width: 100%; max-height: 160px; object-fit: cover; border-radius: 8px; margin-bottom: 8px;
        }
        .meal-meta { display: flex; gap: 14px; font-size: 0.78em; color: var(--secondary-text-color); margin-bottom: 8px; }
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
      </style>
      <ha-card>
        <div class="unavailable">Nutriplan Bridge: esperando datos de Home Assistant…</div>
      </ha-card>
    `;
  }

  _toggle(franja) {
    if (this._expanded.has(franja)) {
      this._expanded.delete(franja);
    } else {
      this._expanded.add(franja);
    }
    this._render();
  }

  _renderMeal(meal) {
    const franja = meal.franja;
    const isOpen = this._expanded.has(franja);
    const icon = MEAL_ICONS[franja] || "mdi:food";
    const label = MEAL_LABELS[franja] || franja;
    const platoName = meal.plato || "Sin especificar";

    let bodyHtml = "";
    if (isOpen) {
      const img = meal.imagen ? `<img src="${escapeHtml(meal.imagen)}" alt="${escapeHtml(platoName)}" />` : "";
      const metaParts = [];
      if (meal.comensales) metaParts.push(`${escapeHtml(meal.comensales)} comensales`);
      if (meal.rating) metaParts.push(`★ ${escapeHtml(meal.rating)}`);
      const meta = metaParts.length ? `<div class="meal-meta">${metaParts.join(" · ")}</div>` : "";
      const receta = meal.receta
        ? `<div class="meal-receta">${escapeHtml(meal.receta)}</div>`
        : '<div class="empty">Sin receta disponible</div>';
      const ingredientes =
        meal.ingredientes && meal.ingredientes.length
          ? `<ul class="meal-ingredientes">${meal.ingredientes
              .map((ing) => {
                const cantidad = ing.cantidad ? `${escapeHtml(ing.cantidad)}g ` : "";
                const medida = ing.medida_casera ? ` (${escapeHtml(ing.medida_casera)})` : "";
                return `<li>${cantidad}${escapeHtml(ing.nombre || "")}${medida}</li>`;
              })
              .join("")}</ul>`
          : "";
      bodyHtml = `<div class="meal-body">${img}${meta}${receta}${ingredientes}</div>`;
    }

    return `
      <div class="meal-card">
        <div class="meal-header${isOpen ? " expanded" : ""}" data-franja="${escapeHtml(franja)}">
          <ha-icon class="meal-type" icon="${icon}"></ha-icon>
          <span class="franja">${label}</span>
          <span class="plato">${escapeHtml(platoName)}</span>
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
    const cfg = this._config;

    const planEntity = findEntity(hass, cfg.plan_entity, "_plan_actual");
    const mealsEntity = findEntity(hass, cfg.meals_entity, "_comidas_hoy");
    const apptEntity = findEntity(hass, cfg.appointment_entity, "_proxima_cita");
    const trackingEntity = findEntity(hass, cfg.tracking_entity, "_seguimientos");
    const dietistaEntity = findEntity(hass, cfg.dietista_entity, "_dietista");

    const card = this.shadowRoot.querySelector("ha-card");
    if (!planEntity && !mealsEntity && !apptEntity && !trackingEntity) {
      card.innerHTML =
        '<div class="unavailable">No se han encontrado entidades de Nutriplan Bridge. Configura la integración primero.</div>';
      return;
    }

    const dietistaName =
      dietistaEntity && dietistaEntity.state !== "unknown" ? dietistaEntity.state : "Nutriplan Bridge";

    const planName =
      planEntity && planEntity.state !== "unknown" ? planEntity.state : "Sin plan activo";

    const meals = (mealsEntity && mealsEntity.attributes.comidas) || [];
    const mealsHtml = meals.length
      ? `<div class="meals">${meals.map((m) => this._renderMeal(m)).join("")}</div>`
      : '<div class="empty">Sin comidas programadas para hoy</div>';

    let apptHtml = '<div class="empty">Sin próxima cita</div>';
    if (apptEntity && apptEntity.state && apptEntity.state !== "unknown") {
      const label =
        apptEntity.state === "programada" ? "Cita programada" : formatDate(apptEntity.state);
      apptHtml = `<div class="appointment"><ha-icon icon="mdi:calendar-clock"></ha-icon><span>${label}</span></div>`;
    }

    let metricsHtml = "";
    if (trackingEntity) {
      const a = trackingEntity.attributes || {};
      const metrics = [
        ["ultimo_peso", "Peso (kg)"],
        ["ultimo_imc", "IMC"],
        ["ultimo_peso_grasa", "Grasa (kg)"],
      ].filter(([key]) => a[key] !== undefined && a[key] !== null);
      if (metrics.length) {
        metricsHtml = `<div class="metrics">${metrics
          .map(
            ([key, label]) =>
              `<div class="metric"><span class="value">${a[key]}</span><span class="label">${label}</span></div>`
          )
          .join("")}</div>`;
      } else {
        metricsHtml = '<div class="empty">Sin seguimientos registrados</div>';
      }
    }

    card.innerHTML = `
      <div class="header">
        <ha-icon icon="mdi:food-apple"></ha-icon>
        <div>
          <div class="title">${escapeHtml(planName)}</div>
          <div class="subtitle">${escapeHtml(dietistaName)}</div>
        </div>
      </div>

      <div class="section">
        <div class="section-title">Comidas de hoy</div>
        ${mealsHtml}
      </div>

      <div class="section">
        <div class="section-title">Próxima cita</div>
        ${apptHtml}
      </div>

      ${
        trackingEntity
          ? `<div class="section">
              <div class="section-title">Último seguimiento</div>
              ${metricsHtml}
            </div>`
          : ""
      }
    `;

    card.querySelectorAll(".meal-header").forEach((el) => {
      el.addEventListener("click", () => this._toggle(el.dataset.franja));
    });
  }
}

customElements.define("nutriplan-bridge-card", NutriplanBridgeCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "nutriplan-bridge-card",
  name: "Nutriplan Bridge",
  description: "Plan actual, comidas de hoy (con receta e ingredientes), próxima cita y seguimientos.",
});
