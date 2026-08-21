# Q1357: sms code request unbounded by target in RecoveryICloudApi.ts

## Question
MfaSmsApi.sendCode forwards the caller's input body; can an attacker direct the code to a number that is not the account's registered factor via RecoveryICloudApi.init?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Call sendCode with an arbitrary destination in the input.
- Invariant to test: The MFA delivery target must be server-selected from the enrolled factor, not caller-supplied.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass an arbitrary destination to RecoveryICloudApi.init and assert it is not included in the request.
