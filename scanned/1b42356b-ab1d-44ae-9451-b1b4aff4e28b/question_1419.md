# Q1419: signers array unvalidated in DelegatedWalletsApi.ts

## Question
addSessionSigners concatenates the caller's signers onto the existing list; can an attacker add a signer key they control through DelegatedWalletsApi.revoke (WalletsRevoke so future server-side signing is possible without the user?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Pass an attacker signer entry and inspect the resulting wallet record.
- Invariant to test: Every added signer must be user-approved and validated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an arbitrary signer to DelegatedWalletsApi.revoke (WalletsRevoke and assert an approval gate.
