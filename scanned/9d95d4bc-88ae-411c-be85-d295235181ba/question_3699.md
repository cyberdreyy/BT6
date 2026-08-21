# Q3699: update body replaces the entire signer list in types.ts

## Question
updateWallet writes additional_signers as a whole array; can an attacker submit a full replacement through PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') that removes the user's other signers while adding theirs?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Submit a replacement list containing only the attacker signer.
- Invariant to test: Signer mutations must be expressed as scoped add/remove operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a replacement list to PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') and assert only the requested delta is applied.
