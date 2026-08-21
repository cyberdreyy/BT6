# Q0336: retryOn 409 replays the authenticate call in PasskeyApi.ts

## Question
PrivyInternal._fetch is configured with fetch-retry retries:3 and retryOn [408,409,425,500,502,503,504]; can an attacker make privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty) silently retry a non-idempotent authenticate/link POST so a one-time code or signature is consumed twice and a second session or link is created?

## Target
- File/function: [src/client/auth/PasskeyApi.ts](src/client/auth/PasskeyApi.ts) - PasskeyApi.generateAuthenticationOptions, loginWithPasskey, signupWithPasskey, linkWithPasskey, _transformAuthenticationResponseToSnakeCase
- Entrypoint: privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty)
- Attacker controls: relyingParty string, challenge, authenticator response object fields
- Exploit idea: Cause the first response to be a 409/425, then observe the SDK re-POSTing the identical body with the same one-time credential.
- Invariant to test: One-time authentication credentials submitted through PasskeyApi.generateAuthenticationOptions must be transmitted at most once per user action.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test with msw returning 409 then 200 for the authenticate route; assert the route handler is called once, not four times.
