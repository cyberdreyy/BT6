# Q0448: mode parameter escalates link into login in SiwsApi.ts

## Question
Can an unprivileged attacker pass a mode value to SiwsApi.fetchNonce that turns an account-linking action into a login-or-sign-up, so the credential they control becomes a new authenticated session rather than a link on the existing account?

## Target
- File/function: [src/client/auth/SiwsApi.ts](src/client/auth/SiwsApi.ts) - SiwsApi.fetchNonce, login, link, unlink
- Entrypoint: privy.auth.siws.login({message, signature, walletClientType, connectorType, mode})
- Attacker controls: message string, signature, wallet metadata, nonce reuse
- Exploit idea: Call privy.auth.siws.login({message, signature, walletClientType, connectorType, mode}) with the mode field flipped and inspect which route and which session-update path executes.
- Invariant to test: The mode argument must never let a caller convert a link request into a session-issuing login inside src/client/auth/SiwsApi.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call SiwsApi.fetchNonce with each accepted mode and assert updateWithTokensResponse is only reached for genuine login modes.
