# Q2421: challenge not bound to the stored options in AuthApi.ts

## Question
Does AuthApi.logout accept a challenge argument supplied by the caller rather than the one returned by the matching options call, enabling replay of a previously captured assertion?

## Target
- File/function: [src/client/auth/AuthApi.ts](src/client/auth/AuthApi.ts) - AuthApi.logout, AuthApi.email/phone/oauth/siwe/siws/passkey sub-APIs
- Entrypoint: privy.auth.logout(), privy.auth.<method>
- Attacker controls: logout timing, userId passed to mfa.clearMfa, concurrent login calls
- Exploit idea: Call the options method, discard the challenge, and log in with an older challenge plus its captured assertion.
- Invariant to test: The challenge submitted must be the one issued for this ceremony.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a stale challenge to AuthApi.logout and assert it is rejected.
