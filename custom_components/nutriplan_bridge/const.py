"""Constants for the Nutriplan Bridge integration."""

DOMAIN = "nutriplan_bridge"

# Endpoint contract confirmed by decompiling the vendor's own app bundle
# (Hermes bytecode -> hermes-dec) and cross-checked live (HTTP status codes
# only, no account used) on 2026-08-10:
# - POST /api/login_check            -> FormData {"_username", "_password"}
# - POST /api/paciente/token/refresh -> exists (Allow: POST), body/response format assumed
#   (gesdinet/jwt-refresh-token-bundle default: {"refresh_token": "..."} ->
#    {"token": "...", "refresh_token": "..."}). Not confirmed with a real 200 response
#   because no test account was available - if refresh fails the client falls back
#   to a full re-login with the stored email/password automatically.
# - GET  /api/paciente/plans        -> list of plan summaries
# - GET  /api/paciente/plan[?id=]   -> plan detail, incl. nested "dietas"
# - GET  /api/paciente/cita         -> {"cita": {...} | null}
# - GET  /api/paciente/seguimientos -> body-composition tracking entries
# - GET  /api/paciente/dietista     -> assigned dietitian
BASE_URL = "https://dietopro.com"

CONF_UPDATE_INTERVAL = "update_interval"
DEFAULT_UPDATE_INTERVAL = 15  # minutes

PLATFORMS = ["sensor"]
