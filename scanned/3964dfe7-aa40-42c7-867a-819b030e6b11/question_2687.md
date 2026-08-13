# Q2687: lending_pool_add_bank_permissionless: pool-add path validates type but not exact economic identity [candidate-accounts-from-sibling-staked] [config-derivation]

## Question
Can an unprivileged attacker invoke `lending_pool_add_bank_permissionless` with candidate accounts from sibling staked pools with the same owner program so `lending_pool_add_bank_permissionless` validates only object type/shape, not exact economic identity, violating `permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only` and causing `High: live bank misconfiguration, mispricing, or durable user freeze`? Focus specifically on derived bank config and authority surfaces created by pool-add helpers.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs` / `lending_pool_add_bank_permissionless`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: candidate accounts from sibling staked pools with the same owner program
- Exploit idea: Look for external accounts that pass type checks but belong to another market, mint, or vault family. Focus specifically on derived bank config and authority surfaces created by pool-add helpers.
- Invariant to test: permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only
- Expected Immunefi impact: High: live bank misconfiguration, mispricing, or durable user freeze
- Fast validation: Use same-type accounts from a parallel market and assert add-pool rejects every non-canonical economic identity. Derive the resulting config under adversarial inputs and assert every safety-critical field and authority remains canonical.
