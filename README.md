# Mesa de Ayuda (Help Desk) – versión web

Sistema de tickets tipo GLPI hecho con **Flask + SQLAlchemy**.

## Funciones
- **Cuentas de cliente:** el cliente se registra una sola vez (nombre, apellido, dirección, teléfono, correo y contraseña). Sus datos quedan guardados y la sesión permanece iniciada hasta 30 días ("Mantener sesión iniciada"). Puede editar sus datos en *Mi perfil* y ver *Mis tickets*.
- **Tickets:** el cliente solo escribe asunto, categoría, prioridad y descripción; los datos personales se toman de su perfil. Se genera el código `TK-AAAA-00001`. Cualquiera puede consultar el estado con el código (sin ver datos personales).
- **Seguimiento:** En proceso · Atendido · Finalizado · Escalado a dependencia superior, con historial (fecha, estado, responsable, comentario).
- **Panel técnico (login):** lista con búsqueda y filtro, actualizar estados, escalar a una dependencia, eliminar, reportes con gráfico y exportación a CSV, datos de ejemplo.
- Un ticket finalizado ya no admite cambios. Solo el técnico y el dueño del ticket ven teléfono y dirección.
- Si ya tenías una base de datos de una versión anterior, la app agrega sola la tabla `usuarios` y la columna `tickets.usuario_id` al arrancar.

## Ejecutar en local
```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py                   # http://localhost:5000
```
Técnico: entra en `/login` con `ADMIN_USER` / `ADMIN_PASSWORD` (por defecto `admin` / `admin123`). Clientes: `/registro`.

## Variables de entorno
| Variable | Descripción | Por defecto |
|---|---|---|
| `DATABASE_URL` | Conexión a la base de datos | SQLite local (`helpdesk.db`) |
| `SECRET_KEY` | Clave de sesión (obligatorio cambiarla y mantenerla fija; si cambia, se cierran las sesiones) | valor de desarrollo |
| `ADMIN_USER` / `ADMIN_PASSWORD` | Acceso al panel técnico | `admin` / `admin123` |
| `TZ_OFFSET` | Zona horaria respecto a UTC | `-5` |
| `PORT` | Puerto (lo suele definir la plataforma) | `5000` |

Ejemplos de `DATABASE_URL`:
- PostgreSQL: `postgresql://usuario:clave@host:5432/basedatos`
- MySQL / MariaDB: `mysql+pymysql://usuario:clave@host:3306/basedatos`

Las tablas se crean automáticamente al iniciar.

## Despliegue en Vercel
1. Sube este proyecto a un repositorio de GitHub (o usa `vercel` desde la carpeta).
2. En Vercel: **Add New → Project** → importa el repositorio (detecta Flask solo; no cambies nada del build).
3. Antes de desplegar (o después, y luego *Redeploy*), define las variables de entorno:
   `DATABASE_URL` (PostgreSQL, p. ej. Neon desde *Storage / Marketplace*), `SECRET_KEY`, `ADMIN_USER`, `ADMIN_PASSWORD`, `TZ_OFFSET=-5`.
4. Las tablas se crean solas en la primera visita.

Notas: los estáticos están en `public/static/`; SQLite no funciona en Vercel (disco de solo lectura), usa PostgreSQL.
