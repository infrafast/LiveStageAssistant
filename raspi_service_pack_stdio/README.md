# Live Stage Assistant Raspberry Pi stdio service

run locally to test:
```bash
ASSISTANT_AUTO_ENV_DIR=./raspi_service_pack_stdio .venv/bin/python voice_assistant/agent.py --env-file auto
```

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

The installer copies `.env.online` and `.env.offline` to `/etc/livestageassistant/` and makes that directory writable by `pi`. The service sets `ASSISTANT_AUTO_ENV_DIR=/etc/livestageassistant`, so `auto` loads `.env.online` when internet is reachable and `.env.offline` when it is not. Both profiles point `MCP_CONFIG` to `raspi_service_pack_stdio/mcp_servers_raspi.json`, so the stdio MCP servers keep using repo-relative paths to the sibling MCP folders. The web Config -> MCP Servers routing editor writes to the active `MCP_CONFIG`; keep that JSON writable by `pi` if you move it outside the repo.

## Prerequisites

Install system packages used by backend audio capture/playback and cloud TTS MP3 playback:

```bash
sudo apt update
sudo apt install portaudio19-dev alsa-utils ffmpeg espeak espeak-ng libespeak1 libespeak-ng1
```

`alsa-utils` provides tools such as `aplay` for ALSA device checks, and `ffmpeg` is required for backend OpenAI/ElevenLabs MP3 TTS playback. It is also used to decode browser WebM/Opus audio before optional Resemblyzer speaker recognition. Without `ffmpeg`, backend cloud TTS can fall back to `pyttsx3` and browser-side speaker recognition may return `unknown`.

Backend TTS volume, backend microphone monitoring volume, and pan are software controls applied before PyAudio writes to the selected output device. In the web Config -> Audio In/Out section, `BACKEND_AUDIO_OUTPUT_PAN=0.00` keeps backend audio centered; `-1.00` sends it left and `1.00` sends it right. `BACKEND_AUDIO_MONITOR_MODE=off` keeps the current behavior, `passthrough` forwards backend microphone chunks to backend output while capture is running, and `rejected` replays only wake-word-rejected phrases. `BACKEND_AUDIO_MONITOR_VOLUME=1.00` controls that microphone monitoring path separately from TTS gain.

Backend microphone monitoring is **experimental** and has not yet been validated on Raspberry Pi hardware. Leave it on `off` for normal operation. When testing, use headphones and a low monitoring volume first; ALSA/PipeWire device contention, feedback, latency, and channel/rate compatibility may require platform-specific adjustments.

On Raspberry Pi with PipeWire, USB audio devices may be busy when opened directly through ALSA `hw:` or `plughw:`. Prefer selecting the `pipewire` backend output in the web UI, then set the USB sink as PipeWire's default output:

```bash
wpctl status
wpctl set-default <USB_SINK_ID>
```

The installed system service runs as `pi` but outside the interactive login shell. To let PyAudio see the same PipeWire/Pulse devices as an interactive `pi` terminal, the service exports:

```ini
XDG_RUNTIME_DIR=/run/user/1000
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
```

The installer also enables linger for `pi` so `/run/user/1000` can exist at boot before an interactive login. If `pipewire` is visible when running the agent manually but missing when running it as a service, reinstall the service pack or run:

```bash
sudo loginctl enable-linger pi
sudo systemctl daemon-reload
sudo systemctl restart livestageassistant
```

Config -> User interface lists every top-level `assets/*.wav` file for the thinking sound and startup loader. Selecting a loader WAV sets `STARTUP_LOADER_SOUND_ENABLED=true` and `STARTUP_LOADER_SOUND_FILE`; selecting **No startup loader sound** disables it. The loader remains backend-only, loops on the selected backend output while the assistant starts, stops just before the startup announcement, and uses `BACKEND_TTS_VOLUME` plus `BACKEND_AUDIO_OUTPUT_PAN`. Its selector and Play button are disabled for Browser or Silent TTS output. Set `COMMAND_ACK_SOUND_ENABLED=true` from the same panel to play `assets/ring.wav` after the LLM/MCP response is ready and just before TTS generation/playback begins. The acknowledgement sound uses the selected speech side: backend PyAudio for backend TTS, or browser audio for browser TTS. It does not stop the thinking sound.

On the Raspberry Pi, install the assistant dependencies with the repository install script. It installs the speaker-recognition stack too, using the same CPU Torch then Resemblyzer-without-dependencies order as desktop/server installs:

```bash
cd /home/pi/LiveStageAssistant
uv venv
./scripts/install.sh
```

Do not install `LiveStageAssistant[speaker]` directly on Raspberry Pi: depending on the current PyTorch wheels, pip may try to install CUDA/NVIDIA packages that are useless and heavy on Pi. Use `./scripts/install.sh`, then verify that no CUDA/NVIDIA packages were pulled:

```bash
source .venv/bin/activate
pip freeze | grep -Ei "nvidia|cuda|triton"
```

The last command should print nothing. If Resemblyzer still pulls CUDA packages on your platform, remove them and install Resemblyzer without dependencies after Torch and its required CPU dependencies are already present. Keep `scipy` and `platformdirs` explicit because the speaker backend validates `scipy.special.loggamma` and `platformdirs.user_cache_dir` at runtime:

