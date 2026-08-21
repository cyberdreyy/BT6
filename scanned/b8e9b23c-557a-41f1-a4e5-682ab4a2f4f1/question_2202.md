# Q2202: relying party string controlled by caller in EmailApi.ts

## Question
In src/client/auth/EmailApi.ts, is the relying party supplied by the caller and echoed into the ceremony, letting an attacker start a credential ceremony scoped to a different origin than the one they occupy?

## Target
- File/function: [src/client/auth/EmailApi.ts](src/client/auth/EmailApi.ts) - EmailApi.sendCode, loginWithCode, linkWithCode, updateEmail, unlink
- Entrypoint: privy.auth.email.loginWithCode(email, code)
- Attacker controls: email string, code string, mode, opts.embedded, call ordering/repetition
- Exploit idea: Call EmailApi.sendCode with a relying party that is not the current origin and observe the options returned.
- Invariant to test: The relying party used by EmailApi.sendCode must be derived from the app's configured origin.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call EmailApi.sendCode with a foreign relying party and assert the SDK refuses.
