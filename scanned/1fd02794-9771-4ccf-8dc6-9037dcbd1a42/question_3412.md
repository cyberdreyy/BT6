# Q3412: is_new_user drives privileged app UI in EmailApi.ts

## Question
EmailApi.sendCode merges is_new_user and oauth_tokens from the authenticate response into the returned user; can an attacker influence those fields to make the integrating app treat an existing account as newly created?

## Target
- File/function: [src/client/auth/EmailApi.ts](src/client/auth/EmailApi.ts) - EmailApi.sendCode, loginWithCode, linkWithCode, updateEmail, unlink
- Entrypoint: privy.auth.email.loginWithCode(email, code)
- Attacker controls: email string, code string, mode, opts.embedded, call ordering/repetition
- Exploit idea: Return is_new_user true for an existing account and observe the merged user object.
- Invariant to test: Merged response flags must be derived from the authenticated result, not accepted blindly.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert EmailApi.sendCode derives is_new_user from the server result for the same subject as the stored token.
