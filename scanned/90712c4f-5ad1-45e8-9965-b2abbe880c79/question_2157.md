# Q2157: raw-sign hashes anything in get-wallet.ts

## Question
rawSign forwards the caller's params to WalletRawSign under the same signed envelope; can an attacker use getWallet(): WalletGet by wallet_id to obtain a raw signature over a transaction digest that the wallet would never sign through a typed path?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Submit a transaction hash through the raw-sign entrypoint.
- Invariant to test: Raw-hash signing must require an explicit, distinct user approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: submit a transaction digest through getWallet(): WalletGet by wallet_id and assert an approval gate is enforced.
