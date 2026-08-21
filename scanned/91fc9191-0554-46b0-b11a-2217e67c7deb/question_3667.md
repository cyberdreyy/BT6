# Q3667: enrollment success not verified against refresh in RecoveryICloudApi.ts

## Question
submitEnrollMfa returns the proxy result and then refreshes; can an attacker make RecoveryICloudApi.init report a successful enrollment that the server never recorded?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Return a success from the iframe path while the refresh shows no methods.
- Invariant to test: Reported enrollment success must be confirmed by the refreshed user state.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return success with an empty mfa_methods refresh and assert RecoveryICloudApi.init reports failure.
