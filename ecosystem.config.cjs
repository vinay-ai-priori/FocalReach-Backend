/**
 * PM2 process file for the Focal Reach backend stack (production).
 *
 * Processes (mirrors run.bat, minus dev-only --reload):
 *   focalreach-api          FastAPI via uvicorn, 4 worker processes
 *   focalreach-heavy-worker Celery "heavy" queue (website.analyze, qualification.run —
 *                           the scraper/Chromium tasks), solo pool, concurrency 1 so a
 *                           small instance never runs two scrapes at once
 *   focalreach-light-worker Celery "light" queue (pollers, dispatch, fast LLM calls),
 *                           threads pool, concurrency 6
 *   focalreach-beat         Celery beat — drives scheduled outreach dispatch; without
 *                           it, bookings are made but emails never send
 *
 * Prereqs: backend/.env configured (Neon DATABASE_URL, REDIS_URL, OPENAI_API_KEY),
 * schema migrated (alembic upgrade head), and the venv created:
 *   python -m venv .venv && .venv/bin/pip install -r requirements.txt
 *
 * Usage (from this directory):
 *   pm2 start ecosystem.config.cjs
 *   pm2 save && pm2 startup     # survive reboots
 */

const path = require("path");

const BACKEND = __dirname;
// Windows venv keeps executables in Scripts/, POSIX in bin/.
const PYTHON =
  process.platform === "win32"
    ? path.join(BACKEND, ".venv", "Scripts", "python.exe")
    : path.join(BACKEND, ".venv", "bin", "python");

const CELERY_APP = "app.core.celery_app.celery_app";

const common = {
  cwd: BACKEND,
  interpreter: "none", // PM2 must not wrap the venv python in Node
  autorestart: true,
  max_restarts: 10,
  restart_delay: 5000,
  time: true, // timestamps in pm2 logs
  env: {
    PYTHONUNBUFFERED: "1",
  },
};

module.exports = {
  apps: [
    {
      ...common,
      name: "focalreach-api",
      script: PYTHON,
      args: "-m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4",
      // uvicorn manages its own 4 worker processes — PM2 runs a single supervisor.
      instances: 1,
      kill_timeout: 15000, // let in-flight requests finish on reload/stop
    },
    {
      ...common,
      name: "focalreach-heavy-worker",
      script: PYTHON,
      args: `-m celery -A ${CELERY_APP} worker --loglevel=info --pool=solo --concurrency=1 -Q heavy -n heavy@%h`,
      instances: 1,
      kill_timeout: 150000, // a mid-flight scrape/qualification wave gets time to finish
      max_memory_restart: "1500M", // Chromium leaks are recycled instead of OOM-killing the box
    },
    {
      ...common,
      name: "focalreach-light-worker",
      script: PYTHON,
      args: `-m celery -A ${CELERY_APP} worker --loglevel=info --pool=threads --concurrency=6 -Q light -n light@%h`,
      instances: 1,
      kill_timeout: 60000,
    },
    {
      ...common,
      name: "focalreach-beat",
      script: PYTHON,
      args: `-m celery -A ${CELERY_APP} beat --loglevel=info`,
      instances: 1, // NEVER scale beat above 1 — duplicate beats double-fire every schedule
      kill_timeout: 10000,
    },
  ],
};
