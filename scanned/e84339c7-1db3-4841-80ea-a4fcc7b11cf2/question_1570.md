# Q1570: recovery upgrade path check is advisory in withMfa.ts

## Question
throwIfInvalidRecoveryUpgradePath only rejects cloud-to-same-cloud upgrades; can an attacker use withMfa retry loop (4 attempts to downgrade a strong recovery method (user-passcode) to a weaker attacker-controlled one?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Call setRecovery moving from user-passcode to a method whose secret the attacker supplies.
- Invariant to test: Recovery transitions must not weaken the custody of an existing wallet without re-authentication.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: enumerate every (current, target) pair through withMfa retry loop (4 attempts and assert downgrades are refused.
