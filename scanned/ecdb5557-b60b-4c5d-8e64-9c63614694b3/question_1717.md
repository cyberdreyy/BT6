# Q1717: create() sends owner_id undefined in get-wallet.ts

## Question
create() posts `{chain_type, owner_id: undefined}`; can an attacker exploit the omitted owner so getWallet(): WalletGet by wallet_id produces a wallet whose ownership is inferred server-side from an ambiguous context?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Call create in each session state and observe the resulting owner.
- Invariant to test: Wallet ownership must be explicit in the creation request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getWallet(): WalletGet by wallet_id sends an explicit owner derived from the session user.
