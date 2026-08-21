# Q2160: raw-sign hashes anything in sign-wallet-request.ts

## Question
rawSign forwards the caller's params to WalletRawSign under the same signed envelope; can an attacker use SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) to obtain a raw signature over a transaction digest that the wallet would never sign through a typed path?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Submit a transaction hash through the raw-sign entrypoint.
- Invariant to test: Raw-hash signing must require an explicit, distinct user approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: submit a transaction digest through SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert an approval gate is enforced.
