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
- `GET /api/paciente/current-user` — perfil del propio paciente
- `GET /api/paciente/eni` — estado de la encuesta nutricional inicial
- `GET /api/paciente/rating` — valoraciones que el paciente ha dado
- `GET /api/paciente/charla` — charlas/consultas con el dietista
- `GET /api/paciente/chat/unread` y `/chat/messages` — chat con el dietista
- `PUT /api/paciente/chat/reset-unread` — marcar el chat como leído
- `PUT /api/paciente/superplato/{id}/rating` — valorar un plato

`api/paciente/pagos` se intuía por el nombre del hook `useGetPagosQuery` pero
**no existe** (probado en vivo: 404), así que no hay nada de pagos en la
integración.

Cada franja de una `dieta` (p. ej. `dieta.comida`) trae a su vez `hora` y una
lista `subingestas` (una comida puede tener más de un plato, p. ej. desayuno
= café + sándwich). Cada `subingesta.plato` trae `alimentoCantidades`
(ingredientes: `alimento.nombre`, `cantidad`, `medidaCasera`, más el grupo y
supergrupo del alimento), datos nutricionales completos (energía, proteínas,
grasas, carbohidratos, fibra, sodio, potasio, calcio, hierro, vitaminas...),
`alergenos`, y `superPlato` (`nombre`, `receta` -puede ser `null` en platos
simples como una pieza de fruta-, `comensales`, `duracion`, `rating`,
`imagePath`/`thumbnail`). Esto está confirmado contra una respuesta real de
la API (no solo inferido del código de la app). El sensor `comidas_hoy`
expone todo esto ya montado por plato en su atributo `comidas` (incluido un
diccionario `nutrientes` por plato, y los identificadores `subingesta_id`/
`plato_id`/`super_plato_id` que hacen falta para las acciones de cambiar o
valorar un plato), y además un `resumen_nutricional` con el total del día
(suma de todos los platos), y `plan_id`/`dieta_index` para las mismas
acciones. La tarjeta lo muestra desplegable con receta, alérgenos, calorías
e ingredientes por cada plato.

El sensor `seguimientos` incluye además un `historial` completo (no solo el
último registro) y `delta_peso`/`delta_imc`/`delta_peso_grasa`/`delta_porcentaje_grasa`
respecto a la medición anterior. El campo real de agua corporal es
`porcentajeAgua` (no `pesoAgua` como sugería el código decompilado); cuando
la cuenta no manda directamente `pesoAgua`/`pesoMasaMagra`, se calculan
(`peso_agua = peso * porcentajeAgua / 100`, `peso_masa_magra = peso - peso_grasa`).

El sensor `estado_eni` no es solo "% completado": es la ficha completa de la
encuesta inicial (objetivo, antropometría, alergias/intolerancias/aversiones,
fármacos, hábitos de vida, y los objetivos de energía/macros que DietoPro
calculó a partir de ella, incluida una `ingesta_diaria_recomendada` con el
mismo formato que los `nutrientes` de cada plato). Se completa cuando
`lastViewedStep` llega a `"eniFinish"` (los flags booleanos que se habían
intuido al principio no existen).

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

Esto crea 10 entidades bajo un dispositivo "Nutriplan Bridge":
`sensor.nutriplan_bridge_plan_actual`, `sensor.nutriplan_bridge_comidas_hoy`,
`sensor.nutriplan_bridge_proxima_cita`, `sensor.nutriplan_bridge_seguimientos`,
`sensor.nutriplan_bridge_dietista`, `sensor.nutriplan_bridge_mensajes_sin_leer`,
`sensor.nutriplan_bridge_mi_perfil`, `sensor.nutriplan_bridge_estado_eni`,
`sensor.nutriplan_bridge_valoraciones`, `sensor.nutriplan_bridge_charlas`.

Y cuatro acciones (Herramientas de desarrollo → Acciones, o en automatizaciones):
- `nutriplan_bridge.marcar_chat_leido`
- `nutriplan_bridge.valorar_plato` (necesita el `super_plato_id` que trae
  cada plato en el atributo `comidas` del sensor `comidas_hoy`, y una
  puntuación de 1 a 5)
- `nutriplan_bridge.opciones_plato` (solo lectura: lista los platos
  alternativos para sustituir uno en una franja, usando `plato_id` + `franja`
  del atributo `comidas`)
