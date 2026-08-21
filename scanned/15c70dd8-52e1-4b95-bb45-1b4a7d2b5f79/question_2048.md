# Q2048: wallet_id accepted from the caller in update-wallet.ts

## Question
getWallet/updateWallet/rawSign take wallet_id from the caller; can an attacker pass a wallet id that is not theirs through updateWallet(): signs {version:1 and have the SDK build and sign an envelope for it?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Call the operation with a foreign wallet id.
- Invariant to test: Wallet ids must be validated against the authenticated user's linked accounts before signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign wallet id to updateWallet(): signs {version:1 and assert refusal before signing.
