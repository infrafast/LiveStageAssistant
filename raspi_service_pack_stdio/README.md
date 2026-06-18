# Live Stage Assistant Raspberry Pi stdio service

run locally to test:
ASSISTANT_AUTO_ENV_DIR=./raspi_service_pack_stdio .venv/bin/python voice_assistant/agent.py --env-file auto

This pack installs Live Stage Assistant as a Raspberry Pi `systemd` service using the local stdio MCP profile.

It assumes these folders are siblings under `/home/pi`:

```text
/home/pi/LiveStageAssistant
/home/pi/XMSeries-MCP
/home/pi/QLCPlus-MCP
```

The service starts the Python agent in automatic online/offline profile mode:

```bash
/home/pi/LiveStageAssistant/.venv/bin/python voice_assistant/agent.py --env-file auto
```

The installer copies `.env.online` and `.env.offline` to `/etc/livestageassistant/`. The service sets `ASSISTANT_AUTO_ENV_DIR=/etc/livestageassistant`, so `auto` loads `.env.online` when internet is reachable and `.env.offline` when it is not. Both profiles point `MCP_CONFIG` to `raspi_service_pack_stdio/mcp_servers_raspi.json`, so the stdio MCP servers keep using repo-relative paths to the sibling MCP folders.

## Prerequisites

On the Raspberry Pi, install the assistant dependencies first:

```bash
cd /home/pi/LiveStageAssistant
uv venv
uv pip install -e .
```

Build the MCP servers before starting the assistant:

```bash
cd /home/pi/XMSeries-MCP
npm ci
npm run build

cd /home/pi/QLCPlus-MCP
npm ci
npm run build
```

Node.js must be `20.20.0` or newer. Node 22 LTS is recommended.

## Install

```bash
cd /home/pi/LiveStageAssistant/raspi_service_pack_stdio
chmod +x install_livestageassistant_service.sh livestageassistant
./install_livestageassistant_service.sh
```

Review the copied runtime configs:

```bash
sudo nano /etc/livestageassistant/.env.online
sudo nano /etc/livestageassistant/.env.offline
```

## Commands

Start automatically at boot:

```bash
livestageassistant auto
```

Start, stop, restart, and inspect:

```bash
livestageassistant start
livestageassistant stop
livestageassistant restart
livestageassistant status
livestageassistant logs
```

Test the same auto profile mode from a foreground terminal:

```bash
livestageassistant run-auto
```

If you invoke the agent directly, pass the same auto profile directory used by systemd:

```bash
cd /home/pi/LiveStageAssistant
ASSISTANT_AUTO_ENV_DIR=/etc/livestageassistant .venv/bin/python voice_assistant/agent.py --env-file auto
```

Disable boot auto-start:

```bash
livestageassistant noauto
```

Check state:

```bash
livestageassistant last-state
livestageassistant health
livestageassistant test-remote
```

Edit the active service environment:

```bash
livestageassistant config
livestageassistant restart
```
