// PM2 process definition: `pm2 start ecosystem.config.js`
const path = require("path");

const python =
  process.platform === "win32"
    ? path.join(__dirname, ".venv", "Scripts", "python.exe")
    : path.join(__dirname, ".venv", "bin", "python");

module.exports = {
  apps: [
    {
      name: "ai-agency-network",
      cwd: __dirname,
      script: "main.py",
      args: "serve --concurrency 2", // dashboard + API + worker; use "run" for a headless worker
      interpreter: python,
      autorestart: true,
      restart_delay: 5000,
      exp_backoff_restart_delay: 1000,
      max_restarts: 50,
      kill_timeout: 35000, // > worker shutdown_grace, so in-flight jobs can finish
      env: { PYTHONUNBUFFERED: "1", PYTHONUTF8: "1" },
      out_file: path.join(__dirname, "logs", "pm2-out.log"),
      error_file: path.join(__dirname, "logs", "pm2-error.log"),
      merge_logs: true,
      time: true,
    },
  ],
};
