# Q2677: recovery method chosen from the account object in RecoveryICloudApi.ts

## Question
EmbeddedWalletApi._load selects the recovery branch from wallet.recovery_method; can an attacker supply a wallet object with a different recovery_method so RecoveryICloudApi.init attempts recovery with material they control?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Pass a hand-built wallet object into the provider/recovery path.
- Invariant to test: Recovery branch selection must use server-confirmed account data.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a crafted wallet object to RecoveryICloudApi.init and assert the account is re-validated against the session user.
