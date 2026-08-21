# Q3087: logout does not await server revocation in SiweApi.ts

## Question
AuthApi.logout swallows the Logout request error before clearing local state; can an attacker abuse this so the refresh token stays valid server-side while the app reports a completed logout?

## Target
- File/function: [src/client/auth/SiweApi.ts](src/client/auth/SiweApi.ts) - SiweApi.init, loginWithSiwe, linkWithSiwe, unlinkWallet, generateSiweMessage
- Entrypoint: privy.auth.siwe.init(wallet, domain, uri) then loginWithSiwe(signature, wallet, message)
- Attacker controls: domain, uri, chainId, walletClientType, connectorType, full message override, signature
- Exploit idea: Make the Logout route fail and then reuse the previously captured refresh token.
- Invariant to test: A completed logout must guarantee server-side revocation or surface the failure.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: fail the Logout route, assert SiweApi.init surfaces the failure instead of resolving silently.
