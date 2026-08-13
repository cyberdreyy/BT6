# Q3172: lending_pool_configure_bank_oracle: oracle-config path can retarget future permissionless cache writes [two-banks-whose-oracle-like] [identity-vs-shape]

## Question
Can an unprivileged attacker use `lending_pool_configure_bank_oracle` with two banks whose oracle-like accounts are type-compatible so `lending_pool_configure_bank_oracle` retargets future permissionless cache writes to attacker-selected pricing context, violating `oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended` and leading to `Critical: attacker-installed pricing enabling theft or insolvency`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs` / `lending_pool_configure_bank_oracle`
- Entrypoint: `lending_pool_configure_bank_oracle`
- Attacker controls: two banks whose oracle-like accounts are type-compatible
- Exploit idea: Check whether protected config fields later consumed by permissionless cache-refresh paths can be corrupted by a public auth/binding bug. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended
- Expected Immunefi impact: Critical: attacker-installed pricing enabling theft or insolvency
- Fast validation: Mutate the config under attacker conditions, then try the permissionless refresher and assert it still cannot write from attacker-selected sources. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
