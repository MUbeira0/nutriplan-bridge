# Nutriplan Bridge for Home Assistant

Integración personalizada de Home Assistant (`custom_components/nutriplan_bridge`)
para una app de pacientes de nutrición, con sensores de **plan/comidas del
día** y **próxima cita/seguimientos**, más una tarjeta Lovelace
(`nutriplan-bridge-card`) que se registra sola, sin tocar `resources:` a mano.

## Cómo se obtuvo el contrato de la API

No es una integración "a ciegas". La app oficial es React Native + Expo
compilada a bytecode Hermes (`assets/index.android.bundle`). Se decompiló ese
bytecode con [hermes-dec](https://github.com/P1sec/hermes-dec) y se leyó
directamente la definición de la API (RTK Query) del código fuente de la app:
rutas, verbos HTTP, y los nombres de campo exactos de las respuestas
(`sentByEmailAt`, `nombreHorario`, `pesoGrasa`, etc.). Esto se cruzó además
en vivo contra el backend (solo códigos de estado HTTP, sin usar ninguna
cuenta) para confirmar qué rutas existen y qué verbo aceptan.

**Verificado por código fuente + en vivo:**
- `POST /api/login_check` — FormData `_username` / `_password`
- `GET /api/paciente/plans` — lista de planes (`id`, `created`, `sentByEmailAt`, `title`)
- `GET /api/paciente/plan[?id=]` — detalle de un plan, con `dietas: [...]`
- `GET /api/paciente/cita` — `{"cita": {...} | null}`
- `GET /api/paciente/seguimientos` — lista con `peso`, `imc`, `pesoGrasa`, `porcentajeGrasa`, `pesoMasaMagra`, `pesoAgua`, `perimetroCintura`, `perimetroCinturaUmbilical`, `perimetroCadera`, `createdTimestamp`
- `GET /api/paciente/dietista`

Cada franja de una `dieta` (p. ej. `dieta.comida`) trae a su vez `hora` y una
lista `subingestas` (una comida puede tener más de un plato, p. ej. desayuno
= café + sándwich). Cada `subingesta.plato` trae `alimentoCantidades`
(ingredientes: `alimento.nombre`, `cantidad`, `medidaCasera`), `energia`
(kcal), `alergenos`, y `superPlato` (`nombre`, `receta` -puede ser `null` en
platos simples como una pieza de fruta-, `comensales`, `duracion`, `rating`,
`imagePath`/`thumbnail`). Esto está confirmado contra una respuesta real de
la API (no solo inferido del código de la app). El sensor `comidas_hoy`
expone todo esto ya montado en su atributo `comidas`, y la tarjeta lo
muestra desplegable con receta, alérgenos, calorías e ingredientes por cada
plato.

**Único punto sin confirmar al 100%:** el nombre exacto del campo de fecha
*dentro* del objeto `cita` (no había forma de verlo sin una respuesta real
autenticada). El sensor de "Próxima cita" prueba varios nombres candidatos y,
si ninguno encaja, sigue funcionando (indica "programada" en vez de la fecha)
y expone el JSON completo en el atributo `raw` para ajustarlo en un minuto si
hace falta.

## Instalación

1. Copia la carpeta `custom_components/nutriplan_bridge` de este repo a
   `<config>/custom_components/nutriplan_bridge` de tu instalación de Home
   Assistant.
2. Reinicia Home Assistant.
3. Ajustes → Dispositivos y servicios → Añadir integración → **Nutriplan
   Bridge**.
4. Introduce tu email y contraseña de la app (viajan directamente al backend
   real, nunca pasan por ningún otro sitio).

Esto crea 5 entidades bajo un dispositivo "Nutriplan Bridge":
`sensor.nutriplan_bridge_plan_actual`, `sensor.nutriplan_bridge_comidas_hoy`,
`sensor.nutriplan_bridge_proxima_cita`, `sensor.nutriplan_bridge_seguimientos`,
`sensor.nutriplan_bridge_dietista`.

## Tarjeta

Desde el editor de dashboards: "Añadir tarjeta" → busca "Nutriplan Bridge" →
se añade con un clic (tiene editor visual; las entidades se autodetectan, no
hace falta rellenar nada salvo que tengas varias cuentas). O en YAML:

```yaml
type: custom:nutriplan-bridge-card
```

No hace falta registrar ningún recurso a mano ni indicar entidades: la
integración registra el JS automáticamente y la tarjeta detecta sus propias
entidades por convención de nombre.

## Si algo no encaja con tu cuenta

Cada sensor expone el JSON crudo de la API en el atributo `raw`
(Herramientas para desarrolladores → Estados). Si algún estado sale vacío o
distinto a lo esperado, copia ese `raw` para poder ajustar el parseo con
datos reales.