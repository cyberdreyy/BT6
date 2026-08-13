# Q2622: lending_pool_add_bank_permissionless: pool-add fee or metadata side effects can be redirected [replay-of-a-previously-valid] [market-identity]

## Question
Can an unprivileged attacker invoke `lending_pool_add_bank_permissionless` with replay of a previously valid add-permissionless layout under a new target so `lending_pool_add_bank_permissionless` redirects flat-fee or metadata side effects during pool creation, violating `permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only` and causing `High: live bank misconfiguration, mispricing, or durable user freeze`? Focus specifically on exact economic identity of the external market, mint, reserve, or validator relationship.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs` / `lending_pool_add_bank_permissionless`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: replay of a previously valid add-permissionless layout under a new target
- Exploit idea: Add-pool paths often transfer setup fees and initialize metadata; both should stay bound to canonical destinations/objects. Focus specifically on exact economic identity of the external market, mint, reserve, or validator relationship.
- Invariant to test: permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only
- Expected Immunefi impact: High: live bank misconfiguration, mispricing, or durable user freeze
- Fast validation: Swap candidate destinations and metadata targets and assert add-pool cannot credit or initialize anything unvalidated. Supply same-type accounts from a sibling market or pool and assert add-pool rejects every non-canonical identity.
