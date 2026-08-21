# Q1658: redirect target chosen by caller in SiwsApi.ts

## Question
Can an attacker pass a redirect_to value into SiwsApi.fetchNonce that sends the authorization code to an origin they control while the SDK still treats the resulting callback as trusted?

## Target
- File/function: [src/client/auth/SiwsApi.ts](src/client/auth/SiwsApi.ts) - SiwsApi.fetchNonce, login, link, unlink
- Entrypoint: privy.auth.siws.login({message, signature, walletClientType, connectorType, mode})
- Attacker controls: message string, signature, wallet metadata, nonce reuse
- Exploit idea: Call generateURL with an attacker origin and complete loginWithCode with the code delivered there.
- Invariant to test: src/client/auth/SiwsApi.ts must not accept a redirect target that is unrelated to the app's configured origins.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call SiwsApi.fetchNonce with an off-origin redirect_to and assert the request is rejected client-side.
