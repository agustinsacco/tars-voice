# Setup

This guide installs the gateway from source. Whisper, Piper, Tars, Cloudflare Tunnel, and Cloudflare Access remain independently managed components.

## 1. Clone and install

```bash
git clone https://github.com/agustinsacco/tars-voice.git ~/.local/share/tars-voice
cd ~/.local/share/tars-voice
make setup
```

## 2. Install speech models

### Whisper

Build `whisper.cpp` with the acceleration backend appropriate for the host. For Vulkan:

```bash
git clone --depth 1 https://github.com/ggml-org/whisper.cpp vendor/whisper.cpp
cmake -S vendor/whisper.cpp -B vendor/whisper.cpp/build-vulkan -DGGML_VULKAN=ON
cmake --build vendor/whisper.cpp/build-vulkan --config Release -j"$(nproc)"
```

Download a supported GGML model using the upstream model script. Model files are intentionally excluded from this repository.

### Piper

Install or download a Piper voice with both files present:

```text
models/piper/en_US-lessac-medium.onnx
models/piper/en_US-lessac-medium.onnx.json
```

Set `VOICE_PIPER_MODEL` to the ONNX path.

## 3. Configure the gateway

```bash
install -d -m 0700 ~/.config/tars-voice
install -m 0600 .env.example ~/.config/tars-voice/voice.env
$EDITOR ~/.config/tars-voice/voice.env
```

Generate the relay token locally with a cryptographically secure tool, for example:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Put the same token into the loopback Tars relay configuration. Never paste a production token into an issue, commit, or chat.

## 4. Configure Cloudflare

1. Create a Tunnel route for the chosen hostname to `http://127.0.0.1:8788`.
2. Create a self-hosted Access application for that hostname.
3. Restrict its policy to the intended identity.
4. Copy the Access application audience (`AUD`) and team domain into `voice.env`.
5. Set `VOICE_PUBLIC_ORIGIN` to the exact HTTPS origin.

The gateway validates Cloudflare's `Cf-Access-Jwt-Assertion` rather than trusting the tunnel alone.

## 5. Configure the Tars relay

The relay must listen on loopback and implement [`docs/tars-relay.md`](tars-relay.md). Tars remains responsible for memory, tool policy, and consequential-action confirmation.

## 6. Install user services

The committed service files assume this layout:

```text
~/.local/share/tars-voice
~/.config/tars-voice/voice.env
```

Install and enable them:

```bash
install -d ~/.config/systemd/user
install -m 0644 deploy/systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now tars-voice-whisper tars-voice-gateway
```

Edit the service paths first if using a different layout.

## 7. Validate

```bash
make validate
curl -fsS http://127.0.0.1:8788/healthz
curl -fsS http://127.0.0.1:8788/readyz | python3 -m json.tool
```

Then test microphone capture and playback from a real desktop browser and phone. Headless checks do not constitute end-to-end voice acceptance.
