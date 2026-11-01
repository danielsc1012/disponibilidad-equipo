# Disponibilidad del Equipo · Iglesia Northwood

Página con contraseña para que el equipo pastoral vea en qué fechas están o no están disponibles
los miembros del equipo pastoral y del equipo ministerial. Fuente: los blockout dates de Planning Center Services.

- `equipo.json` define quién aparece y en qué grupo (nombre como está en Planning Center, alias opcionales, `pco_id` opcional).
- `build.py` lee los blockouts de esas personas (4 semanas atrás → 6 meses adelante) y genera la página. No guarda motivos.
- `template.html` es la página: calendario mensual, "¿Quién está disponible?" y próximas ausencias.
- `.github/workflows/actualizar.yml` corre todos los días, cifra la página con StatiCrypt y la publica en GitHub Pages.

Ningún blockout se guarda en este repositorio.

**Secretos necesarios** (Settings → Secrets and variables → Actions):
`PCO_APP_ID`, `PCO_SECRET` (Personal Access Token de Planning Center) y `PORTAL_PASSWORD` (la contraseña del equipo).
