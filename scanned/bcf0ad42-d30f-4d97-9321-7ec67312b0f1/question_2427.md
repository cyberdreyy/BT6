# Q2427: challenge not bound to the stored options in SiweApi.ts

## Question
Does SiweApi.init accept a challenge argument supplied by the caller rather than the one returned by the matching options call, enabling replay of a previously captured assertion?

## Target
- File/function: [src/client/auth/SiweApi.ts](src/client/auth/SiweApi.ts) - SiweApi.init, loginWithSiwe, linkWithSiwe, unlinkWallet, generateSiweMessage
- Entrypoint: privy.auth.siwe.init(wallet, domain, uri) then loginWithSiwe(signature, wallet, message)
- Attacker controls: domain, uri, chainId, walletClientType, connectorType, full message override, signature
- Exploit idea: Call the options method, discard the challenge, and log in with an older challenge plus its captured assertion.
- Invariant to test: The challenge submitted must be the one issued for this ceremony.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a stale challenge to SiweApi.init and assert it is rejected.
