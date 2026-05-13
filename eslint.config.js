// Flat ESLint config for the MMGIS frontend tool.
//
// The plugin code in mmgis-tool/HyPlan is built inside an MMGIS source
// tree by MMGIS's own babel/webpack pipeline.  We can't run the MMGIS
// build in this repo's CI, so the goal here is narrow: catch real bugs
// (undefined variables, unused declarations, unreachable code) without
// fighting MMGIS's stylistic choices or trying to resolve its relative
// imports (../../Basics/...).
//
// Run locally:
//     npm install
//     npm run lint

const js = require('@eslint/js')
const globals = require('globals')

module.exports = [
    js.configs.recommended,
    {
        files: ['mmgis-tool/HyPlan/**/*.js'],
        languageOptions: {
            ecmaVersion: 2022,
            sourceType: 'module',
            globals: {
                ...globals.browser,
                // Leaflet is loaded globally by MMGIS, so it shows up as
                // a free reference rather than an import.
                L: 'readonly',
            },
        },
        rules: {
            // The MMGIS plugin convention (and the existing file) leans on
            // hoisted function declarations and a handful of intentionally
            // unused `_` parameters.  Be helpful, not pedantic.
            'no-unused-vars': ['warn', {
                argsIgnorePattern: '^_',
                varsIgnorePattern: '^_',
                caughtErrorsIgnorePattern: '^(_|e$|err$|exc$)',
            }],

            // Inner-function declarations are used throughout HyPlanTool.js
            // for panel-scoped helpers — accept that style.
            'no-inner-declarations': 'off',

            // Prefer === / !== — the existing file is consistent on this.
            eqeqeq: ['error', 'always', { null: 'ignore' }],
        },
    },
    {
        // Vitest tests for the pure helpers in mmgis-tool/HyPlan.
        files: ['tests/js/**/*.{js,test.js}'],
        languageOptions: {
            ecmaVersion: 2022,
            sourceType: 'module',
            globals: {
                ...globals.node,
            },
        },
        rules: {
            eqeqeq: ['error', 'always', { null: 'ignore' }],
        },
    },
]
