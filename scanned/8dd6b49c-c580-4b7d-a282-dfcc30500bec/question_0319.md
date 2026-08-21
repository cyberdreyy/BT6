# Q0319: delegation consent payload built client-side in DelegatedWalletsApi.ts

## Question
delegateWallet assembles rootWallet and delegatedWallets objects and hands them to the iframe consent step; can an attacker craft that payload through DelegatedWalletsApi.revoke (WalletsRevoke so the consent screen describes one wallet while another is delegated?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Submit mismatched root and delegated entries.
- Invariant to test: The consent payload must be derived from validated account data and be exactly what is executed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched payload to DelegatedWalletsApi.revoke (WalletsRevoke and assert refusal.
