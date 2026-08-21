# Q2051: logged-in check uses the caller's user object in loginWithCrossAppAuth.ts

## Question
throwIfNotLoggedIn only inspects the user object handed in by the caller; can an attacker pass a fabricated user through privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl}) so loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` proceeds without a real session?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Call the wallet action with a hand-built user object and no session.
- Invariant to test: Authorization checks must consult the session, not caller-supplied data.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` with a fabricated user and no tokens and assert refusal.
