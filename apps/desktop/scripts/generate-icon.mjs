// Rasterize the canonical vector brand asset for Electron Builder (ICNS/ICO).
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
const require = createRequire(new URL("../../web/package.json", import.meta.url));
const sharp = require("sharp");
const source = fileURLToPath(new URL("../../web/app/icon.svg", import.meta.url));
const target = fileURLToPath(new URL("../assets/icon.png", import.meta.url));
await sharp(source).resize(1024, 1024).png().toFile(target);
