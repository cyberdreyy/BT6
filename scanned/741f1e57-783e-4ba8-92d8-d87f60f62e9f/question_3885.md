# Q3885: recovery access token reused across providers in RecoveryApi.ts

## Question
google-drive and icloud recovery both accept recoveryAccessToken; can an attacker present a token from one provider in the other's branch through RecoveryApi.getRecoveryKeyMaterial?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Call recovery with a mismatched provider/token pair.
- Invariant to test: Recovery tokens must be validated against the provider the wallet is bound to.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: cross provider and token in RecoveryApi.getRecoveryKeyMaterial and assert rejection.
