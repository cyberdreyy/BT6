# Q1651: redirect target chosen by caller in AuthApi.ts

## Question
Can an attacker pass a redirect_to value into AuthApi.logout that sends the authorization code to an origin they control while the SDK still treats the resulting callback as trusted?

## Target
- File/function: [src/client/auth/AuthApi.ts](src/client/auth/AuthApi.ts) - AuthApi.logout, AuthApi.email/phone/oauth/siwe/siws/passkey sub-APIs
- Entrypoint: privy.auth.logout(), privy.auth.<method>
- Attacker controls: logout timing, userId passed to mfa.clearMfa, concurrent login calls
- Exploit idea: Call generateURL with an attacker origin and complete loginWithCode with the code delivered there.
- Invariant to test: src/client/auth/AuthApi.ts must not accept a redirect target that is unrelated to the app's configured origins.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call AuthApi.logout with an off-origin redirect_to and assert the request is rejected client-side.
