# Q0838: expiry chosen by the client clock in update-wallet.ts

## Question
The expiry is derived from the local clock; can an attacker skew the clock so updateWallet(): signs {version:1 mints an envelope valid far into the future?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Advance the system clock and inspect the generated expiry.
- Invariant to test: Request validity must not be extendable by the client.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: mock Date.now far ahead and assert updateWallet(): signs {version:1 clamps the expiry.