- `nutriplan_bridge.cambiar_plato` — **modifica tu plan real, no es
  reversible desde HA**. Necesita `plan_id`, `dieta` (el `dieta_index` del
  sensor `comidas_hoy` — es el día de la semana, 0=lunes, deducido de la
  propia llamada a esta acción en la app), `franja`, `plato_actual_id` (el
  `plato_id` del plato a sustituir, no su `subingesta_id`) y
  `nuevo_plato_id` (de `opciones_plato`).

  **Historial:** la primera versión daba un 500 real (error interno del
  servidor de DietoPro) al probarla en vivo, y tras corregir eso pasó a
  "funcionar" sin error pero sin cambiar nunca el plato de verdad (ni en
  Home Assistant ni en la app oficial). Las dos causas, encontradas
  retrocediendo otra vez sobre el código de la mutación:
  1. La app convierte `planId`/`dieta` a número explícitamente pero manda
     `currentId` tal cual - se asumió que `currentId` era el `subingesta_id`
     (texto, tipo `"14256879280"`), corregido para que viajara como texto.
  2. **Ese supuesto era incorrecto de raíz**: rastreando el punto exacto
     donde la app arma la llamada (dos sitios distintos en el código
     decompilado, `ChangePlatoOptionsContainer` y
     `ChangePlatoPreviewModal`, coinciden) se ve que `currentId` en
     realidad **nunca** es el `subingesta_id` — es el propio `plato_id`
     (número) del plato que se está sustituyendo, el mismo identificador
     que ya se usa para pedir alternativas en `opciones_plato`. Al mandar
     el `subingesta_id`, DietoPro no encontraba ningún plato que
     coincidiera y simplemente no hacía nada, sin dar ningún error. Ya
     corregido: el campo se llama `plato_actual_id` y espera el `plato_id`,
     no el `subingesta_id`.

  También se vio en vivo que alguna opción venía sin `id` utilizable, lo
  que rompía la validación con un error críptico ("expected int...").
  Corregido (opciones sin id descartadas, y validación propia con mensajes
  claros en vez de dejar que falle la validación en bruto). Si te sigue
  fallando, prueba el mismo cambio desde la app oficial: si allí también
  falla, no es cosa de esta integración.
  Los errores salen cortos en la tarjeta; el detalle técnico completo
  queda en el registro de Home Assistant (Ajustes → Sistema → Registros).

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

**Si aun así no aparece sola** (le pasó a alguien: los sensores funcionaban
pero el recurso no se registraba solo): el `manifest.json` no declaraba que
esta integración depende de `frontend`, así que Home Assistant no
garantizaba que ese componente ya estuviera listo cuando intentaba
registrar el script — según el orden de arranque, podía fallar en
silencio. Ya está declarado como dependencia (v0.20.0+). Si aun con eso
falla, ahora queda registrado como error en el log de Home Assistant en
vez de fallar callado, y puedes añadirlo a mano como alternativa: Ajustes →
Panel de control → Recursos → Añadir recurso → URL
`/nutriplan_bridge_files/nutriplan-bridge-card.js` → tipo "Módulo de
JavaScript".

Pensada para móvil: al desplegar una comida solo se ve una lista corta
(foto pequeña + nombre + kcal por plato); tocando un plato concreto se abre
su detalle completo (receta, ingredientes, alérgenos, estrellas para
valorarlo con `valorar_plato`). El botón "Cambiar plato" consulta
`opciones_plato` y muestra **todas** las alternativas (el backend busca la
lista dentro de la respuesta y descarta arrays más pequeños que puedan
colar antes, en vez de asumir una clave fija) — cada opción es también
desplegable, con su propia receta e ingredientes, y solo se aplica al
pulsar "Aceptar este plato" (llama a `cambiar_plato`). Así una comida con
varios platos no vuelca tres recetas enteras de golpe en la pantalla. Si
tienes varias cuentas configuradas, la tarjeta ya sabe de cuál es cada plato
(el sensor `comidas_hoy` expone su propio `config_entry_id`) y lo manda solo
en cada llamada.

La tarjeta solo se vuelve a pintar cuando cambia alguna de sus propias
entidades, no en cada actualización de Home Assistant (evita parpadeos y
saltos al recargar imágenes sin necesidad).

## Si algo no encaja con tu cuenta

Cada sensor expone el JSON crudo de la API en el atributo `raw`
(Herramientas para desarrolladores → Estados). Si algún estado sale vacío o
distinto a lo esperado, copia ese `raw` para poder ajustar el parseo con
datos reales.