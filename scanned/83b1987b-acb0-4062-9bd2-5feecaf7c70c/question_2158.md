# Q2158: raw-sign hashes anything in update-wallet.ts

## Question
rawSign forwards the caller's params to WalletRawSign under the same signed envelope; can an attacker use updateWallet(): signs {version:1 to obtain a raw signature over a transaction digest that the wallet would never sign through a typed path?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Submit a transaction hash through the raw-sign entrypoint.
- Invariant to test: Raw-hash signing must require an explicit, distinct user approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: submit a transaction digest through updateWallet(): signs {version:1 and assert an approval gate is enforced.
