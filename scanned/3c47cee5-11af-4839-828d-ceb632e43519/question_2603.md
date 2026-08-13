# Q2603: lending_pool_add_bank_permissionless: seeded add-pool path can create an attacker-favored authority surface [cross-group-candidate-contexts-sharing] [config-derivation]

## Question
Can an unprivileged attacker use `lending_pool_add_bank_permissionless` with cross-group candidate contexts sharing the same mint interface so `lending_pool_add_bank_permissionless` creates a bank/pool with an attacker-favored seed or authority surface, violating `permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only` and causing `High: live bank misconfiguration, mispricing, or durable user freeze`? Focus specifically on derived bank config and authority surfaces created by pool-add helpers.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs` / `lending_pool_add_bank_permissionless`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: cross-group candidate contexts sharing the same mint interface
- Exploit idea: Probe bank-seed and authority derivation assumptions on add-pool-like paths and whether auth bypass would let attacker choose unsafe derivations. Focus specifically on derived bank config and authority surfaces created by pool-add helpers.
- Invariant to test: permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only
- Expected Immunefi impact: High: live bank misconfiguration, mispricing, or durable user freeze
- Fast validation: Generate edge-case seeds and assert canonical derivations are domain-separated, collision-free, and fully bound to the intended object. Derive the resulting config under adversarial inputs and assert every safety-critical field and authority remains canonical.
