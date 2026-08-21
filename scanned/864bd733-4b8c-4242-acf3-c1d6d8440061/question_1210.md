# Q1210: off is the default when unset in formatters.ts

## Question
formatWalletAddress (5 leading + 4 trailing chars) defaults createOnLogin to 'off' when the option is absent; can an attacker exploit an app that assumes provisioning happened so subsequent code uses an undefined wallet?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Log in with the option omitted and inspect downstream wallet usage.
- Invariant to test: Absent configuration must not silently disable a security-relevant provisioning step.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: omit the option and assert formatWalletAddress (5 leading + 4 trailing chars) reports the decision explicitly.
