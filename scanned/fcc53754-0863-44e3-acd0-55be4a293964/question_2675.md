# Q2675: recovery method chosen from the account object in RecoveryApi.ts

## Question
EmbeddedWalletApi._load selects the recovery branch from wallet.recovery_method; can an attacker supply a wallet object with a different recovery_method so RecoveryApi.getRecoveryKeyMaterial attempts recovery with material they control?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Pass a hand-built wallet object into the provider/recovery path.
- Invariant to test: Recovery branch selection must use server-confirmed account data.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a crafted wallet object to RecoveryApi.getRecoveryKeyMaterial and assert the account is re-validated against the session user.
