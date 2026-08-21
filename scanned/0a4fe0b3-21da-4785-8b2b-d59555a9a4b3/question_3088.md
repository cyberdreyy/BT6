# Q3088: logout does not await server revocation in SiwsApi.ts

## Question
AuthApi.logout swallows the Logout request error before clearing local state; can an attacker abuse this so the refresh token stays valid server-side while the app reports a completed logout?

## Target
- File/function: [src/client/auth/SiwsApi.ts](src/client/auth/SiwsApi.ts) - SiwsApi.fetchNonce, login, link, unlink
- Entrypoint: privy.auth.siws.login({message, signature, walletClientType, connectorType, mode})
- Attacker controls: message string, signature, wallet metadata, nonce reuse
- Exploit idea: Make the Logout route fail and then reuse the previously captured refresh token.
- Invariant to test: A completed logout must guarantee server-side revocation or surface the failure.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: fail the Logout route, assert SiwsApi.fetchNonce surfaces the failure instead of resolving silently.
