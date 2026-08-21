# Q3961: no expiry in the signed statement in AuthApi.ts

## Question
The statement built in src/client/auth/AuthApi.ts carries Issued At but no expiration; can an attacker replay a signature captured months earlier through AuthApi.logout?

## Target
- File/function: [src/client/auth/AuthApi.ts](src/client/auth/AuthApi.ts) - AuthApi.logout, AuthApi.email/phone/oauth/siwe/siws/passkey sub-APIs
- Entrypoint: privy.auth.logout(), privy.auth.<method>
- Attacker controls: logout timing, userId passed to mfa.clearMfa, concurrent login calls
- Exploit idea: Sign once, store the message and signature, replay after a long delay.
- Invariant to test: Authentication statements must carry an expiry the client enforces.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert AuthApi.logout rejects a message whose Issued At is older than a short window.
