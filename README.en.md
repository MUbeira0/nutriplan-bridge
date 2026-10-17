# Nutriplan Bridge for Home Assistant

[Español](README.md) · English

A Home Assistant custom integration and Lovelace card for **patients of the DietoPro nutrition
app** (dietopro.com): your diet plan, today's meals with recipes, your appointments, body
measurements and chat with your dietitian, inside Home Assistant.

> **Unofficial.** Not affiliated with or endorsed by DietoPro. It signs in with *your own* patient
> account, exactly like the official app, and talks to the same API. DietoPro may change that API
> at any time; when it does, Home Assistant shows a notice under *Settings → Repairs* and the
> diagnostics file makes it easy to report. Use at your own risk, and read the *Actions that write*
> section below before using them.

<p align="center">
  <img src="docs/screenshots/light_day.png" width="300" alt="Card, light theme">
  <img src="docs/screenshots/dark_day.png" width="300" alt="Card, dark theme">
</p>

## Install

**HACS (recommended):** HACS → Integrations → ⋮ → *Custom repositories* → add
`https://github.com/MUbeira0/nutriplan-bridge` as an *Integration* → Download → restart Home
Assistant. Then *Settings → Devices & services → Add integration → Nutriplan Bridge* and sign in
with your DietoPro patient e-mail and password.

The card registers itself — no dashboard resource to add. Add it from the card picker
(*Nutriplan Bridge*) or in YAML:

```yaml
type: custom:nutriplan-bridge-card
```

Everything is auto-detected; the visual editor only matters if you configured several accounts or
want to hide sections.

## What you get

| Platform | Entities |
|---|---|
| sensor | current plan, today's meals, next appointment, body measurements, dietitian, unread messages, profile, initial survey, ratings, talks |
| sensor (numeric, with history) | weight, BMI, body fat %, lean mass, waist, today's calories/protein/fat/carbs/fibre, next meal, calorie target, target weight, weight to goal, 30-day weight change, days since last measurement, meals eaten today (with weekly adherence) |
| binary_sensor | unread messages, appointment within 24 h, new plan pending, measurement due, appointment unconfirmed |
| calendar | meals, appointments |
| todo | shopping list of the whole plan |
| button | refresh now, mark chat as read |

Bus events for automations: `nutriplan_bridge_plan_changed`, `nutriplan_bridge_new_message`,
`nutriplan_bridge_appointment_changed`.

**Today's plan is chosen by its start date** (the date in the plan's title), not by "latest sent",
so next week's plan does not replace the current one early. The day and plan roll over at midnight.

### The card

* Day chips (today marked), week overview, previous/next plans (read-only), swipe between days.
* Next meal highlighted; tick meals as eaten (stored in Home Assistant, shared by all your
  devices) with calories eaten, macro split against your targets, weekly adherence.
* Per dish: recipe, ingredients, nutrients, allergens as chips, your rating; swap a dish or a lone
  food for an alternative.
* Shopping list (whole plan or one day), optionally pushed to a Home Assistant to-do list.
* Appointment with confirmation status; booking / cancelling / confirming buttons appear **only
  if your account allows them** (read from the API).
* Chat panel with unread count, composer and attachments; weight chart.
* Accessible (keyboard, ARIA), compact mode, accent colour, sections can be hidden.

## Actions that write to your real account

These change data in your DietoPro account and your dietitian sees them. They were traced from the
official app's code and tested against a fake server, **not** against a live account — check the
result in the official app the first time:

`enviar_mensaje`, `enviar_archivo_chat`, `solicitar_cita`, `confirmar_cita`, `cancelar_cita`,
`cambiar_plato`, `cambiar_alimento`, `valorar_plato`, `marcar_chat_leido`, `registrar_medicion`.

`registrar_medicion` is **manual and optional**: your dietitian must have opened a measurement in
DietoPro (the app fills it in step by step); the integration cannot create one, never sends
anything on its own, and the card button only exists if you enable `show_measurement`.

Read-only actions: `obtener_comidas`, `lista_compra`, `citas_disponibles`, `opciones_plato`,
`opciones_alimento`, `medicion_abierta`. Local only (nothing sent to DietoPro): `marcar_comida`.

## Notes

* Polling is every 15 minutes by default (configurable); each refresh reads all sources so changes
  made in the official app show up.
* Entity names are in Spanish and are not translated on purpose: the card finds the integration's
  entities by the end of their entity id.
* Sensitive attributes (profile, health survey, chat text) are exposed as attributes, as in the
  Spanish docs. Keep that in mind when sharing dashboards.

## Troubleshooting

*Settings → Devices & services → Nutriplan Bridge → Download diagnostics* produces a file with
personal data redacted; attach it to a bug report.

## Development

```bash
pip install homeassistant pytest
python -m pytest -q                       # unit tests (CI also runs hassfest and HACS validation)
pip install playwright aiohttp requests
pytest tests/e2e -o addopts=""            # end to end: a real Home Assistant + a fake DietoPro + a browser
```

License: MIT.
