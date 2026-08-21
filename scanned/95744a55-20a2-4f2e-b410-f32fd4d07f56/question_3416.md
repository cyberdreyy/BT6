# Q3416: is_new_user drives privileged app UI in PasskeyApi.ts

## Question
PasskeyApi.generateAuthenticationOptions merges is_new_user and oauth_tokens from the authenticate response into the returned user; can an attacker influence those fields to make the integrating app treat an existing account as newly created?

## Target
- File/function: [src/client/auth/PasskeyApi.ts](src/client/auth/PasskeyApi.ts) - PasskeyApi.generateAuthenticationOptions, loginWithPasskey, signupWithPasskey, linkWithPasskey, _transformAuthenticationResponseToSnakeCase
- Entrypoint: privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty)
- Attacker controls: relyingParty string, challenge, authenticator response object fields
- Exploit idea: Return is_new_user true for an existing account and observe the merged user object.
- Invariant to test: Merged response flags must be derived from the authenticated result, not accepted blindly.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert PasskeyApi.generateAuthenticationOptions derives is_new_user from the server result for the same subject as the stored token.
