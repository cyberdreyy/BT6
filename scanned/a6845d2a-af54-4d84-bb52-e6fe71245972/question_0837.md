# Q0837: expiry chosen by the client clock in get-wallet.ts

## Question
The expiry is derived from the local clock; can an attacker skew the clock so getWallet(): WalletGet by wallet_id mints an envelope valid far into the future?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Advance the system clock and inspect the generated expiry.
- Invariant to test: Request validity must not be extendable by the client.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: mock Date.now far ahead and assert getWallet(): WalletGet by wallet_id clamps the expiry.
