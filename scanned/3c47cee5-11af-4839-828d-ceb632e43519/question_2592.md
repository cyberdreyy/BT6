# Q2592: lending_pool_add_bank_permissionless: pool-add configuration conversion drops a safety parameter [candidate-accounts-from-sibling-staked] [market-identity]

## Question
Can an unprivileged attacker make `lending_pool_add_bank_permissionless` reach `lending_pool_add_bank_permissionless` with candidate accounts from sibling staked pools with the same owner program so add-pool configuration conversion drops or corrupts a safety-critical parameter, violating `permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only` and leading to `High: live bank misconfiguration, mispricing, or durable user freeze`? Focus specifically on exact economic identity of the external market, mint, reserve, or validator relationship.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs` / `lending_pool_add_bank_permissionless`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: candidate accounts from sibling staked pools with the same owner program
- Exploit idea: Audit conversion from integration-specific config into bank config so no critical field silently defaults unsafely. Focus specifically on exact economic identity of the external market, mint, reserve, or validator relationship.
- Invariant to test: permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only
- Expected Immunefi impact: High: live bank misconfiguration, mispricing, or durable user freeze
- Fast validation: Compare derived bank config against expected canonical config for adversarial inputs and assert all safety-critical fields survive exactly. Supply same-type accounts from a sibling market or pool and assert add-pool rejects every non-canonical identity.
