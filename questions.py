import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 30
# todo: the path from https://github.com/base/base
SOURCE_REPO = "base/base"
# todo: the name of the repository
REPO_NAME = "base"
run_number = os.environ.get('GITHUB_RUN_NUMBER') or os.environ.get('CI_PIPELINE_IID', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"

scope_files = [
    # =================================================================================
    # B20 native token precompiles: the value-moving surface any funded address calls
    # directly with ordinary calldata - mint, burn, transfer, approve, seize, permit
    # =================================================================================
    "crates/common/precompiles/src/b20_asset/precompile.rs",
    "crates/common/precompiles/src/b20_asset/dispatch.rs",
    "crates/common/precompiles/src/b20_asset/accounting.rs",
    "crates/common/precompiles/src/b20_asset/storage.rs",
    "crates/common/precompiles/src/b20_asset/versions.rs",
    "crates/common/precompiles/src/b20_asset/logic/v1.rs",
    "crates/common/precompiles/src/b20_asset/logic/v2.rs",
    "crates/common/precompiles/src/b20_asset/logic/interface.rs",
    "crates/common/precompiles/src/b20_asset/abi/v1.rs",
    "crates/common/precompiles/src/b20_asset/abi/v2.rs",
    "crates/common/precompiles/src/b20_stablecoin/precompile.rs",
    "crates/common/precompiles/src/b20_stablecoin/dispatch.rs",
    "crates/common/precompiles/src/b20_stablecoin/accounting.rs",
    "crates/common/precompiles/src/b20_stablecoin/storage.rs",
    "crates/common/precompiles/src/b20_stablecoin/versions.rs",
    "crates/common/precompiles/src/b20_stablecoin/logic/v1.rs",
    "crates/common/precompiles/src/b20_stablecoin/logic/v2.rs",
    "crates/common/precompiles/src/b20_stablecoin/logic/interface.rs",
    "crates/common/precompiles/src/b20_stablecoin/abi.rs",
    "crates/common/precompiles/src/b20_factory/precompile.rs",
    "crates/common/precompiles/src/b20_factory/dispatch.rs",
    "crates/common/precompiles/src/b20_factory/storage.rs",
    "crates/common/precompiles/src/b20_factory/variant.rs",
    "crates/common/precompiles/src/b20_factory/versions.rs",
    "crates/common/precompiles/src/b20_factory/logic/v1.rs",
    "crates/common/precompiles/src/b20_factory/logic/interface.rs",
    "crates/common/precompiles/src/b20_factory/abi/v1.rs",

    # =================================================================================
    # Precompile authorization, policy and shared token accounting: role checks,
    # pause vectors, EIP-2612 permits and the ABI decode every call passes through
    # =================================================================================
    "crates/common/precompiles/src/common/ops/guards.rs",
    "crates/common/precompiles/src/common/ops/roles.rs",
    "crates/common/precompiles/src/common/ops/permit.rs",
    "crates/common/precompiles/src/common/ops/non_zero_address.rs",
    "crates/common/precompiles/src/common/token.rs",
    "crates/common/precompiles/src/common/token_accounting.rs",
    "crates/common/precompiles/src/common/core_storage.rs",
    "crates/common/precompiles/src/common/pausable_feature.rs",
    "crates/common/precompiles/src/common/policy_type.rs",
    "crates/common/precompiles/src/common/abi_fingerprint.rs",
    "crates/common/precompiles/src/common/abi/b20_abi.rs",
    "crates/common/precompiles/src/common/abi/v1.rs",
    "crates/common/precompiles/src/common/abi/v2.rs",
    "crates/common/precompiles/src/policy/precompile.rs",
    "crates/common/precompiles/src/policy/dispatch.rs",
    "crates/common/precompiles/src/policy/accounting.rs",
    "crates/common/precompiles/src/policy/storage.rs",
    "crates/common/precompiles/src/policy/versions.rs",
    "crates/common/precompiles/src/policy/logic/v1.rs",
    "crates/common/precompiles/src/policy/logic/v2.rs",
    "crates/common/precompiles/src/policy/logic/interface.rs",
    "crates/common/precompiles/src/policy/abi/v1.rs",
    "crates/common/precompiles/src/policy/abi/v2.rs",
    "crates/common/precompiles/src/activation/precompile.rs",
    "crates/common/precompiles/src/activation/dispatch.rs",
    "crates/common/precompiles/src/activation/storage.rs",
    "crates/common/precompiles/src/activation/abi.rs",
    "crates/common/precompiles/src/nonce/precompile.rs",
    "crates/common/precompiles/src/nonce/dispatch.rs",
    "crates/common/precompiles/src/nonce/storage.rs",
    "crates/common/precompiles/src/nonce/abi.rs",
    "crates/common/precompiles/src/tx_context/precompile.rs",
    "crates/common/precompiles/src/tx_context/dispatch.rs",
    "crates/common/precompiles/src/tx_context/storage.rs",
    "crates/common/precompiles/src/tx_context/abi.rs",
    "crates/common/precompiles/src/lookup.rs",
    "crates/common/precompiles/src/provider.rs",
    "crates/common/precompiles/src/spec.rs",
    "crates/common/precompiles/src/observer.rs",
    "crates/common/precompiles/src/bn254_pair.rs",
    "crates/common/precompiles/src/bls12_381.rs",

    # =================================================================================
    # Precompile storage engine: slot derivation, packing and journaling that decides
    # whether one token's state can be reached through another's calldata
    # =================================================================================
    "crates/common/precompile-storage/src/storage_ctx.rs",
    "crates/common/precompile-storage/src/journal.rs",
    "crates/common/precompile-storage/src/packing.rs",
    "crates/common/precompile-storage/src/evm.rs",
    "crates/common/precompile-storage/src/neutral.rs",
    "crates/common/precompile-storage/src/provider.rs",
    "crates/common/precompile-storage/src/registration.rs",
    "crates/common/precompile-storage/src/types/slot.rs",
    "crates/common/precompile-storage/src/types/mapping.rs",
    "crates/common/precompile-storage/src/types/array.rs",
    "crates/common/precompile-storage/src/types/vec.rs",
    "crates/common/precompile-storage/src/types/set.rs",
    "crates/common/precompile-storage/src/types/bytes_like.rs",
    "crates/common/precompile-storage/src/types/primitives.rs",

    # =================================================================================
    # EIP-8130 sponsored and delegated transactions: authenticator resolution, scopes,
    # expiry, 2D nonces and sponsor fee accounting an attacker drives with one envelope
    # =================================================================================
    "crates/execution/eip8130/src/authorize.rs",
    "crates/execution/eip8130/src/verify.rs",
    "crates/execution/eip8130/src/validate.rs",
    "crates/execution/eip8130/src/apply.rs",
    "crates/execution/eip8130/src/scope.rs",
    "crates/execution/eip8130/src/fee.rs",
    "crates/execution/eip8130/src/signature.rs",
    "crates/execution/eip8130/src/transaction.rs",
    "crates/execution/eip8130/src/recovered.rs",
    "crates/execution/eip8130/src/resolved.rs",
    "crates/execution/eip8130/src/dispatch.rs",
    "crates/execution/eip8130/src/account_config.rs",
    "crates/execution/eip8130/src/outcome.rs",
    "crates/execution/eip8130/src/events.rs",
    "crates/execution/eip8130/src/config.rs",
    "crates/common/eip8130/src/intrinsic.rs",
    "crates/common/eip8130/src/nonce.rs",
    "crates/common/eip8130/src/schedule.rs",
    "crates/common/consensus/src/transaction/eip8130/tx.rs",
    "crates/common/consensus/src/transaction/eip8130/signed.rs",
    "crates/common/consensus/src/transaction/eip8130/call.rs",
    "crates/common/consensus/src/transaction/eip8130/coinbase_tip.rs",
    "crates/common/consensus/src/transaction/eip8130/account_changes.rs",
    "crates/common/consensus/src/transaction/eip8130/addresses.rs",
    "crates/execution/eip8130-rpc/src/eth.rs",
    "crates/execution/eip8130-rpc/src/estimate.rs",
    "crates/execution/eip8130-rpc/src/nonce_reader.rs",
    "crates/execution/eip8130-rpc/src/zenith_gate.rs",

    # =================================================================================
    # EVM execution core: the handler, spec gating and fee logic every transaction an
    # attacker broadcasts runs through on every node
    # =================================================================================
    "crates/common/evm/src/handler.rs",
    "crates/common/evm/src/evm.rs",
    "crates/common/evm/src/spec.rs",
    "crates/common/evm/src/factory.rs",
    "crates/common/evm/src/l1block.rs",
    "crates/common/evm/src/canyon.rs",
    "crates/common/evm/src/base_time.rs",
    "crates/common/evm/src/zenith.rs",
    "crates/common/evm/src/eip8130.rs",
    "crates/common/evm/src/eip8130_phase_statuses.rs",
    "crates/common/evm/src/receipt_builder.rs",
    "crates/common/evm/src/result.rs",
    "crates/common/evm/src/tx_env.rs",
    "crates/common/evm/src/executor/block_executor.rs",
    "crates/common/evm/src/executor/context.rs",
    "crates/common/evm/src/executor/factory.rs",
    "crates/common/evm/src/executor/result.rs",
    "crates/common/evm/src/transaction/core.rs",
    "crates/common/evm/src/transaction/deposit.rs",
    "crates/common/evm/src/transaction/eip8130.rs",
    "crates/common/evm/src/transaction/builder.rs",
    "crates/common/evm/src/transaction/traits.rs",
    "crates/common/evm/src/api/builder.rs",
    "crates/common/evm/src/api/default_ctx.rs",
    "crates/common/evm/src/api/exec.rs",
    "crates/common/evm/src/precompiles/mod.rs",
    "crates/common/evm2/src/handler.rs",
    "crates/common/evm2/src/executor.rs",
    "crates/common/evm2/src/transition.rs",
    "crates/common/evm2/src/nonce_manager.rs",
    "crates/common/evm2/src/precompiles.rs",
    "crates/common/evm2/src/registry.rs",
    "crates/common/evm2/src/spec.rs",
    "crates/common/evm2/src/transaction.rs",
    "crates/common/evm2/src/zenith.rs",
    "crates/execution/evm/src/build.rs",
    "crates/execution/evm/src/env.rs",
    "crates/execution/evm/src/l1.rs",
    "crates/execution/evm/src/receipts.rs",
    "crates/execution/evm/src/config.rs",

    # =================================================================================
    # Transaction and receipt consensus encoding: hashing, envelopes and RLP that
    # decide transaction identity and the receipts root every node must agree on
    # =================================================================================
    "crates/common/consensus/src/transaction/envelope.rs",
    "crates/common/consensus/src/transaction/canonical.rs",
    "crates/common/consensus/src/transaction/pooled.rs",
    "crates/common/consensus/src/transaction/typed.rs",
    "crates/common/consensus/src/transaction/tx_type.rs",
    "crates/common/consensus/src/transaction/deposit.rs",
    "crates/common/consensus/src/transaction/meta.rs",
    "crates/common/consensus/src/receipts/envelope.rs",
    "crates/common/consensus/src/receipts/receipt.rs",
    "crates/common/consensus/src/receipts/deposit.rs",
    "crates/common/consensus/src/receipts/eip8130.rs",
    "crates/common/consensus/src/block.rs",
    "crates/common/consensus/src/reth_compat.rs",
    "crates/common/consensus/src/predeploys.rs",
    "crates/common/consensus/src/extra/holocene.rs",
    "crates/common/consensus/src/extra/jovian.rs",
    "crates/common/consensus/src/extra/encoder.rs",
    "crates/common/rpc-types/src/transaction.rs",
    "crates/common/rpc-types/src/transaction/request.rs",
    "crates/common/rpc-types/src/receipt.rs",
    "crates/common/rpc-types/src/block.rs",
    "crates/common/rpc-types/src/log.rs",
    "crates/common/rpc-types/src/eip8130.rs",

    # =================================================================================
    # Transaction pool admission: validation, validity predicates, 2D nonces, DA size
    # estimation and eviction an attacker steers with the transactions they submit
    # =================================================================================
    "crates/execution/txpool/src/validator.rs",
    "crates/execution/txpool/src/validity.rs",
    "crates/execution/txpool/src/transaction.rs",
    "crates/execution/txpool/src/pool.rs",
    "crates/execution/txpool/src/ordering.rs",
    "crates/execution/txpool/src/best.rs",
    "crates/execution/txpool/src/parking.rs",
    "crates/execution/txpool/src/two_d_nonce_pool.rs",
    "crates/execution/txpool/src/invalidation.rs",
    "crates/execution/txpool/src/state_diff_maintain.rs",
    "crates/execution/txpool/src/block_expiry.rs",
    "crates/execution/txpool/src/estimated_da_size.rs",
    "crates/execution/txpool/src/limits.rs",
    "crates/execution/txpool/src/manifest.rs",
    "crates/execution/txpool/src/guard.rs",
    "crates/execution/txpool/src/wire.rs",
    "crates/execution/txpool/src/builder/rpc.rs",
    "crates/execution/txpool-rpc/src/rpc.rs",
    "crates/execution/txpool-rpc/src/extension.rs",
    "crates/common/flz/src/flz.rs",
    "crates/common/l1-fees/src/params.rs",

    # =================================================================================
    # Block building and resource metering: the accounting that decides what an
    # attacker's transaction pays for and whether it can monopolise block capacity
    # =================================================================================
    "crates/execution/payload/src/builder.rs",
    "crates/execution/payload/src/validator.rs",
    "crates/execution/payload/src/validity.rs",
    "crates/execution/payload/src/affordability.rs",
    "crates/execution/payload/src/inclusion.rs",
    "crates/execution/payload/src/metering.rs",
    "crates/execution/payload/src/resource_metering.rs",
    "crates/execution/payload/src/parkable.rs",
    "crates/execution/payload/src/predicate_loads.rs",
    "crates/execution/payload/src/rejection_cache.rs",
    "crates/execution/payload/src/payload.rs",
    "crates/execution/payload/src/types.rs",
    "crates/execution/metering/src/meter.rs",
    "crates/execution/metering/src/inspector.rs",
    "crates/execution/metering/src/block.rs",
    "crates/execution/metering/src/transaction.rs",
    "crates/execution/metering/src/rpc.rs",
    "crates/execution/metering/src/types.rs",
    "crates/common/bundles/src/bundle.rs",
    "crates/common/bundles/src/parsed.rs",
    "crates/common/bundles/src/meter.rs",
    "crates/common/bundles/src/accepted.rs",
    "crates/builder/core/src/execution.rs",
    "crates/builder/core/src/shadow_validity.rs",
    "crates/builder/core/src/flashblocks/best_txs.rs",
    "crates/builder/core/src/flashblocks/generator.rs",
    "crates/builder/core/src/flashblocks/payload.rs",
    "crates/builder/core/src/flashblocks/context.rs",
    "crates/builder/core/src/flashblocks/deadline.rs",
    "crates/builder/metering/src/store.rs",
    "crates/builder/metering/src/ext.rs",

    # =================================================================================
    # Flashblocks preconfirmation state: what a public node serves as pending before
    # the block is sealed, and the reconciliation when the two disagree
    # =================================================================================
    "crates/execution/flashblocks/src/processor.rs",
    "crates/execution/flashblocks/src/validation.rs",
    "crates/execution/flashblocks/src/pending_blocks.rs",
    "crates/execution/flashblocks/src/state.rs",
    "crates/execution/flashblocks/src/state_builder.rs",
    "crates/execution/flashblocks/src/block_assembler.rs",
    "crates/execution/flashblocks/src/receipt_builder.rs",
    "crates/execution/flashblocks/src/cache.rs",
    "crates/execution/flashblocks/src/subscription.rs",
    "crates/execution/flashblocks/src/rpc/eth.rs",
    "crates/execution/flashblocks/src/rpc/pubsub.rs",
    "crates/execution/flashblocks/src/rpc/types.rs",
    "crates/common/flashblocks/src/payload.rs",
    "crates/common/flashblocks/src/block.rs",
    "crates/common/flashblocks/src/metadata.rs",

    # =================================================================================
    # Public RPC surface: the HTTP, WebSocket and pubsub endpoints any anonymous
    # client calls on a public Base node
    # =================================================================================
    "crates/execution/rpc/src/eth/call.rs",
    "crates/execution/rpc/src/eth/block.rs",
    "crates/execution/rpc/src/eth/transaction.rs",
    "crates/execution/rpc/src/eth/receipt.rs",
    "crates/execution/rpc/src/eth/pending_block.rs",
    "crates/execution/rpc/src/eth/proofs.rs",
    "crates/execution/rpc/src/eth/pubsub.rs",
    "crates/execution/rpc/src/eth/base_time.rs",
    "crates/execution/rpc/src/state.rs",
    "crates/execution/rpc/src/witness.rs",
    "crates/execution/rpc/src/debug.rs",
    "crates/execution/rpc/src/sequencer.rs",
    "crates/execution/rpc/src/error.rs",
    "crates/execution/rpc/src/trace_middleware.rs",
    "crates/execution/tx-forwarding/src/forwarder/request.rs",
    "crates/execution/tx-forwarding/src/reader/validator.rs",
    "crates/infra/ingress-rpc/src/validation.rs",
    "crates/infra/ingress-rpc/src/service.rs",
    "crates/infra/websocket-proxy/src/server.rs",
    "crates/infra/websocket-proxy/src/subscriber.rs",
    "crates/infra/websocket-proxy/src/rate_limit.rs",
    "crates/infra/websocket-proxy/src/registry.rs",
    "crates/infra/websocket-proxy/src/filter.rs",
    "crates/infra/websocket-proxy/src/auth.rs",
    "crates/utilities/trusted-proxy/src/trusted_proxy.rs",
    "crates/consensus/rpc/src/rollup.rs",
    "crates/consensus/rpc/src/output.rs",
    "crates/consensus/rpc/src/base.rs",
    "crates/consensus/rpc/src/response.rs",

    # =================================================================================
    # Derivation of L1 data an unprivileged user writes permissionlessly: deposit
    # logs, system config updates and the attributes every honest node replays
    # =================================================================================
    "crates/consensus/protocol/src/deposits.rs",
    "crates/consensus/protocol/src/info/isthmus.rs",
    "crates/consensus/protocol/src/info/jovian.rs",
    "crates/consensus/protocol/src/info/ecotone.rs",
    "crates/consensus/protocol/src/info/bedrock.rs",
    "crates/consensus/protocol/src/info/variant.rs",
    "crates/consensus/protocol/src/output_root.rs",
    "crates/consensus/protocol/src/block.rs",
    "crates/consensus/protocol/src/base_time.rs",
    "crates/consensus/protocol/src/timing.rs",
    "crates/consensus/derive/src/attributes/stateful.rs",
    "crates/consensus/derive/src/stages/attributes_queue.rs",
    "crates/consensus/derive/src/stages/batch/batch_validator.rs",
    "crates/consensus/derive/src/stages/batch/batch_queue.rs",
    "crates/consensus/derive/src/pipeline/core.rs",
    "crates/common/genesis/src/system/config.rs",
    "crates/common/genesis/src/system/log.rs",
    "crates/common/genesis/src/system/update.rs",
    "crates/common/genesis/src/updates/gas_config.rs",
    "crates/common/genesis/src/updates/gas_limit.rs",
    "crates/common/genesis/src/updates/eip1559.rs",
    "crates/common/genesis/src/updates/min_base_fee.rs",
    "crates/common/genesis/src/updates/operator_fee.rs",
    "crates/common/genesis/src/updates/da_footprint_gas_scalar.rs",
    "crates/common/genesis/src/rollup.rs",
    "crates/common/genesis/src/params.rs",
    "crates/execution/chainspec/src/basefee.rs",
    "crates/execution/chainspec/src/spec.rs",
    "crates/execution/chainspec/src/upgrades.rs",
    "crates/execution/consensus/src/validation/isthmus.rs",
    "crates/execution/consensus/src/validation/canyon.rs",
    "crates/execution/consensus/src/proof.rs",
    "crates/consensus/upgrades/src/isthmus.rs",
    "crates/consensus/upgrades/src/jovian.rs",
    "crates/consensus/upgrades/src/ecotone.rs",
    "crates/consensus/upgrades/src/fjord.rs",

    # =================================================================================
    # Fault proof program and dispute path: the permissionless soundness boundary that
    # decides whether an incorrect output root can be finalised against the bridge
    # =================================================================================
    "crates/proof/client/src/driver.rs",
    "crates/proof/client/src/prologue.rs",
    "crates/proof/client/src/epilogue.rs",
    "crates/proof/driver/src/core.rs",
    "crates/proof/driver/src/executor.rs",
    "crates/proof/driver/src/cursor.rs",
    "crates/proof/driver/src/pipeline.rs",
    "crates/proof/driver/src/tip.rs",
    "crates/proof/executor/src/builder/core.rs",
    "crates/proof/executor/src/builder/assemble.rs",
    "crates/proof/executor/src/builder/env.rs",
    "crates/proof/executor/src/db/mod.rs",
    "crates/proof/executor/src/util.rs",
    "crates/proof/mpt/src/node.rs",
    "crates/proof/mpt/src/util.rs",
    "crates/proof/mpt/src/list_walker.rs",
    "crates/proof/mpt/src/traits.rs",
    "crates/proof/preimage/src/oracle.rs",
    "crates/proof/preimage/src/key.rs",
    "crates/proof/preimage/src/hint.rs",
    "crates/proof/primitives/src/proof.rs",
    "crates/proof/primitives/src/proof_encoder.rs",
    "crates/proof/primitives/src/proposal.rs",
    "crates/proof/challenge/src/validator.rs",
    "crates/proof/challenge/src/driver.rs",
    "crates/proof/challenge/src/scanner.rs",
    "crates/proof/challenge/src/bond.rs",
    "crates/proof/challenge/src/anchor.rs",
    "crates/proof/zk/witness/src/l2_output.rs",
    "crates/proof/zk/witness/src/witness_generation/generator.rs",
    "crates/proof/zk/witness/src/witness_generation/preimage_witness_collector.rs",

    # =================================================================================
    # State trie and proof generation every balance, storage read and withdrawal proof
    # an attacker requests or relies on bottoms out in
    # =================================================================================
    "crates/execution/trie/src/live.rs",
    "crates/execution/trie/src/in_memory.rs",
    "crates/execution/trie/src/proof.rs",
    "crates/execution/trie/src/cursor.rs",
    "crates/execution/trie/src/cursor_factory.rs",
    "crates/execution/trie/src/batch_provider.rs",
    "crates/execution/trie/src/provider.rs",
    "crates/execution/trie/src/api.rs",
    "crates/execution/trie/src/db/store.rs",
    "crates/execution/trie/src/db/batch.rs",
    "crates/execution/trie/src/db/cursor.rs",
    "crates/execution/trie/src/db/models/storage.rs",
    "crates/execution/trie/src/db/models/change_set.rs",
    "crates/execution/trie/src/prune/pruner.rs",
    "crates/execution/proofs/src/proofs.rs",
]


target_scopes = [
    "Critical. An unprivileged caller mints, duplicates or destroys B20 token balance that was never backed, breaking supply conservation on a native token precompile: transfer_inner, burn_inner, mint, batch_mint, burn, update_supply_cap and to_scaled_balance/to_raw_balance in crates/common/precompiles/src/b20_asset/logic/v1.rs and logic/v2.rs, the equivalent handlers in b20_stablecoin/logic/v1.rs and logic/v2.rs, set_balance, set_total_supply and set_allowance in crates/common/precompiles/src/common/token_accounting.rs, the AssetAccounting implementations in b20_asset/accounting.rs and b20_stablecoin/accounting.rs, or the handle_asset_call and route decode paths in b20_asset/dispatch.rs let an attacker-chosen amount, multiplier or recipient list overflow, truncate, round or credit twice.",
    "Critical. An unprivileged caller performs a privileged B20 or policy operation on a token or account whose role they do not hold, an unauthorized account operation: ensure_role, ensure_token_role, ensure_not_paused, ensure_policy, ensure_policy_type, ensure_authorized_by_id, ensure_blocked and ensure_seizable in crates/common/precompiles/src/common/ops/guards.rs, grant_role, grant_role_unchecked, revoke_role, revoke_role_unchecked, renounce_last_admin, set_role_admin and ensure_role_admin_mutations_available in b20_asset/logic/v1.rs, struct_hash, signing_hash, recover_signer and validate_recovered_address in crates/common/precompiles/src/common/ops/permit.rs, domain_separator and eip712_domain, or the caller resolution in b20_asset/dispatch.rs and crates/common/precompiles/src/tx_context/dispatch.rs admit a caller, signature or role bitmap that never satisfied the check.",
    "Critical. An attacker gets an EIP-8130 transaction executed as, or paid for by, an account whose key they do not hold: authenticate_actor, authenticate, authorize_k1 and resolve_bound in crates/execution/eip8130/src/authorize.rs, verify, verify_with_recovered_sender and verify_sender in verify.rs, authorize_actor, authorize_non_self_actor, revoke_actor, enforce_locked_authorize_rules and apply_config_change in apply.rs, the scope and expiry packing in scope.rs and account_config.rs, signature recovery in signature.rs, or sender/payer resolution in recovered.rs and resolved.rs accept an expired, revoked, out-of-scope, replayed or malleable authorization so a sender or sponsor is bound without consent.",
    "Critical. An attacker reads or writes precompile state belonging to a different token, account or namespace through their own call, corrupting balances, roles or policies: the slot derivation in crates/common/precompile-storage/src/types/slot.rs, mapping.rs, array.rs, vec.rs, set.rs and bytes_like.rs, read/write and packing in packing.rs and storage_ctx.rs, the journal and revert handling in journal.rs and evm.rs, the namespace registration in registration.rs and provider.rs, or the storage layouts in crates/common/precompiles/src/b20_asset/storage.rs, b20_stablecoin/storage.rs, b20_factory/storage.rs, policy/storage.rs and nonce/storage.rs let two distinct keys collide or a reverted frame leave its writes committed.",
    "Critical. One transaction an attacker broadcasts makes honest Base nodes disagree on the resulting state root, receipts root or block hash, forcing an unintended chain split: the fork and activation gating in crates/common/evm/src/spec.rs, handler.rs, eip8130_phase_statuses.rs and crates/common/precompiles/src/spec.rs with activation/storage.rs, the receipt construction in crates/common/evm/src/receipt_builder.rs and crates/common/consensus/src/receipts/receipt.rs and eip8130.rs, transaction hashing and encoding in crates/common/consensus/src/transaction/envelope.rs, canonical.rs and eip8130/signed.rs, the fee math in crates/common/evm/src/l1block.rs, crates/execution/evm/src/l1.rs and crates/common/l1-fees/src/params.rs, or the base-fee and extra-data rules in crates/execution/chainspec/src/basefee.rs and crates/common/consensus/src/extra/jovian.rs produce a result only some nodes reproduce.",
    "Critical. A transaction, deposit or contract call an attacker submits panics, overflows or wedges block execution, so every node applying that block halts and the network stops confirming transactions: the execution loop in crates/common/evm/src/executor/block_executor.rs and crates/common/evm2/src/executor.rs, handler.rs error mapping, the precompile dispatch in crates/common/precompiles/src/b20_asset/dispatch.rs, b20_factory/dispatch.rs and policy/dispatch.rs, apply_config_change and advance_channel_sequence in crates/execution/eip8130/src/apply.rs, deposit handling in crates/consensus/protocol/src/deposits.rs and crates/common/evm/src/transaction/deposit.rs, the system-config log decoding in crates/common/genesis/src/system/log.rs and updates/gas_config.rs, or attributes building in crates/consensus/derive/src/attributes/stateful.rs turn attacker-chosen bytes into a node-fatal error rather than a rejected transaction.",
    "Critical. An incorrect L2 output root is provable, letting an attacker finalise a withdrawal that was never earned and drain the bridge: the state transition in crates/proof/client/src/driver.rs with prologue.rs and epilogue.rs, advance_to_target and the safe-head cursor in crates/proof/driver/src/core.rs, executor.rs and cursor.rs, block assembly and header checks in crates/proof/executor/src/builder/core.rs, assemble.rs and env.rs, the trie decoding and inclusion logic in crates/proof/mpt/src/node.rs, util.rs and list_walker.rs, preimage key construction and oracle validation in crates/proof/preimage/src/key.rs and oracle.rs, output-root computation in crates/consensus/protocol/src/output_root.rs and crates/proof/zk/witness/src/l2_output.rs, or the proposal checks in crates/proof/primitives/src/proposal.rs and crates/proof/challenge/src/validator.rs accept a witness or claim that does not match the real chain.",
    "Critical. An attacker executes work the node never charges them for, or pins block capacity so other users' transactions cannot be included: the intrinsic and per-operation costs in crates/common/eip8130/src/intrinsic.rs and schedule.rs, max_fee_charge, validate_balance and validate_gas_and_tip in crates/execution/eip8130/src/fee.rs, unaffordable and unaffordable_tip in crates/execution/payload/src/affordability.rs, fits_in, add_to, from_execution and the slot-counting helpers in crates/execution/payload/src/resource_metering.rs with crates/execution/metering/src/meter.rs and inspector.rs, the DA estimate in crates/execution/txpool/src/estimated_da_size.rs and crates/common/flz/src/flz.rs, or gas accounting in crates/common/evm/src/handler.rs undercharge, double-refund or mis-attribute a reachable execution path.",
    "High. A single anonymous RPC request, or a cheap stream of self-funded transactions, makes a public Base node stop serving other users or serve state that is wrong: eth_call, eth_estimateGas and state overrides in crates/execution/rpc/src/eth/call.rs and state.rs, log and block queries in eth/block.rs and eth/receipt.rs, subscriptions in eth/pubsub.rs and crates/execution/flashblocks/src/rpc/pubsub.rs, proof and witness generation in eth/proofs.rs and witness.rs with crates/execution/trie/src/proof.rs, the pending flashblock state served by crates/execution/flashblocks/src/pending_blocks.rs, state.rs, validation.rs and processor.rs, the admission checks in crates/infra/ingress-rpc/src/validation.rs, the proxy limits in crates/infra/websocket-proxy/src/rate_limit.rs, subscriber.rs and registry.rs, or the pool limits and predicate indexes in crates/execution/txpool/src/limits.rs, validity.rs, parking.rs and two_d_nonce_pool.rs perform an unbounded scan, an unchecked allocation, or return preconfirmed state that the sealed block contradicts.",
    "Critical/High blind spot. An ordinary funded address or anonymous RPC client abuses an assumption Base never wrote down: two distinct payloads that hash to the same transaction identity across crates/common/consensus/src/transaction/envelope.rs and eip8130/signed.rs, an ABI selector whose v1 and v2 decoders in crates/common/precompiles/src/common/abi/ disagree on argument bounds, a precompile version or activation flag read at dispatch but re-read after a nested call in crates/common/precompiles/src/activation/ and lookup.rs, a limit enforced in the txpool validator but not on the direct builder or ingress path to the same execution, a policy or pause vector that blocks transfer but not seize, permit or a factory-minted variant, a value that survives validation but overflows only once crates/common/precompile-storage/src/packing.rs packs it back, an eip8130 channel nonce that advances on a reverted frame, a deposit or system-config field derivation accepts but block execution rejects, or a cache in crates/execution/txpool/src/validator.rs, crates/execution/flashblocks/src/cache.rs or crates/execution/trie/src/in_memory.rs that answers differently from the state it fronts - yielding an unauthorized account operation, unbacked token supply, stolen or permanently frozen funds, a node halt on block execution, an unintended chain split, a provable wrong output root, or an RPC the node can no longer serve.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one Base target.

    ```
    target_file format:
    "'File Name: crates/common/precompiles/src/b20_asset/logic/v1.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact Base target:

    {target_file}

    Project focus:
    Base is an OP-Stack L2. Focus only on what an ordinary user reaches: signing and broadcasting any transaction type (legacy, EIP-1559, EIP-2930, EIP-7702, EIP-8130 sponsored/delegated) to a public Base RPC or the ingress RPC, deploying and calling their own contract, calling the native B20 asset, B20 stablecoin, B20 factory, policy, nonce, tx-context and activation precompiles with their own calldata, sending a permissionless L1 deposit or system-config transaction, opening or defending a permissionless dispute game, and sending anonymous eth_* JSON-RPC, WebSocket or flashblocks-subscription requests. Downstream of that: precompile dispatch and role checks, precompile storage slots, EIP-8130 authorization and fee accounting, EVM handler and fork gating, receipt and transaction encoding, txpool admission, block building and resource metering, flashblocks pending state, derivation of attacker-written L1 data, the fault-proof program, and the state trie.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact symbols (Rust fn, method, struct, enum variant, trait, const or storage-slot type) when possible.
    * Attacker is unprivileged only: anyone who funds a Base address and broadcasts signed transactions, deploys and calls their own contract, calls a native precompile, creates their own B20 token via the factory, submits an L1 deposit, participates permissionlessly in a dispute game, or sends anonymous RPC/WebSocket requests to a public node. They control only their own keys.
    * Attacker is NOT the sequencer, builder, batcher, proposer, prover, node operator, chain governor, or any role holder on someone else's token. Never assume a malicious peer, malicious node, malicious sequencer or builder, p2p/gossip/sync attacker, network-level DoS or flooding, leaked key, non-default config, or social engineering.
    * Out of scope, never ask about: p2p networking and peer handling, block production by the sequencer, batcher and proposer submission, node startup and CLI/config parsing, metrics, tracing and logging, deployment and infra, dependency versions, the enclave/TEE attestation host.
    * Ignore test files, mocks, benchmarks, docs, generated bindings, and config-only findings.
    * Every question must describe a real signed transaction, precompile call, contract deployment, L1 deposit, dispute-game move or single RPC request the attacker actually submits through a valid entrypoint. No generic unbounded-allocation, memory-growth or resource-exhaustion speculation; no "what if the input is huge" without a concrete submitted payload and a concrete broken invariant.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must target an unauthorized account or token operation, direct theft or permanent freezing of funds, unbacked token supply or balance inflation, a node halt while executing a block, an unintended chain split between honest nodes, a provable incorrect output root, or a public RPC a node can no longer serve.
    * Every question must be testable by a `cargo nextest run -p base-common-precompiles`, `-p base-precompile-storage`, `-p base-execution-eip8130`, `-p base-common-evm`, `-p base-common-consensus`, `-p base-execution-txpool`, `-p base-execution-payload-builder`, `-p base-flashblocks`, `-p base-execution-rpc`, `-p base-protocol` or `-p base-proof-executor` unit test, or a single-node block-execution flow test.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Authorization: an account's balance, roles, allowances, policies or EIP-8130 configuration change only when the caller or a valid unexpired in-scope signature from that account authorises it.
    * Value conservation: ETH and B20 supply debited on one side is credited exactly once on the other; mints respect the supply cap, fees and sponsor payments are never created or duplicated.
    * State isolation: a call to one precompile, token or account can never read or write another's storage slots, and reverted frames commit nothing.
    * Determinism: given the same block, every honest node at the same fork and precompile activation state reaches the same state root, receipts root and block hash, and the fault-proof program reproduces it exactly.
    * Availability: no single submitted transaction or RPC request can halt block execution or make a public endpoint permanently unable to answer other users.

    Each question must include:
    1. target module/fn;
    2. attacker action (a concrete transaction, precompile call with selector and arguments, contract deployment, deposit, dispute move or RPC request);
    3. preconditions (accounts, ETH balance, deployed contract, created B20 token, granted authorization or open game the attacker relies on);
    4. execution sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger EXECUTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: cargo nextest unit test / single-node block-execution test PARAMETERS and assert AUTHORIZATION, VALUE_CONSERVATION, STATE_ISOLATION, DETERMINISM, or AVAILABILITY.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused Base exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: anyone who funds a Base address and broadcasts signed transactions, deploys and calls their own contract, calls a native precompile, creates their own B20 token, submits an L1 deposit, plays a permissionless dispute game, or sends anonymous RPC/WebSocket requests to a public node. No sequencer, builder, batcher, proposer, prover, node-operator, governor, or foreign-key access.
- Reject malicious-sequencer, malicious-builder, malicious-peer, malicious-node, p2p/gossip/sync, network-level DoS or request flooding, leaked-key, and misconfiguration-only paths.
- Reject 51%-style, sybil and centralization claims, L1 reorg assumptions, economic-design critique, self-harm (attacker only damages their own account or their own token), and monitoring, CLI, logging, deployment, dependency-only, and test/mock/generated/config-only findings.
- Reject generic unbounded-allocation or storage-growth claims with no concrete submitted payload and no broken invariant.
- Focus on real chain impact: an unauthorized operation on an account or token the attacker does not control, direct theft or permanent freezing of user funds, unbacked token supply or balance inflation, a node halt while executing a block, an unintended chain split between honest nodes, a provable incorrect output root, key or secret disclosure, remote code execution, or a public RPC the node can no longer serve.

## Validate
- Trace the exact reachable path from the attacker's signed transaction, precompile call, deposit, dispute move or RPC request into the affected function.
- Check whether signature recovery, EIP-8130 authorization and scope checks, precompile role guards and pause vectors, precompile activation and version gating, txpool validation, intrinsic gas and resource metering, fork gating in the EVM spec, storage-namespace derivation, journal revert handling, or existing error returns already stop it.
- Confirm the path is reachable on current mainnet chainspec and active fork/activation state, not only behind a disabled flag.
- Accept only a concrete unauthorized operation, fund loss or freezing, unbacked supply, node halt, chain split, wrong provable output root, key disclosure, RCE, or lasting RPC unavailability.
- Require exact file/function support and a reproducible `cargo nextest` or single-node block-execution PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker payload, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and severity: Critical (unauthorized account or token operation, direct theft or permanent freezing of funds, unbacked supply or balance inflation, node takeover or RCE, key disclosure, network unable to confirm new transactions, unintended chain split, provable incorrect output root enabling a bridge withdrawal) or High (RPC or node DoS from a single request or transaction, transaction-inclusion censorship, corruption of shared on-chain state other users depend on)]

### Likelihood Explanation
[Preconditions, accounts and balance needed, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[cargo nextest test / single-node block-execution test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for Base.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only analogs an unprivileged transaction sender, contract deployer, precompile caller, B20 token creator, depositor, dispute-game participant or anonymous RPC client can reach: precompile dispatch, role guards, permits and policies, precompile storage slot derivation and journaling, EIP-8130 authorization, nonce and fee accounting, EVM handler and fork gating, transaction and receipt encoding, txpool admission, block building and resource metering, flashblocks pending state, derivation of attacker-written L1 deposit and system-config data, the fault-proof program and MPT, or the public eth_* and WebSocket query paths.
- Reject malicious-sequencer, malicious-builder, malicious-peer, malicious-node, p2p/sync, network-DoS, leaked-key, monitoring, CLI, deployment, mocked-only paths, dependency-only bugs, and no-impact analogs.
- Medium, High and Critical only; no low, or resource-only analogs.

## Validate
- Map the bug class to the strongest reachable Base path from a single signed transaction, precompile call, deposit, dispute move or RPC request.
- Prove root cause with exact file/function support.
- Accept only a concrete unauthorized operation, theft or permanent freezing of funds, unbacked supply, node halt, chain split, wrong provable output root, key disclosure, RCE, or an RPC the node can no longer serve.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for Base security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Focus on High and Critical; reject informational, best-practice, and resource-only reports.
- Reject malicious-sequencer, malicious-builder, malicious-batcher, malicious-proposer, malicious-peer, malicious-node, p2p/gossip/sync, network-level DoS or request flooding, monitoring endpoints, CLI, logging, deployment and infra, dependency-only, docs/style, generated-bindings, and test/mock/config-only issues.
- Reject if the exploit needs sequencer, builder, batcher, proposer, prover, node-operator, governor or privileged role access, a role on a token the attacker does not own, another user's key, victim social engineering, a non-default config, an inactive fork or precompile activation, an L1 reorg, or anything outside what an unprivileged user can put in a signed transaction, a precompile call, a deposit, a dispute-game move, or an anonymous RPC request.
- Reject 51%-style majority attacks, sybil and centralization claims, economic-design critique, and self-harm where the attacker only damages their own account or their own token.
- Reject if the bug was fixed, acknowledged, or publicly disclosed already, per the eligibility rules.
- A valid report must be triggerable by an unprivileged transaction sender, precompile caller, depositor, dispute-game participant or anonymous RPC client, unless the claim proves escalation from that starting point.
- The final impact must map to an in-scope category: Critical - remote code execution or node takeover, key or secret disclosure, unauthorized operation on an account or token the attacker does not control, direct theft or permanent freezing of user funds, unbacked token supply or balance inflation, the network unable to confirm new transactions, an unintended chain split, or a provable incorrect output root enabling an unearned bridge withdrawal; High - DoS of the public RPC, WebSocket or node implementation from a single request or transaction, transaction-inclusion censorship, or corruption of shared on-chain state other users depend on.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, module, function, and line/code references.
2. Clear root cause and broken authorization, value-conservation, state-isolation, determinism, or availability invariant.
3. Reachable exploit path: preconditions (attacker accounts, ETH balance, deployed contract, created token, granted authorization) -> signed transaction, precompile call, deposit, dispute move or RPC request -> trigger -> bad result.
4. Existing signature recovery, EIP-8130 authorization and scope checks, precompile role guards and pause vectors, activation and version gating, txpool validation, intrinsic gas and resource metering, storage-namespace derivation, journal revert handling, and error returns reviewed and shown insufficient.
5. Concrete in-scope High/Critical impact with realistic likelihood.
6. Reproducible proof path: `cargo nextest` PoC against the real crates, or exact steps in a single-node block-execution flow.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an ordinary user trigger this by broadcasting a transaction, calling a precompile, submitting a deposit, or sending one anonymous RPC request, without sequencer, builder, role-holder, or foreign-key access?
- Does the code actually behave as claimed on current mainnet chainspec and active fork and precompile activation state?
- Is the impact caused by this code, not by a privileged actor, a peer, or a dependency?
- Is the fund loss, unauthorized operation, halt, split, wrong output root or RPC outage concrete rather than hypothetical, and does it harm someone other than the attacker?
- Would a Base triager accept the proof-of-concept?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete in-scope impact, severity rationale, and Base bounty category]

## Likelihood Explanation
[Attacker capability, accounts and balance required, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or cargo nextest / single-node block-execution test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
