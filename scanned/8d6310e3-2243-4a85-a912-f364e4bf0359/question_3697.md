# Q3697: update body replaces the entire signer list in get-wallet.ts

## Question
updateWallet writes additional_signers as a whole array; can an attacker submit a full replacement through getWallet(): WalletGet by wallet_id that removes the user's other signers while adding theirs?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Submit a replacement list containing only the attacker signer.
- Invariant to test: Signer mutations must be expressed as scoped add/remove operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a replacement list to getWallet(): WalletGet by wallet_id and assert only the requested delta is applied.
