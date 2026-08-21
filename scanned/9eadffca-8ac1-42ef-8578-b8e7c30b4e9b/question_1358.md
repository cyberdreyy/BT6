# Q1358: sms code request unbounded by target in index.ts

## Question
MfaSmsApi.sendCode forwards the caller's input body; can an attacker direct the code to a number that is not the account's registered factor via throwIfInvalidRecoveryUpgradePath?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Call sendCode with an arbitrary destination in the input.
- Invariant to test: The MFA delivery target must be server-selected from the enrolled factor, not caller-supplied.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass an arbitrary destination to throwIfInvalidRecoveryUpgradePath and assert it is not included in the request.
