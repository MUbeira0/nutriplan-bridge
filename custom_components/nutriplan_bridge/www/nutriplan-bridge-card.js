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
 * Each plato has a "Cambiar plato" button (calls the nutriplan_bridge.
 * opciones_plato / cambiar_plato actions) and a 5-star rating control
 * (nutriplan_bridge.valorar_plato). cambiar_plato mutates the real, live
 * plan - see api.py's docstring for what is/isn't fully confirmed about it.
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

const ENTITY_FIELDS = [
  { key: "plan_entity", suffix: "_plan_actual", label: "Plan actual" },
  { key: "meals_entity", suffix: "_comidas_hoy", label: "Comidas de hoy" },
  { key: "appointment_entity", suffix: "_proxima_cita", label: "Próxima cita" },
  { key: "tracking_entity", suffix: "_seguimientos", label: "Seguimientos" },
  { key: "dietista_entity", suffix: "_dietista", label: "Dietista" },
];

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
    // subingesta_id -> { status: "loading"|"options"|"applying"|"error", options?, franja?, error? }
    this._changeState = new Map();
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
    ];
    const changed = !this._lastEntities || entities.some((e, i) => e !== this._lastEntities[i]);
    this._lastEntities = entities;
    if (changed) this._safeRender();
  }

  getCardSize() {
    return 6;
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
        .meal-header ha-icon.meal-type { --mdc-icon-size: 18px; color: var(--primary-color); flex-shrink: 0; }
        .meal-header .meal-thumb {
          width: 28px; height: 28px; border-radius: 6px; object-fit: cover; flex-shrink: 0;
        }
        .meal-header .franja { font-size: 0.72em; color: var(--secondary-text-color); }
        .meal-header .plato { font-size: 0.92em; color: var(--primary-text-color); flex: 1; }
        .meal-header ha-icon.chevron {
          --mdc-icon-size: 20px; color: var(--secondary-text-color);
          transition: transform .15s ease;
        }
        .meal-header.expanded ha-icon.chevron { transform: rotate(180deg); }
        .meal-body { padding: 0 14px 14px 14px; display: flex; flex-direction: column; gap: 14px; }
        .plato-block:not(:first-child) { padding-top: 12px; border-top: 1px solid var(--divider-color, rgba(127,127,127,.2)); }
        .plato-nombre { font-size: 0.95em; font-weight: 500; color: var(--primary-text-color); margin-bottom: 6px; }
        .plato-block img {
          width: 100%; height: 160px; object-fit: cover; border-radius: 8px; margin-bottom: 8px;
          background: var(--secondary-background-color, rgba(127,127,127,.08));
        }
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
        .action-status { font-size: 0.82em; color: var(--secondary-text-color); }
        .action-status.error { color: var(--error-color, #c62828); margin-bottom: 6px; }
        .opciones-plato { display: flex; flex-wrap: wrap; }
      </style>
      <ha-card>
        <div class="unavailable">Nutriplan Bridge: esperando datos de Home Assistant…</div>
      </ha-card>
    `;
    // Delegated once on the shadow root (survives ha-card's innerHTML being
    // replaced on every render) instead of re-attaching listeners each time.
    this.shadowRoot.addEventListener("click", (ev) => this._handleClick(ev));
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

  _optionLabel(opt) {
    if (!opt || typeof opt !== "object") return "Opción";
    const superPlato = opt.superPlato || opt.super_plato || {};
    return superPlato.nombre || opt.nombre || opt.name || `Opción #${opt.id ?? "?"}`;
  }

  _extractOptions(response) {
    const raw = response && response.opciones;
    if (Array.isArray(raw)) return raw;
    if (raw && Array.isArray(raw.data)) return raw.data;
    if (raw && Array.isArray(raw.items)) return raw.items;
    return [];
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
      this._changeState.set(subingestaId, { status: "options", options: this._extractOptions(response), franja });
    } catch (err) {
      this._changeState.set(subingestaId, { status: "error", error: err.message || String(err), franja });
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
          subingesta_id: subingestaId,
          nuevo_plato_id: nuevoPlatoId,
        }),
        true
      );
      this._changeState.delete(subingestaId);
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
    const header = ev.target.closest(".meal-header");
    if (header) {
      this._toggle(header.dataset.franja);
      return;
    }
    const cambiarBtn = ev.target.closest(".cambiar-btn");
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
    const opcionBtn = ev.target.closest(".opcion-btn");
    if (opcionBtn) {
      this._applyCambiarPlato(opcionBtn.dataset.subingesta, opcionBtn.dataset.nuevoId);
      return;
    }
    const star = ev.target.closest(".rating-star");
    if (star) {
      const container = star.closest(".rating-stars");
      this._rateDish(container.dataset.superPlato, Number(star.dataset.value));
    }
  }

  _renderPlatoActions(plato, franja) {
    const stateKey = plato.subingesta_id;
    if (stateKey === undefined || stateKey === null) return "";
    const state = this._changeState.get(stateKey) || { status: "idle" };
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
        return `<div class="action-status">Sin alternativas disponibles. <button class="action-btn cancelar-btn" data-subingesta="${escapeHtml(
          stateKey
        )}">Cerrar</button></div>`;
      }
      const buttons = opts
        .map(
          (opt) =>
            `<button class="action-btn opcion-btn" data-subingesta="${escapeHtml(stateKey)}" data-nuevo-id="${escapeHtml(
              opt.id
            )}">${escapeHtml(this._optionLabel(opt))}</button>`
        )
        .join("");
      return `<div class="opciones-plato">${buttons}<button class="action-btn cancelar-btn" data-subingesta="${escapeHtml(
        stateKey
      )}">Cancelar</button></div>`;
    }
    if (state.status === "error") {
      return `<div class="action-status error">${escapeHtml(
        state.error
      )}</div><button class="cambiar-btn" ${dataAttrs}><ha-icon icon="mdi:refresh"></ha-icon>Reintentar</button>`;
    }
    return `<button class="cambiar-btn" ${dataAttrs}><ha-icon icon="mdi:swap-horizontal"></ha-icon>Cambiar plato</button>`;
  }

  _renderPlato(plato, franja) {
    const img = plato.imagen
      ? `<img src="${escapeHtml(plato.imagen)}" alt="${escapeHtml(plato.nombre || "")}" loading="lazy" onerror="this.style.display='none'" />`
      : "";
    const metaParts = [];
    if (plato.calorias) metaParts.push(`${escapeHtml(plato.calorias)} kcal`);
    if (plato.comensales) metaParts.push(`${escapeHtml(plato.comensales)} comensales`);
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

    const alergenos =
      plato.alergenos && plato.alergenos.length
        ? `<div class="meal-alergenos">Alérgenos: ${plato.alergenos.map(escapeHtml).join(", ")}</div>`
        : "";

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
      <div class="plato-block">
        <div class="plato-nombre">${escapeHtml(plato.nombre || "Sin especificar")}</div>
        ${img}
        ${meta}
        ${starsHtml}
        ${alergenos}
        ${receta}
        ${ingredientes}
        <div class="plato-actions">${this._renderPlatoActions(plato, franja)}</div>
      </div>
    `;
  }

  _renderMeal(meal, labelsOverride) {
    const franja = meal.franja;
    const isOpen = this._expanded.has(franja);
    const icon = MEAL_ICONS[franja] || "mdi:food";
    const label = (labelsOverride && labelsOverride[franja]) || MEAL_LABELS[franja] || franja;
    const platos = meal.platos || [];
    const summary = platos.length
      ? platos.map((p) => p.nombre || "Sin especificar").join(" + ")
      : "Sin especificar";
    const thumbSrc = platos.find((p) => p.imagen)?.imagen;
    const thumbHtml = thumbSrc
      ? `<img class="meal-thumb" src="${escapeHtml(thumbSrc)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('ha-icon'),{className:'meal-type',icon:'${icon}'}))" />`
      : `<ha-icon class="meal-type" icon="${icon}"></ha-icon>`;

    let bodyHtml = "";
    if (isOpen) {
      bodyHtml = `<div class="meal-body">${platos.map((p) => this._renderPlato(p, franja)).join("")}</div>`;
    }

    return `
      <div class="meal-card">
        <div class="meal-header${isOpen ? " expanded" : ""}" data-franja="${escapeHtml(franja)}">
          ${thumbHtml}
          <span class="franja">${label}${meal.hora ? ` · ${escapeHtml(meal.hora)}` : ""}</span>
          <span class="plato">${escapeHtml(summary)}</span>
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
    this._planId = mealsAttrs.plan_id;
    this._dietaIndex = mealsAttrs.dieta_index;
    // Needed so the actions below know WHICH configured account a plato
    // belongs to - without it, calling them with more than one Nutriplan
    // Bridge account configured fails ("Multiple accounts are configured;
    // pass config_entry_id to pick one").
    this._configEntryId = mealsAttrs.config_entry_id;

    const dietistaAttrs = (dietistaEntity && dietistaEntity.attributes) || {};
    const dietistaName =
      dietistaEntity && dietistaEntity.state !== "unknown" ? dietistaEntity.state : "Nutriplan Bridge";
    const dietistaAvatar = dietistaAttrs.avatar;
    const mealLabels = dietistaAttrs.nombres_franjas || {};

    const planName =
      planEntity && planEntity.state !== "unknown" ? planEntity.state : "Sin plan activo";

    const meals = mealsAttrs.comidas || [];
    const mealsHtml = meals.length
      ? `<div class="meals">${meals.map((m) => this._renderMeal(m, mealLabels)).join("")}</div>`
      : '<div class="empty">Sin comidas programadas para hoy</div>';

    let apptHtml = '<div class="empty">Sin próxima cita</div>';
    if (apptEntity && apptEntity.state && apptEntity.state !== "unknown") {
      const label =
        apptEntity.state === "programada" ? "Cita programada" : formatDate(apptEntity.state);
      apptHtml = `<div class="appointment"><ha-icon icon="mdi:calendar-clock"></ha-icon><span>${escapeHtml(
        label
      )}</span></div>`;
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
              `<div class="metric"><span class="value">${escapeHtml(a[key])}</span><span class="label">${label}</span></div>`
          )
          .join("")}</div>`;
      } else {
        metricsHtml = '<div class="empty">Sin seguimientos registrados</div>';
      }
    }

    const avatarHtml = dietistaAvatar
      ? `<img class="avatar" src="${escapeHtml(dietistaAvatar)}" alt="${escapeHtml(dietistaName)}" onerror="this.replaceWith(Object.assign(document.createElement('ha-icon'),{icon:'mdi:food-apple'}))" />`
      : `<ha-icon icon="mdi:food-apple"></ha-icon>`;

    card.innerHTML = `
      <div class="header">
        ${avatarHtml}
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
 * dashboard. All fields are optional - entities are auto-detected - this is
 * only useful if you run more than one Nutriplan Bridge account. */
class NutriplanBridgeCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._forms) {
      this._forms.forEach((form) => (form.hass = hass));
    }
  }

  connectedCallback() {
    this._render();
  }

  _valueChanged(key, value) {
    const next = { ...this._config };
    if (value) {
      next[key] = value;
    } else {
      delete next[key];
    }
    this._config = next;
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: next }, bubbles: true, composed: true }));
  }

  _render() {
    if (!this.isConnected) return;
    this.innerHTML = `
      <style>
        .info { padding: 8px 2px 14px 2px; color: var(--secondary-text-color); font-size: 0.9em; }
        .field { margin-bottom: 8px; }
        .field label { display: block; font-size: 0.85em; color: var(--secondary-text-color); margin-bottom: 2px; }
      </style>
      <div class="info">
        Las entidades se detectan automáticamente (no hace falta rellenar nada).
        Usa esto solo si tienes varias cuentas de Nutriplan Bridge y quieres
        forzar qué sensores usa esta tarjeta en concreto.
      </div>
      <div class="fields"></div>
    `;
    const container = this.querySelector(".fields");
    this._forms = [];
    ENTITY_FIELDS.forEach(({ key, label }) => {
      const wrapper = document.createElement("div");
      wrapper.className = "field";
      const labelEl = document.createElement("label");
      labelEl.textContent = label;
      wrapper.appendChild(labelEl);

      const picker = document.createElement("ha-entity-picker");
      picker.hass = this._hass;
      picker.value = this._config[key] || "";
      picker.includeDomains = ["sensor"];
      picker.addEventListener("value-changed", (ev) => {
        ev.stopPropagation();
        this._valueChanged(key, ev.detail.value);
      });
      wrapper.appendChild(picker);
      container.appendChild(wrapper);
      this._forms.push(picker);
    });
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
    description: "Plan actual, comidas de hoy (con receta, ingredientes y cambio/valoración de platos), próxima cita y seguimientos.",
  });
}
