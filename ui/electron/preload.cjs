'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('racenet', {
  onRows:  (cb) => ipcRenderer.on('rows-update', (_e, data) => cb(data)),
  offRows: (cb) => ipcRenderer.removeListener('rows-update', (_e, data) => cb(data)),
});
