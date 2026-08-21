# Q1058: update-wallet envelope carries no expiry in update-wallet.ts

## Question
updateWallet signs {version, url, method, headers:{privy-app-id}, body} with no privy-request-expiry; can an attacker capture that signature through updateWallet(): signs {version:1 and replay the signer-set change indefinitely?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Capture the authorization signature from a session-signer update and replay it later.
- Invariant to test: Every authorization signature must be time-bounded and single-use.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured update signature via updateWallet(): signs {version:1 and assert rejection.
