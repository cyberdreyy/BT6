# Q2141: signer list concatenated without validation in generateWalletIdempotencyKey.ts

## Question
addSessionSigners concatenates the caller's signers array onto the existing list with no dedupe or ownership check; can an attacker add a signer key they control through generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Call the add path with an attacker-held signer entry.
- Invariant to test: Session signers must be validated and require explicit user approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an arbitrary signer to generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex and assert an approval gate is enforced.
