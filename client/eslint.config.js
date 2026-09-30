import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

// Source installed from the shadcn and Pipecat UI registries. It is updated by re-adding the
// item and reviewing the diff (`npx shadcn@latest add @pipecat/<item> --diff`), not edited to
// suit these rules, so the rules that only concern our own components are off for it.
const vendored = [
  'src/components/ui/**',
  'src/components/pipecat/**',
  'src/hooks/use-pipecat-app.ts',
  'src/lib/transports.ts',
  'src/lib/visualizer.ts',
  'src/hooks/use-pipecat-*.ts',
]

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
  },
  {
    files: vendored,
    rules: {
      'react-refresh/only-export-components': 'off',
      'react-hooks/refs': 'off',
      'react-hooks/static-components': 'off',
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/immutability': 'off',
    },
  },
])
