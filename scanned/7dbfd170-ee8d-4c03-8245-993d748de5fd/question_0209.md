# Q0209: imported flag flips the root in DelegatedWalletsApi.ts

## Question
getRootWallet returns the account itself when imported is true; can an attacker present an account object with imported set through privy.delegated.revoke() so DelegatedWalletsApi.revoke (WalletsRevoke treats an arbitrary wallet as its own root?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Pass a crafted account with imported true.
- Invariant to test: Account flags used for delegation must come from server-confirmed state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass {imported:true} on a crafted account to DelegatedWalletsApi.revoke (WalletsRevoke and assert re-validation.
