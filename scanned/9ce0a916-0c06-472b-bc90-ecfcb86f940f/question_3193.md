# Q3193: lending_pool_configure_bank_oracle: oracle-config path reuses stale auxiliary state from a previous mode [a-config-call-mixing-setup] [downstream-cache]

## Question
Can an unprivileged attacker route `lending_pool_configure_bank_oracle` through `lending_pool_configure_bank_oracle` with a config call mixing setup variants and oracle keys so the bank reuses stale auxiliary state from a previous oracle mode, violating `oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended` and causing `Critical: attacker-installed pricing enabling theft or insolvency`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs` / `lending_pool_configure_bank_oracle`
- Entrypoint: `lending_pool_configure_bank_oracle`
- Attacker controls: a config call mixing setup variants and oracle keys
- Exploit idea: Mode transitions must not leave old cached assumptions live if a public bug can trigger them. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended
- Expected Immunefi impact: Critical: attacker-installed pricing enabling theft or insolvency
- Fast validation: Switch modes in adversarial sequences and assert downstream pricing always reflects the final mode only. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
