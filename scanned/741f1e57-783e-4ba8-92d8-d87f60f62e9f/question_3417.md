# Q3417: is_new_user drives privileged app UI in SiweApi.ts

## Question
SiweApi.init merges is_new_user and oauth_tokens from the authenticate response into the returned user; can an attacker influence those fields to make the integrating app treat an existing account as newly created?

## Target
- File/function: [src/client/auth/SiweApi.ts](src/client/auth/SiweApi.ts) - SiweApi.init, loginWithSiwe, linkWithSiwe, unlinkWallet, generateSiweMessage
- Entrypoint: privy.auth.siwe.init(wallet, domain, uri) then loginWithSiwe(signature, wallet, message)
- Attacker controls: domain, uri, chainId, walletClientType, connectorType, full message override, signature
- Exploit idea: Return is_new_user true for an existing account and observe the merged user object.
- Invariant to test: Merged response flags must be derived from the authenticated result, not accepted blindly.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert SiweApi.init derives is_new_user from the server result for the same subject as the stored token.
