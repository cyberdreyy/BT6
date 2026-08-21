# Q1936: getWallet result drives the next write in create.ts

## Question
getWallet returns additional_signers that addSessionSigners concatenates and writes back; can an attacker influence the read so create(): WalletCreate with optional privy-idempotency-key header writes back a signer set containing an entry they control?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Return an extra signer in the read response and observe it persisted by the subsequent write.
- Invariant to test: Read-modify-write of authorization state must validate every entry before rewriting.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: inject an extra signer into create(): WalletCreate with optional privy-idempotency-key header's read stub and assert it is not written back.
