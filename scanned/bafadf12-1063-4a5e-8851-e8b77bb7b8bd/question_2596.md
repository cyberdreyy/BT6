# Q2596: lending_pool_add_bank_permissionless: seeded add-pool path can create an attacker-favored authority surface [a-bank-seed-reused-across] [market-identity]

## Question
Can an unprivileged attacker use `lending_pool_add_bank_permissionless` with a bank seed reused across otherwise distinct candidate assets so `lending_pool_add_bank_permissionless` creates a bank/pool with an attacker-favored seed or authority surface, violating `permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only` and causing `High: live bank misconfiguration, mispricing, or durable user freeze`? Focus specifically on exact economic identity of the external market, mint, reserve, or validator relationship.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs` / `lending_pool_add_bank_permissionless`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: a bank seed reused across otherwise distinct candidate assets
- Exploit idea: Probe bank-seed and authority derivation assumptions on add-pool-like paths and whether auth bypass would let attacker choose unsafe derivations. Focus specifically on exact economic identity of the external market, mint, reserve, or validator relationship.
- Invariant to test: permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only
- Expected Immunefi impact: High: live bank misconfiguration, mispricing, or durable user freeze
- Fast validation: Generate edge-case seeds and assert canonical derivations are domain-separated, collision-free, and fully bound to the intended object. Supply same-type accounts from a sibling market or pool and assert add-pool rejects every non-canonical identity.
