# Q3962: no expiry in the signed statement in EmailApi.ts

## Question
The statement built in src/client/auth/EmailApi.ts carries Issued At but no expiration; can an attacker replay a signature captured months earlier through EmailApi.sendCode?

## Target
- File/function: [src/client/auth/EmailApi.ts](src/client/auth/EmailApi.ts) - EmailApi.sendCode, loginWithCode, linkWithCode, updateEmail, unlink
- Entrypoint: privy.auth.email.loginWithCode(email, code)
- Attacker controls: email string, code string, mode, opts.embedded, call ordering/repetition
- Exploit idea: Sign once, store the message and signature, replay after a long delay.
- Invariant to test: Authentication statements must carry an expiry the client enforces.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert EmailApi.sendCode rejects a message whose Issued At is older than a short window.
