# Q0429: ownership check by address equality in DelegatedWalletsApi.ts

## Question
delegateWallet finds the target with `chain_type === n && address === t`; can an attacker submit a checksummed or padded address through DelegatedWalletsApi.revoke (WalletsRevoke that fails or passes this check incorrectly?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Pass mixed-case and padded variants of an owned address.
- Invariant to test: Ownership comparison must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test address forms through DelegatedWalletsApi.revoke (WalletsRevoke.
