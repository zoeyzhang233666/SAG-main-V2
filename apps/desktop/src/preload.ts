import { contextBridge, ipcRenderer } from "electron";

import { DESKTOP_CHANNELS, type UpdateState } from "./channels";

export interface SagDesktopBridge {
  readonly isDesktop: true;
  readonly platform: NodeJS.Platform;
  readonly apiBase?: string;
  appInfo(): Promise<{ version: string; platform: NodeJS.Platform; arch: string }>;
  checkForUpdates(): Promise<{ supported: boolean }>;
  onUpdateState(listener: (state: UpdateState) => void): () => void;
}

const port = Number(process.argv.find(value => /^--sag-api-port=\d+$/.test(value))?.split("=")[1]);

const bridge: SagDesktopBridge = Object.freeze({
  isDesktop: true,
  platform: process.platform,
  apiBase: Number.isInteger(port) && port > 0 && port <= 65535
    ? `http://127.0.0.1:${port}` : undefined,
  appInfo: () => ipcRenderer.invoke(DESKTOP_CHANNELS.appInfo),
  checkForUpdates: () => ipcRenderer.invoke(DESKTOP_CHANNELS.checkForUpdates),
  onUpdateState: (listener: (state: UpdateState) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, state: UpdateState) => {
      listener(state);
    };
    ipcRenderer.on(DESKTOP_CHANNELS.updateState, handler);
    return () => ipcRenderer.removeListener(DESKTOP_CHANNELS.updateState, handler);
  },
});

contextBridge.exposeInMainWorld("sagDesktop", bridge);
