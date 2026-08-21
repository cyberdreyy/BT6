# Q3696: update body replaces the entire signer list in create.ts

## Question
updateWallet writes additional_signers as a whole array; can an attacker submit a full replacement through create(): WalletCreate with optional privy-idempotency-key header that removes the user's other signers while adding theirs?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Submit a replacement list containing only the attacker signer.
- Invariant to test: Signer mutations must be expressed as scoped add/remove operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a replacement list to create(): WalletCreate with optional privy-idempotency-key header and assert only the requested delta is applied.
