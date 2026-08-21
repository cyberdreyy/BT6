# Q0728: 30 minute expiry window in update-wallet.ts

## Question
The expiry header is Date.now()+1800000 and the only check is the client's own `Date.now() > expiry`; can an attacker capture an authorization signature from updateWallet(): signs {version:1 and replay it for the remainder of that window?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Capture a signed request and replay it minutes later.
- Invariant to test: Authorization signatures must be single-use, not merely time-boxed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured updateWallet(): signs {version:1 request and assert the second use fails.
