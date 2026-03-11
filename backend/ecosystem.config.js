module.exports = {
  apps: [{
    name: "engageflow-backend",
    script: "/usr/bin/bash",
    args: ["-c", "cd /root/.openclaw/workspace/engageflow/backend && ./venv/bin/uvicorn app:app --host 0.0.0.0 --port 3103"],
    cwd: "/root/.openclaw/workspace/engageflow/backend",
    env: {
      JOINER_ENABLED: "false",
      JOINER_MODE: "simulate"
    }
  }]
};
