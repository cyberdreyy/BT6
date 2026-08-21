# Q0287: wallet_id lives only in the URL in get-wallet.ts

## Question
The signed envelope includes the compiled url but the body omits wallet_id; can an attacker exploit URL/body separation in getWallet(): WalletGet by wallet_id so a signature produced for one wallet path is presented for another?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Compare envelopes for two wallet ids and test whether the server-visible binding is only positional.
- Invariant to test: Wallet identity must be bound inside the signed body as well as the path.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getWallet(): WalletGet by wallet_id includes wallet_id in the signed payload.
