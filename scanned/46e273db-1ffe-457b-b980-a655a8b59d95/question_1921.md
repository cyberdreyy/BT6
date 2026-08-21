# Q1921: idempotency collision merges two creates in generateWalletIdempotencyKey.ts

## Question
create() forwards privy-idempotency-key; can an attacker cause two logically distinct wallet creations to collapse into one through generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex, so the app believes it provisioned a wallet it does not own?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Issue two creates with the same derived key under different contexts.
- Invariant to test: Distinct creation intents must not share an idempotency key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run two generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex creates with the same key and assert the second is rejected, not silently aliased.
