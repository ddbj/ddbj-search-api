/**
 * @see https://prettier.io/docs/en/configuration.html
 * @type {import("prettier").Config}
 */

const config = {
  plugins: ["./node_modules/@typespec/prettier-plugin-typespec/dist/index.js"],
  overrides: [{ files: "*.tsp", options: { parser: "typespec" } }],
  semi: true,
  trailingComma: "es5",
  singleQuote: false,
  tabWidth: 2,

  printWidth: 100,
};

export default config;
