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

class NutriplanBridgeCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 5;
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
        .plan-name { font-size: 1.05em; color: var(--primary-text-color); }
        .meals { display: flex; flex-wrap: wrap; gap: 8px; }
        .meal-chip {
          display: flex; align-items: center; gap: 6px;
          background: var(--secondary-background-color, rgba(127,127,127,.08));
          border-radius: 16px; padding: 6px 12px; font-size: 0.88em;
          color: var(--primary-text-color);
        }
        .meal-chip ha-icon { --mdc-icon-size: 18px; color: var(--primary-color); }
        .empty { color: var(--secondary-text-color); font-style: italic; font-size: 0.9em; }
        .row { display: flex; justify-content: space-between; align-items: center; }
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

    const franjas = (mealsEntity && mealsEntity.attributes.franjas) || [];
    const mealsHtml = franjas.length
      ? `<div class="meals">${franjas
          .map(
            (f) =>
              `<div class="meal-chip"><ha-icon icon="${MEAL_ICONS[f] || "mdi:food"}"></ha-icon>${
                MEAL_LABELS[f] || f
              }</div>`
          )
          .join("")}</div>`
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
          <div class="title">${planName}</div>
          <div class="subtitle">${dietistaName}</div>
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
  }
}

customElements.define("nutriplan-bridge-card", NutriplanBridgeCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "nutriplan-bridge-card",
  name: "Nutriplan Bridge",
  description: "Plan actual, comidas de hoy, próxima cita y seguimientos de tu cuenta de nutrición.",
});
