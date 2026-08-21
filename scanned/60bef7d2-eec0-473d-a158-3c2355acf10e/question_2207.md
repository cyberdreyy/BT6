# Q2207: relying party string controlled by caller in SiweApi.ts

## Question
In src/client/auth/SiweApi.ts, is the relying party supplied by the caller and echoed into the ceremony, letting an attacker start a credential ceremony scoped to a different origin than the one they occupy?

## Target
- File/function: [src/client/auth/SiweApi.ts](src/client/auth/SiweApi.ts) - SiweApi.init, loginWithSiwe, linkWithSiwe, unlinkWallet, generateSiweMessage
- Entrypoint: privy.auth.siwe.init(wallet, domain, uri) then loginWithSiwe(signature, wallet, message)
- Attacker controls: domain, uri, chainId, walletClientType, connectorType, full message override, signature
- Exploit idea: Call SiweApi.init with a relying party that is not the current origin and observe the options returned.
- Invariant to test: The relying party used by SiweApi.init must be derived from the app's configured origin.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call SiweApi.init with a foreign relying party and assert the SDK refuses.
