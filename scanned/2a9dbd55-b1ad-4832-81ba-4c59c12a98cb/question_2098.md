# Q2098: nonce reuse across two logins in SiwsApi.ts

## Question
Can an attacker reuse a nonce previously issued by init()/fetchNonce for the same address to authenticate a second time from a different device or context?

## Target
- File/function: [src/client/auth/SiwsApi.ts](src/client/auth/SiwsApi.ts) - SiwsApi.fetchNonce, login, link, unlink
- Entrypoint: privy.auth.siws.login({message, signature, walletClientType, connectorType, mode})
- Attacker controls: message string, signature, wallet metadata, nonce reuse
- Exploit idea: Capture the nonce, complete a login, then replay message and signature.
- Invariant to test: Each issued nonce must be single-use for SiwsApi.fetchNonce.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: complete a login and then replay the same message/signature and assert the second attempt fails.
