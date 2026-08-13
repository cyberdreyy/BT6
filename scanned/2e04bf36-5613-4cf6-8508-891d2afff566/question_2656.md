# Q2656: lending_pool_add_bank_permissionless: clone/add helper can duplicate sensitive config into a hostile destination [candidate-accounts-from-sibling-staked] [market-identity]

## Question
Can an unprivileged attacker use `lending_pool_add_bank_permissionless` with candidate accounts from sibling staked pools with the same owner program so `lending_pool_add_bank_permissionless` duplicates sensitive pool config into a hostile destination, violating `permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only` and causing `High: live bank misconfiguration, mispricing, or durable user freeze`? Focus specifically on exact economic identity of the external market, mint, reserve, or validator relationship.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs` / `lending_pool_add_bank_permissionless`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: candidate accounts from sibling staked pools with the same owner program
- Exploit idea: Audit clone/add helper paths that copy bank or emode parameters and must bind source/destination precisely. Focus specifically on exact economic identity of the external market, mint, reserve, or validator relationship.
- Invariant to test: permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only
- Expected Immunefi impact: High: live bank misconfiguration, mispricing, or durable user freeze
- Fast validation: Attempt cross-bank clone/add with mismatched destinations and assert no protected config is copied into attacker-selected objects. Supply same-type accounts from a sibling market or pool and assert add-pool rejects every non-canonical identity.
