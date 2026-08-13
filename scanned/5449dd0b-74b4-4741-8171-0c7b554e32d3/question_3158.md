# Q3158: lending_pool_configure_bank_oracle: price-setting path updates configuration but not its dependent invariants [same-slot-oracle-config-attempt] [identity-vs-shape]

## Question
Can an unprivileged attacker make `lending_pool_configure_bank_oracle` reach `lending_pool_configure_bank_oracle` with same-slot oracle-config attempt before a public price-cache pulse so protected price config updates without updating dependent invariants, violating `oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended` and causing `Critical: attacker-installed pricing enabling theft or insolvency`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs` / `lending_pool_configure_bank_oracle`
- Entrypoint: `lending_pool_configure_bank_oracle`
- Attacker controls: same-slot oracle-config attempt before a public price-cache pulse
- Exploit idea: Audit whether mode changes also maintain required cache, limit, or operational-state invariants. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended
- Expected Immunefi impact: Critical: attacker-installed pricing enabling theft or insolvency
- Fast validation: After the controlled config mutation, run all dependent invariants and assert no user path becomes inconsistently permitted. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
