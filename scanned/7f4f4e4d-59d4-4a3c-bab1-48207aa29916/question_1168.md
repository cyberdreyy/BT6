# Q1168: version field is a constant in update-wallet.ts

## Question
Every envelope sets version: 1; can an attacker exploit the absence of a nonce or request id so two identical operations from updateWallet(): signs {version:1 produce byte-identical signatures that are interchangeable?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Issue the same operation twice and compare signatures.
- Invariant to test: Envelopes must include a unique per-request nonce.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: issue the same updateWallet(): signs {version:1 operation twice and assert the signatures differ.
