# Security policy

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Use GitHub's **Report a vulnerability** flow in the repository Security tab.

Include the affected commit, reproduction steps, expected impact, and any proposed mitigation. Do not include real Access assertions, relay tokens, browser cookies, transcripts, or microphone recordings.

## Supported version

Security fixes target the latest commit on `main`.

## Deployment assumptions

Tars Voice is designed for a private deployment:

- Cloudflare Access authenticates browser traffic.
- The gateway independently validates signed Access assertions.
- The gateway, Whisper server, and Tars relay bind to loopback or an equivalently isolated container network.
- Relay credentials are supplied only through local environment files or a secret manager.
- Consequential-action policy remains in Tars, not in the browser or voice gateway.

A public repository does **not** make a deployed voice endpoint public. Operators must configure their own domain, Access application, relay token, and owner identity.
