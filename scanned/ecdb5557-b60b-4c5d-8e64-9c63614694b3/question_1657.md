# Q1657: redirect target chosen by caller in SiweApi.ts

## Question
Can an attacker pass a redirect_to value into SiweApi.init that sends the authorization code to an origin they control while the SDK still treats the resulting callback as trusted?

## Target
- File/function: [src/client/auth/SiweApi.ts](src/client/auth/SiweApi.ts) - SiweApi.init, loginWithSiwe, linkWithSiwe, unlinkWallet, generateSiweMessage
- Entrypoint: privy.auth.siwe.init(wallet, domain, uri) then loginWithSiwe(signature, wallet, message)
- Attacker controls: domain, uri, chainId, walletClientType, connectorType, full message override, signature
- Exploit idea: Call generateURL with an attacker origin and complete loginWithCode with the code delivered there.
- Invariant to test: src/client/auth/SiweApi.ts must not accept a redirect target that is unrelated to the app's configured origins.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call SiweApi.init with an off-origin redirect_to and assert the request is rejected client-side.
