# Q3742: prepared state kept on the API instance in EmailApi.ts

## Question
src/client/auth/EmailApi.ts caches prepared state (wallet, prepared message) on the API object; can an attacker exploit a second init overwriting that state so a signature prepared for one address is submitted for another?

## Target
- File/function: [src/client/auth/EmailApi.ts](src/client/auth/EmailApi.ts) - EmailApi.sendCode, loginWithCode, linkWithCode, updateEmail, unlink
- Entrypoint: privy.auth.email.loginWithCode(email, code)
- Attacker controls: email string, code string, mode, opts.embedded, call ordering/repetition
- Exploit idea: Call init for address A, then init for address B, then complete the login with A's signature.
- Invariant to test: Prepared authentication state must be bound to the address it was created for.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run two init calls and assert the login rejects a signature that does not match the latest prepared address.
