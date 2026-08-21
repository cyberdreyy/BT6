# Q3038: failure between sign and send loses atomicity in update-wallet.ts

## Question
If fetchPrivyRoute throws after signing, the signature remains valid; can an attacker force that failure in updateWallet(): signs {version:1 and then reuse the signature at a moment of their choosing?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Abort the request post-signature and replay it later.
- Invariant to test: An unused authorization signature must be invalidated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: abort after signing in updateWallet(): signs {version:1 and assert the signature cannot be reused.
