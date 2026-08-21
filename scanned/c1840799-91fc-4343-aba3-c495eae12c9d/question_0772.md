# Q0772: unlink of the last identity leaves an orphan session in EmailApi.ts

## Question
Can an attacker call EmailApi.sendCode's unlink path to remove the only linked account that authenticated the session, then keep using the still-valid stored tokens on the now-unreachable account?

## Target
- File/function: [src/client/auth/EmailApi.ts](src/client/auth/EmailApi.ts) - EmailApi.sendCode, loginWithCode, linkWithCode, updateEmail, unlink
- Entrypoint: privy.auth.email.loginWithCode(email, code)
- Attacker controls: email string, code string, mode, opts.embedded, call ordering/repetition
- Exploit idea: Unlink the sole identity, then call privy.getAccessToken() and a wallet operation with the retained credentials.
- Invariant to test: Removing the last authentication factor must invalidate the local session credentials.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: unlink the last account then assert Session.destroyLocalState ran and getAccessToken returns null.
