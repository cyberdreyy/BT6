# Q0979: TEE wallets rejected only client-side in DelegatedWalletsApi.ts

## Question
delegateWallet and revokeWallets throw unsupported_wallet_type for unified (privy-v2) wallets based on the account object; can an attacker present an account through DelegatedWalletsApi.revoke (WalletsRevoke that evades the check and reaches the delegation path?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Pass an account missing the id field or with a different recovery_method.
- Invariant to test: Custody-type checks must use server-confirmed account records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass evasive account objects to DelegatedWalletsApi.revoke (WalletsRevoke and assert re-validation.
