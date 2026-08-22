The repository content available in the index is limited to `src/README.md`, `src/LICENSE`, and `src/package.json` — there is no actual SDK source code (auth, session, embedded wallet, iframe bridge, MFA, or transaction-signing implementation files) present in this repository's indexed contents. [1](#0-0) [2](#0-1)

### Citations

**File:** src/README.md (L1-64)
```markdown
# @privy-io/js-sdk-core

> 🔧 The Privy JS SDK is a low-level library and **not intended for general consumption.**
>
> **Before building, please reach out to the Privy team** to discuss your project and which Privy SDK options may be better suited to it.

## Usage

### Auth

```tsx
const privy = new Privy({appId: '<your-app-id-here>'});

await privy.auth.sms.sendCode('+1 555 555 5555');
const {user} = await privy.auth.sms.loginWithCode('+1 555 555 5555', '123123');
```

### Embedded Wallets

```tsx
// Or use the embedded wallet with viem
import {createWalletClient, custom} from 'viem';

// Initialize the client
const privy = new Privy({appId: '<your-app-id-here>'});

// Log in
await privy.auth.sms.sendCode('+1 555 555 5555');
const {user} = await privy.auth.sms.loginWithCode('+1 555 555 5555', '123123');

// Create an embedded wallet
const wallet = await privy.embeddedWallet.create();

// Use the embedded wallet
const accounts = await wallet.request({method: 'eth_requestAccounts'});
const response = await wallet.request({
  method: 'eth_sendTransaction',
  params: [
    {
      from: accounts[0],
      to: '0x0000000000000000000000000000000000000000',
      value: '1',
    },
  ],
});

// create a viem client from the privy embedded wallet
const viemWalletClient = createWalletClient({
  chain: mainnet,
  transport: custom(wallet),
});

// use viem to sign a message
await viemWalletClient.signMessage({
  message: 'Hello from Privy!!',
  account: wallet.address,
});
```

## Changelog

Our [changelog](https://docs.privy.io/changelogs/js-sdk-core) contains the latest information about new releases, including features, fixes, and upcoming changes.

We use [Semantic Versioning](https://semver.org/) to track changes.
```

**File:** src/package.json (L1-118)
```json
{
  "name": "@privy-io/js-sdk-core",
  "version": "0.70.0",
  "description": "Vanilla JS client for the Privy Auth API",
  "keywords": [
    "authentication",
    "authorization",
    "identity",
    "privacy",
    "privy",
    "user data",
    "web3"
  ],
  "license": "Apache-2.0",
  "author": "privy.io",
  "sideEffects": false,
  "type": "commonjs",
  "exports": {
    ".": {
      "require": {
        "types": "./dist/dts/index.d.ts",
        "default": "./dist/cjs/index.js"
      },
      "import": {
        "types": "./dist/dts/index.d.mts",
        "default": "./dist/esm/index.mjs"
      }
    },
    "./smart-wallets": {
      "require": {
        "types": "./dist/dts/smart-wallets.d.ts",
        "default": "./dist/cjs/smart-wallets.js"
      },
      "import": {
        "types": "./dist/dts/smart-wallets.d.mts",
        "default": "./dist/esm/smart-wallets.mjs"
      }
    }
  },
  "main": "./dist/cjs/index.js",
  "module": "./dist/esm/index.mjs",
  "types": "./dist/dts/index.d.ts",
  "files": [
    "dist/**/*",
    "LICENSE",
    "README.md"
  ],
  "browserslist": [
    "defaults",
    "not op_mini all"
  ],
  "dependencies": {
    "@privy-io/api-types": "0.20.0",
    "canonicalize": "^2.0.0",
    "eventemitter3": "^5.0.1",
    "fetch-retry": "^6.0.0",
    "jose": "^4.15.5",
    "js-cookie": "^3.0.5",
    "libphonenumber-js": "^1.12.10",
    "set-cookie-parser": "^2.6.0",
    "@privy-io/api-base": "1.9.5",
    "@privy-io/chains": "0.5.2",
    "@privy-io/ethereum": "0.2.2",
    "@privy-io/routes": "0.2.11",
    "@privy-io/encoding": "0.2.2"
  },
  "devDependencies": {
    "@metamask/eth-sig-util": "^8.2.0",
    "oxlint": "1.57.0",
    "oxlint-tsgolint": "0.18.0",
    "@simplewebauthn/types": "9.0.1",
    "@solana/wallet-standard-features": "*",
    "@solana/web3.js": "^1.98.0",
    "@types/jest": "^29.5.14",
    "@types/js-cookie": "^3.0.3",
    "@types/set-cookie-parser": "^2.4.7",
    "@types/text-encoding": "^0.0.37",
    "@wallet-standard/base": "^1.1.0",
    "@wallet-standard/core": "*",
    "@wallet-standard/features": "*",
    "jest": "^29.7.0",
    "msw": "^2.10.4",
    "rolldown": "1.1.5",
    "rolldown-plugin-dts": "0.27.1",
    "rollup-plugin-copy": "3.5.0",
    "text-encoding": "^0.7.0",
    "ts-jest": "^29.2.6",
    "typescript": "~6.0.2",
    "@privy-io/build-config": "1.0.0",
    "@privy-io/tsconfig": "0.0.0"
  },
  "peerDependencies": {
    "permissionless": "^0.2.47",
    "viem": "2.55.15"
  },
  "peerDependenciesMeta": {
    "permissionless": {
      "optional": true
    },
    "viem": {
      "optional": true
    }
  },
  "publishConfig": {
    "access": "public"
  },
  "scripts": {
    "build": "rolldown --config rolldown.config.mjs",
    "check-types": "tsc --noEmit",
    "clean": "rm -rf dist .turbo .swc *.tsbuildinfo",
    "clean:reset": "rm -rf dist .turbo .swc node_modules *.tsbuildinfo",
    "dev": "rolldown --config rolldown.config.mjs --watch",
    "format": "oxlint src --fix",
    "lint": "oxlint src",
    "test": "jest",
    "test:watch": "pnpm run test -- --watch"
  }
}
```
