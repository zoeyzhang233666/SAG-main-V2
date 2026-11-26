import { randomBytes } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import {
  spawn,
  type ChildProcessByStdio,
} from "node:child_process";
import type { Readable } from "node:stream";

import { app, utilityProcess, type UtilityProcess } from "electron";
import log from "electron-log/main";

import { desktopConfig } from "./config";
import { isPortAvailable, resolveApiPort } from "./runtime-ports";

interface RuntimeSecretFile {
  secretKey: string;
}

export interface ManagedRuntime {
  readonly webUrl: string;
  readonly apiUrl: string;
  stop(): void;
}

interface StartedProcess {
  stop(): void;
  startupError(): Error | undefined;
}

function isValidSecretFile(value: unknown): value is RuntimeSecretFile {
  if (!value || typeof value !== "object") return false;
  return typeof (value as RuntimeSecretFile).secretKey === "string"
    && (value as RuntimeSecretFile).secretKey.length >= 64;
}

function loadOrCreateSecret(userDataDir: string): string {
  const file = path.join(userDataDir, "desktop-runtime.json");
  if (existsSync(file)) {
    try {
      const parsed: unknown = JSON.parse(readFileSync(file, "utf8"));
      if (isValidSecretFile(parsed)) return parsed.secretKey;
    } catch (error) {
      log.warn("Ignoring invalid desktop runtime secret", error);
    }
  }
  const secretKey = randomBytes(48).toString("hex");
  writeFileSync(file, `${JSON.stringify({ secretKey }, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  return secretKey;
}

async function findAvailablePort(host: string, preferred: number): Promise<number> {
  for (let port = preferred; port < Math.min(65535, preferred + 100); port += 1) {
    if (await isPortAvailable(host, port)) return port;
  }
  throw new Error(`No available local port found near ${preferred}`);
}

async function waitForHttp(url: string, timeoutMs: number, startupError?: () => Error | undefined): Promise<void> {
  const startedAt = Date.now();
  let lastError: unknown;
  while (Date.now() - startedAt < timeoutMs) {
    const failure = startupError?.();
    if (failure) throw failure;
    try {
      const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(2000) });
      if (response.ok) return;
      lastError = new Error(`${url} returned ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(
    `Timed out waiting for ${url}: ${
      lastError instanceof Error ? lastError.message : String(lastError)
    }`,
  );
}

function pipeUtilityLogs(child: UtilityProcess, name: string): void {
  child.stdout?.on("data", (chunk) => log.info(`[${name}] ${String(chunk).trimEnd()}`));
  child.stderr?.on("data", (chunk) => log.error(`[${name}] ${String(chunk).trimEnd()}`));
  child.on("exit", (code) => log.info(`${name} exited with code ${code}`));
}

function startNextRuntime(webRoot: string, port: number): StartedProcess {
  const serverEntry = path.join(webRoot, "server.js");
  if (!existsSync(serverEntry)) {
    throw new Error(`Packaged Next.js server not found: ${serverEntry}`);
  }
  const bootstrapEntry = path.join(__dirname, "web-runtime.js");
  const child = utilityProcess.fork(bootstrapEntry, [], {
    cwd: webRoot,
    env: {
      ...process.env,
      NODE_ENV: "production",
      HOSTNAME: desktopConfig.apiHost,
      NODE_PATH: path.join(webRoot, "runtime_modules"),
      PORT: String(port),
      SAG_WEB_ROOT: webRoot,
    },
    stdio: "pipe",
    serviceName: "ChemClaw Knowledge Graph Web Runtime",
  });
  pipeUtilityLogs(child, "web");
  let failure: Error | undefined;
  child.once("exit", (code) => { failure = new Error(`前端进程提前退出 (exit code ${code})`); });
  return {
    startupError: () => failure,
    stop: () => {
      child.kill();
    },
  };
}

function pipeChildLogs(
  child: ChildProcessByStdio<null, Readable, Readable>,
  name: string,
): void {
  child.stdout.on("data", (chunk) => log.info(`[${name}] ${String(chunk).trimEnd()}`));
  child.stderr.on("data", (chunk) => log.error(`[${name}] ${String(chunk).trimEnd()}`));
  child.on("exit", (code, signal) => {
    log.info(`${name} exited`, { code, signal });
  });
}

function backendExecutable(resourcesPath: string): string {
  const filename = process.platform === "win32" ? "sag-api.exe" : "sag-api";
  return path.join(resourcesPath, "backend", "sag-api", filename);
}

function startPythonRuntime(
  resourcesPath: string,
  userDataDir: string,
  webOrigin: string,
  apiPort: number,
): StartedProcess {
  const executable = backendExecutable(resourcesPath);
  if (!existsSync(executable)) {
    throw new Error(`Packaged Python backend not found: ${executable}`);
  }
  const secretKey = loadOrCreateSecret(userDataDir);
  const child = spawn(executable, [], {
    cwd: userDataDir,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      PYTHONDONTWRITEBYTECODE: "1",
      SAG_ENVIRONMENT: "prod",
      SAG_DEBUG: "false",
      SAG_SECRET_KEY: secretKey,
      SAG_CORS_ORIGINS: webOrigin,
      SAG_DATABASE_URL: "sqlite+aiosqlite:///./.data/sag.db",
      SAG_DATA_DIR: "./.data/engine",
      SAG_UPLOAD_DIR: "./.data/uploads",
      SAG_SAG_VECTOR_PROVIDER: "lancedb",
      SAG_DESKTOP_HOST: desktopConfig.apiHost,
      SAG_DESKTOP_PORT: String(apiPort),
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  pipeChildLogs(child, "api");
  let failure: Error | undefined;
  child.once("error", (error) => {
    log.error("API process could not start", error);
    const code = "code" in error && typeof error.code === "string" ? error.code : "spawn error";
    failure = new Error(`内置后端无法启动 (${code})`);
  });
  child.once("exit", (code, signal) => {
    failure = new Error(`内置后端提前退出 (exit code ${code ?? "unknown"}, signal ${signal ?? "none"})`);
  });
  return {
    startupError: () => failure,
    stop: () => {
      child.kill();
    },
  };
}

export async function startPackagedRuntime(): Promise<ManagedRuntime> {
  const host = desktopConfig.apiHost;
  const webPort = await findAvailablePort(host, desktopConfig.preferredWebPort);
  const apiPort = await resolveApiPort(host, desktopConfig.apiPort, webPort);
  log.info(`Resolved API port ${apiPort}`);
  log.info(`Resolved Web port ${webPort}`);
  const webHealthUrl = `http://${host}:${webPort}`;
  // Next.js standalone normalizes redirects to localhost. Use that as the UI
  // origin while keeping the actual listener restricted to 127.0.0.1.
  const webUrl = `http://localhost:${webPort}`;
  const apiUrl = `http://${host}:${apiPort}`;
  const webRoot = path.join(process.resourcesPath, "web");
  const userDataDir = app.getPath("userData");

  const processes: StartedProcess[] = [];
  try {
    const web = startNextRuntime(webRoot, webPort);
    processes.push(web);
    const api = startPythonRuntime(process.resourcesPath, userDataDir, webUrl, apiPort);
    processes.push(api);
    const startupError = () => api.startupError() ?? web.startupError();
    await Promise.all([
      waitForHttp(webHealthUrl, desktopConfig.startupTimeoutMs, startupError),
      waitForHttp(
        `${apiUrl}/api/v1/system/ready`,
        desktopConfig.startupTimeoutMs,
        startupError,
      ),
    ]);
    log.info("Desktop runtime ready");
  } catch (error) {
    for (const processHandle of [...processes].reverse()) processHandle.stop();
    throw error;
  }

  return {
    webUrl,
    apiUrl,
    stop: () => {
      for (const processHandle of [...processes].reverse()) processHandle.stop();
    },
  };
}

export async function waitForDevelopmentRuntime(webUrl: string): Promise<ManagedRuntime> {
  await waitForHttp(webUrl, desktopConfig.startupTimeoutMs);
  return {
    webUrl,
    apiUrl: `http://${desktopConfig.apiHost}:${desktopConfig.apiPort}`,
    stop: () => {},
  };
}
