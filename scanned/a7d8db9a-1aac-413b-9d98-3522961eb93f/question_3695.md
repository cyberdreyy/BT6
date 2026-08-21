# Q3695: update body replaces the entire signer list in raw-sign.ts

## Question
updateWallet writes additional_signers as a whole array; can an attacker submit a full replacement through rawSign(): same expiry-signed envelope for WalletRawSign that removes the user's other signers while adding theirs?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Submit a replacement list containing only the attacker signer.
- Invariant to test: Signer mutations must be expressed as scoped add/remove operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a replacement list to rawSign(): same expiry-signed envelope for WalletRawSign and assert only the requested delta is applied.
