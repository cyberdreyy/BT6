# Q2571: lending_pool_add_bank_permissionless: pool-add path binds a foreign external market or mint [cross-group-candidate-contexts-sharing] [config-derivation]

## Question
Can an unprivileged attacker invoke `lending_pool_add_bank_permissionless` with cross-group candidate contexts sharing the same mint interface so `lending_pool_add_bank_permissionless` adds or prepares a bank against a foreign market/mint context, violating `permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only` and causing `High: live bank misconfiguration, mispricing, or durable user freeze`? Focus specifically on derived bank config and authority surfaces created by pool-add helpers.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs` / `lending_pool_add_bank_permissionless`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: cross-group candidate contexts sharing the same mint interface
- Exploit idea: Even role-bound add-pool paths must tightly bind every external market, mint, and vault if auth is ever bypassed. Focus specifically on derived bank config and authority surfaces created by pool-add helpers.
- Invariant to test: permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only
- Expected Immunefi impact: High: live bank misconfiguration, mispricing, or durable user freeze
- Fast validation: Attempt mixed market/mint inputs and assert the path rejects unless every external dependency matches the canonical target. Derive the resulting config under adversarial inputs and assert every safety-critical field and authority remains canonical.
