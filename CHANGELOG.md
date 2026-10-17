# Changelog

## 0.29.1
- Corrige el error «async_write_ha_state from a thread» que aparecía cada minuto (sensor Próxima comida) y cada 5 minutos (binary sensor Cita en 24 h): el refresco periódico era un `lambda`, que Home Assistant ejecuta en un hilo aparte; ahora es un `@callback` en el bucle de eventos. Desde la 0.23.0.
- Test que impide volver a pasar un `lambda` a los temporizadores de Home Assistant. 93 tests.

## 0.29.0
- Cambiar alimentos sueltos: acciones `opciones_alimento` y `cambiar_alimento` y botón «Cambiar alimento» en la tarjeta (contratos de la app oficial v51).
- Alimentos sueltos sin hueco de imagen ni «No requiere preparación». `opciones_plato` rechaza IDs vacíos con un mensaje claro.
- La tarjeta se sirve desde una ruta con la versión para evitar copias antiguas en caché. 90 tests.

## 0.28.2
- Causa real de los «Sin especificar»: una subingesta puede ser un `plato`, un alimento suelto (`alimentoCantidad`) o un suplemento (`suplementoCantidad`), y solo se conocía el primero. Ahora los tres se leen (estructura sacada de la app oficial y confirmada con un diagnóstico real).
- Los alimentos sueltos aparecen con su nombre y cantidad («Pechuga de pollo (150 g)»), cuentan en la lista de la compra y no ofrecen «Cambiar plato» (la app los cambia por otro endpoint).
- El diagnóstico lista las claves de los alimentos sueltos para ajustar sus calorías si la API las envía.

## 0.28.1
- Un alimento suelto (p. ej. una pechuga de pollo), que la API manda sin `superPlato`, ya no sale como «Sin especificar»: se usa el nombre del propio plato o, si no hay, el del ingrediente («Pechuga de pollo (150 g)»).
- El diagnóstico lista la estructura de los platos que sigan sin nombre.

## 0.28.0
- Ajustes con datos de una cuenta real: duración real de la cita, confirmaciones y permisos de la cuenta (reserva, cancelación, confirmación) que gobiernan los botones de la tarjeta.
- Binary sensor *Cita sin confirmar*; charlas en la tarjeta; objetivo calórico y de macros por día junto al resumen del día.
- `codeowners` apunta a `@MUbeira0`. 71 tests.

## 0.27.0
- Marca: icono y logo claro/oscuro. Iconos para todas las acciones y entidades con icono según estado.
- Tarjeta: accesibilidad (teclado, aria), `getGridOptions` y vista previa, alérgenos como etiquetas, kcal por comida, valoración por plato, estados vacíos y de carga, modo compacto, color de acento.
- Arreglo: alérgenos que llegan como objetos ya no se muestran como "[object Object]".
- Capturas en el README. 63 tests.

## 0.26.0
- Acciones sacadas de la app oficial v51: `enviar_mensaje`, `citas_disponibles`, `solicitar_cita`, `confirmar_cita`, `cancelar_cita`.
- Tarjeta: escribir al dietista, pedir y cancelar cita (con confirmación).
- Sensor Próxima cita expone `cita_id`; el chat se pide con `limit=30` como hace la app.
- 57 tests. Licencia MIT.

## 0.25.0
- Tests automáticos (43) y CI: pytest + hassfest + validación de HACS.
- Blueprints: aviso antes de cada comida, recordatorio de cita, recordatorio de pesarte, aviso de plan nuevo/mensaje.
- Ejemplo de frases e `intent_script` para voz.
- Icono de la integración, README con todas las entidades y guía de instalación con HACS.

## 0.24.0
- Lista de la compra nativa (`todo`), binary sensors, botones, más sensores numéricos, eventos en el bus.
- Tarjeta: vista semana, deslizar entre días, nutrientes por plato, contacto, panel de mensajes, gráfica de peso ampliable.

## 0.23.0
- Corrección del cambio de día/semana (refresco a medianoche, plan vigente sin exigir "enviado por email", planes de varias semanas).
- Sensores numéricos con histórico, calendarios, acción `lista_compra`, diagnóstico descargable.
- Tarjeta: siguiente comida, comidas tomadas, macros, lista de la compra.

## 0.22.1
- El plan actual es el que corresponde a hoy (fecha de inicio del título), no el último enviado.

## 0.22.0
- Tarjeta: selector de día y planes anteriores. Acción `obtener_comidas`. Flujo de reautenticación.
- Correcciones: 404 silencioso en escrituras, login 5xx tomado por contraseña incorrecta, actualizaciones totalmente fallidas.
