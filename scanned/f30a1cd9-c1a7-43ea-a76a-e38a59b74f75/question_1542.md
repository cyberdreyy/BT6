# Q1542: code_verifier survives a failed exchange in EmailApi.ts

## Question
Does EmailApi.sendCode leave privy:code_verifier and privy:state_code in storage when the exchange throws, so a later attacker-triggered callback can replay them?

## Target
- File/function: [src/client/auth/EmailApi.ts](src/client/auth/EmailApi.ts) - EmailApi.sendCode, loginWithCode, linkWithCode, updateEmail, unlink
- Entrypoint: privy.auth.email.loginWithCode(email, code)
- Attacker controls: email string, code string, mode, opts.embedded, call ordering/repetition
- Exploit idea: Fail the authenticate request, then deliver a crafted callback that reuses the still-stored state/verifier pair.
- Invariant to test: PKCE material must be deleted on every terminal outcome, not only on success.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make the exchange reject and assert both storage keys are absent afterwards.
