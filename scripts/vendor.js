#!/usr/bin/env node
/**
 * Copy the browser builds of the frontend libraries out of node_modules into
 * app/static/vendor/.
 *
 * The dashboard has no bundler: index.html loads these four files with plain
 * <script> tags and Babel standalone transpiles the JSX in the browser. The
 * vendor directory is therefore a build output and is not tracked in git, so
 * this runs automatically on `npm install` (see the postinstall script).
 */

"use strict";

const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const VENDOR_DIR = path.join(ROOT, "app", "static", "vendor");

// Source path is relative to node_modules; the destination name is what
// app/static/index.html asks for.
const ASSETS = [
  ["react/umd/react.production.min.js", "react.production.min.js"],
  ["react-dom/umd/react-dom.production.min.js", "react-dom.production.min.js"],
  ["echarts/dist/echarts.min.js", "echarts.min.js"],
  ["@babel/standalone/babel.min.js", "babel.min.js"],
];

function main() {
  fs.mkdirSync(VENDOR_DIR, { recursive: true });

  const missing = [];
  for (const [source, name] of ASSETS) {
    const from = path.join(ROOT, "node_modules", ...source.split("/"));
    if (!fs.existsSync(from)) {
      missing.push(source);
      continue;
    }
    const to = path.join(VENDOR_DIR, name);
    fs.copyFileSync(from, to);
    console.log(`vendor: ${name} (${fs.statSync(to).size} bytes)`);
  }

  if (missing.length > 0) {
    console.error(
      `vendor: missing from node_modules: ${missing.join(", ")}\n` +
        "Run `npm install` first."
    );
    process.exit(1);
  }
}

main();
