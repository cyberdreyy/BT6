# Q2208: relying party string controlled by caller in SiwsApi.ts

## Question
In src/client/auth/SiwsApi.ts, is the relying party supplied by the caller and echoed into the ceremony, letting an attacker start a credential ceremony scoped to a different origin than the one they occupy?

## Target
- File/function: [src/client/auth/SiwsApi.ts](src/client/auth/SiwsApi.ts) - SiwsApi.fetchNonce, login, link, unlink
- Entrypoint: privy.auth.siws.login({message, signature, walletClientType, connectorType, mode})
- Attacker controls: message string, signature, wallet metadata, nonce reuse
- Exploit idea: Call SiwsApi.fetchNonce with a relying party that is not the current origin and observe the options returned.
- Invariant to test: The relying party used by SiwsApi.fetchNonce must be derived from the app's configured origin.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call SiwsApi.fetchNonce with a foreign relying party and assert the SDK refuses.
