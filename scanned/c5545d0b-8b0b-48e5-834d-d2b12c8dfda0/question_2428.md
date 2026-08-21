# Q2428: challenge not bound to the stored options in SiwsApi.ts

## Question
Does SiwsApi.fetchNonce accept a challenge argument supplied by the caller rather than the one returned by the matching options call, enabling replay of a previously captured assertion?

## Target
- File/function: [src/client/auth/SiwsApi.ts](src/client/auth/SiwsApi.ts) - SiwsApi.fetchNonce, login, link, unlink
- Entrypoint: privy.auth.siws.login({message, signature, walletClientType, connectorType, mode})
- Attacker controls: message string, signature, wallet metadata, nonce reuse
- Exploit idea: Call the options method, discard the challenge, and log in with an older challenge plus its captured assertion.
- Invariant to test: The challenge submitted must be the one issued for this ceremony.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a stale challenge to SiwsApi.fetchNonce and assert it is rejected.
