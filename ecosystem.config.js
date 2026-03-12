module.exports = {
  apps: [
    {
      name: "skoolindy-backend",
      script: "./backend/start-skoolindy.sh",
      cwd: "/root/.openclaw/workspace/skoolindy/backend",
      watch: false,
      autorestart: true,
      max_restarts: 20,
      min_uptime: "10s",
      env: {
        NODE_ENV: "production"
      }
    },
    {
      name: "skoolindy-frontend",
      script: "bash",
      args: "-c 'npm run dev'",
      cwd: "/root/.openclaw/workspace/skoolindy/frontend",
      watch: false,
      autorestart: true,
      max_restarts: 10
    },
    {
      name: "skoolindy-joiner",
      script: "./joiner/backend/server.js",
      cwd: "/root/.openclaw/workspace/skoolindy",
      watch: false,
      autorestart: true,
      max_restarts: 10,
      interpreter: "node"
    }
  ]
};
