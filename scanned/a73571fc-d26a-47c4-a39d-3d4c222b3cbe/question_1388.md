# Q1388: app id is the only signed header in update-wallet.ts

## Question
The signed headers contain privy-app-id and expiry only; can an attacker exploit unsigned but security-relevant headers (client id, ca-id, native app identifier) in updateWallet(): signs {version:1 to change server-side treatment of the request?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Vary the unsigned headers and observe server-side behaviour differences.
- Invariant to test: All authorization-relevant headers must be signed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert updateWallet(): signs {version:1 signs every header it sends that affects authorization.
