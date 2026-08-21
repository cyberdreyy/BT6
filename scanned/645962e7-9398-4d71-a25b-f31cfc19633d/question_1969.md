# Q1969: delegated wallets carry a wallet index in DelegatedWalletsApi.ts

## Question
The delegation payload includes walletIndex from the account object; can an attacker submit an index through DelegatedWalletsApi.revoke (WalletsRevoke that points at a different wallet than the address?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Submit an address and index that disagree.
- Invariant to test: Address and index in the delegation payload must be verified consistent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a disagreeing pair to DelegatedWalletsApi.revoke (WalletsRevoke and assert rejection.
