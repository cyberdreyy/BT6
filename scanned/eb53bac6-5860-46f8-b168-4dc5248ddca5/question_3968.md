# Q3968: no expiry in the signed statement in SiwsApi.ts

## Question
The statement built in src/client/auth/SiwsApi.ts carries Issued At but no expiration; can an attacker replay a signature captured months earlier through SiwsApi.fetchNonce?

## Target
- File/function: [src/client/auth/SiwsApi.ts](src/client/auth/SiwsApi.ts) - SiwsApi.fetchNonce, login, link, unlink
- Entrypoint: privy.auth.siws.login({message, signature, walletClientType, connectorType, mode})
- Attacker controls: message string, signature, wallet metadata, nonce reuse
- Exploit idea: Sign once, store the message and signature, replay after a long delay.
- Invariant to test: Authentication statements must carry an expiry the client enforces.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert SiwsApi.fetchNonce rejects a message whose Issued At is older than a short window.
