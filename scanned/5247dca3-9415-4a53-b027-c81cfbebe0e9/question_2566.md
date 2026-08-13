# Q2566: lending_pool_add_bank_permissionless: pool-add path binds a foreign external market or mint [precomputed-derived-addresses-supplied-by] [market-identity]

## Question
Can an unprivileged attacker invoke `lending_pool_add_bank_permissionless` with precomputed derived addresses supplied by the attacker so `lending_pool_add_bank_permissionless` adds or prepares a bank against a foreign market/mint context, violating `permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only` and causing `High: live bank misconfiguration, mispricing, or durable user freeze`? Focus specifically on exact economic identity of the external market, mint, reserve, or validator relationship.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs` / `lending_pool_add_bank_permissionless`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: precomputed derived addresses supplied by the attacker
- Exploit idea: Even role-bound add-pool paths must tightly bind every external market, mint, and vault if auth is ever bypassed. Focus specifically on exact economic identity of the external market, mint, reserve, or validator relationship.
- Invariant to test: permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only
- Expected Immunefi impact: High: live bank misconfiguration, mispricing, or durable user freeze
- Fast validation: Attempt mixed market/mint inputs and assert the path rejects unless every external dependency matches the canonical target. Supply same-type accounts from a sibling market or pool and assert add-pool rejects every non-canonical identity.
