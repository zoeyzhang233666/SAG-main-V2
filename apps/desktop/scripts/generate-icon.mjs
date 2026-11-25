// Temporary desktop icon using the existing paper-theme colors.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
const require = createRequire(new URL("../../web/package.json", import.meta.url));
const sharp = require("sharp");
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><rect width="512" height="512" rx="128" fill="#B8955A"/><path d="M360 159a138 138 0 1 0 0 194" fill="none" stroke="#18181b" stroke-width="50" stroke-linecap="round"/></svg>';
await sharp(Buffer.from(svg)).png().toFile(fileURLToPath(new URL("../assets/icon.png", import.meta.url)));
