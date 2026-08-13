# Q3145: lending_pool_configure_bank_oracle: staked-oracle transition can be used to brick live collateral paths [a-config-call-mixing-setup] [downstream-cache]

## Question
Can an unprivileged attacker invoke `lending_pool_configure_bank_oracle` with a config call mixing setup variants and oracle keys so `lending_pool_configure_bank_oracle` performs a staked-oracle transition that bricks or misprices live collateral paths, violating `oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended` and causing `Critical: attacker-installed pricing enabling theft or insolvency`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs` / `lending_pool_configure_bank_oracle`
- Entrypoint: `lending_pool_configure_bank_oracle`
- Attacker controls: a config call mixing setup variants and oracle keys
- Exploit idea: This is in scope when caused by a public bypass, not by an admin making a policy choice. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended
- Expected Immunefi impact: Critical: attacker-installed pricing enabling theft or insolvency
- Fast validation: Exercise the transition under attacker-controlled auth/binding attempts and assert it cannot affect live banks without the intended role and full validation. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