```bash
python -m pip uninstall -y resemblyzer torch triton cuda-toolkit cuda-bindings cuda-pathfinder \
  nvidia-cusparselt-cu13 nvidia-nvtx nvidia-nvshmem-cu13 nvidia-nvjitlink nvidia-nccl-cu13 \
  nvidia-curand nvidia-cufile nvidia-cuda-runtime nvidia-cuda-nvrtc nvidia-cuda-cupti \
  nvidia-cusparse nvidia-cufft nvidia-cublas nvidia-cusolver nvidia-cudnn-cu13
python -m pip cache purge
python -m pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install --no-cache-dir --force-reinstall "scipy==1.12.0" "platformdirs>=4,<5"
python -m pip install --no-cache-dir resemblyzer --no-deps
```

If speaker recognition logs an error like `cannot import name 'loggamma' from 'scipy.special'`, the venv has an incompatible or incomplete SciPy wheel. `pip install --upgrade scipy` may still say `Requirement already satisfied`, so first test the exact import, then force-reinstall a known compatible SciPy wheel inside the assistant venv:

```bash
cd /home/pi/LiveStageAssistant
source .venv/bin/activate

python - <<'PY'
import scipy
print("scipy", scipy.__version__, scipy.__file__)
from scipy.special import loggamma
print("scipy.special.loggamma OK")
PY

python -m pip uninstall -y scipy
python -m pip install --no-cache-dir --force-reinstall "scipy==1.12.0"

python - <<'PY'
import scipy
print("scipy", scipy.__version__, scipy.__file__)
from scipy.special import loggamma
print("scipy.special.loggamma OK")
PY

sudo systemctl restart livestageassistant
```

If `scipy==1.12.0` is not available for your Raspberry Pi/Python combination, try another piwheels-provided CPU wheel such as `scipy==1.11.4`. The important check is that `from scipy.special import loggamma` works before restarting the service.

If speaker recognition logs `module 'platformdirs' has no attribute 'user_cache_dir'`, the venv has an incompatible or shadowed `platformdirs` module. Force-reinstall it and verify the attribute before restarting:

```bash
cd /home/pi/LiveStageAssistant
source .venv/bin/activate

python -m pip uninstall -y platformdirs
python -m pip install --no-cache-dir --force-reinstall "platformdirs>=4,<5"

python - <<'PY'
import platformdirs
print("platformdirs", platformdirs.__version__, platformdirs.__file__)
print("user_cache_dir", platformdirs.user_cache_dir("LiveStageAssistant"))
PY

sudo systemctl restart livestageassistant
```

For reliable recognition, provide up to three WAV samples per speaker from the web Config -> STT/TTS -> Voice Activity Detection -> Speaker profiles section. Each sample can be uploaded as a WAV file or captured from the browser microphone; browser microphone capture requires HTTPS or `localhost`, records up to 10 seconds, offers preview/retry, and then uploads to the same slot. Profile slots use a fixed filename convention: profile 1 reads `profil1_1.wav`, `profil1_2.wav`, and `profil1_3.wav`; profile 2 reads `profil2_1.wav`, `profil2_2.wav`, and `profil2_3.wav`; and so on under `SPEAKER_PROFILES_DIR` (`data/speaker_profiles` locally, `/data/speaker_profiles` in Docker). Older single-file names such as `profil1.wav` are no longer used. A profile becomes usable as soon as one sample embedding exists. A good starting strategy is sample 1: clean reference voice, sample 2: clean voice with another phrase, sample 3: live-condition voice from the same microphone path used live when possible. Every upload computes a per-sample embedding next to the WAV (`profil1_1.npy`, `profil1_2.npy`, etc.), and runtime uses the best sample score for each profile. Avoid music, crowd noise, reverb, clipping, long silences, and multiple voices.

For XMSeries-MCP speaker-aware first-person commands such as `mon retour`, edit `XMS_SPEAKER_MAP` in the web Config -> MCP Servers -> mixer -> Server env options box. The value is shown as JSON in the UI and saved into `mcp_servers_raspi.json` as an environment variable string for the stdio MCP server:

```json
{
  "XMS_SPEAKER_MAP": {
    "laurent": { "bus": "Laurent", "channel": "Talk Laurent" }
  }
}
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
nano /etc/livestageassistant/.env.online
nano /etc/livestageassistant/.env.offline
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

For a local repository test before installing the service, use the pack directory directly:

```bash
cd /home/pi/LiveStageAssistant
ASSISTANT_AUTO_ENV_DIR=./raspi_service_pack_stdio .venv/bin/python voice_assistant/agent.py --env-file auto
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

## HTTPS for browser microphone STT

Browser microphone access is blocked by modern browsers when the web monitor is opened from another machine over plain `http://`. This means browser STT can fail even when backend STT works. Serve the monitor through HTTPS so the browser treats the page as a secure context and allows microphone permission.

Keep Live Stage Assistant listening locally on the Raspberry Pi:

```env
WEB_MONITOR_HOST=127.0.0.1
WEB_MONITOR_PORT=8765
WEB_PASSWORD=
```

Set `WEB_PASSWORD=your-password` if you want the web monitor to ask for a password before opening. Leave it empty to disable web login.

Install Caddy:

```bash
sudo apt update
sudo apt install caddy
```

Create or edit `/etc/caddy/Caddyfile` with your HTTPS hostname:

```caddyfile
:8443 {
    tls internal
    reverse_proxy 127.0.0.1:8765
}
```

Reload Caddy:

```bash
sudo systemctl reload caddy
```

Open the monitor with:

```text
https://<localip>:8765
```

Caddy can automatically issue and renew certificates when the hostname resolves to the Raspberry Pi and the HTTP/HTTPS validation ports are reachable. After HTTPS is working, the browser microphone selector and browser STT controls should become usable after you grant microphone permission.
